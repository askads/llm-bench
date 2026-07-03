"""Самодостаточные агентные loop'ы (замена askads app.engine.claude.run_chat).

- run_anthropic: Claude натив + GLM через Anthropic-совместимый base_url (z.ai). Стриминг,
  thinking/effort, cache_control, обрезка, маршрутизация тулов (+ префикс metrika_),
  ретраи на транзиентных ошибках (429/529/5xx).
- run_openai: GPT через chat.completions + function-tools (reasoning_effort для gpt-5).

Оба принимают режим сессии ('live' | 'fixed') и платформу — MCP-слой решает, спавнить
реальный сервер или фикстуры. Возвращают done-подобный dict:
{answer, tool_trace, input_tokens, cache_read_tokens, cache_write_tokens, tokens_out, error}.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack

from anthropic import APIError, AsyncAnthropic, RateLimitError

from llmbench.core import (MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS_THINKING, MAX_TOOL_ITERATIONS,
                           MCP_INIT_TIMEOUT_S, MCP_TOOL_CALL_TIMEOUT_S, METRIKA_PREFIX,
                           METRIKA_READ_ONLY_TOOLS, PLATFORM_YANDEX_DIRECT, PLATFORM_YANDEX_METRIKA,
                           SYSTEM_PROMPT, TOOL_RESULT_CHAR_BUDGET, TURN_TOOL_RESULTS_CHAR_BUDGET,
                           TURN_TOOL_RESULT_FLOOR_CHARS, clamp_tool_result, clamp_turn_results,
                           is_allowed, retry_call, to_anthropic_tools, to_metrika_anthropic_tools,
                           to_openai_tools)
from llmbench.mcp import open_session

_EMPTY_USAGE = {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "tokens_out": 0}


def _sys(system_prompt, nonce):
    return system_prompt + (f"\n\n[bench:{nonce}]" if nonce else "")


async def _assemble(stack, mode, platform, metrika_enabled, conv):
    """Открывает сессию(и), собирает tools + карту маршрутов {имя: (session, real, is_primary)}.
    Инициализация — под таймаутом: зависший node-сервер не должен вешать грид."""
    session = await asyncio.wait_for(
        stack.enter_async_context(open_session(mode, platform)), MCP_INIT_TIMEOUT_S)
    direct = conv(await asyncio.wait_for(session.list_tools(), MCP_INIT_TIMEOUT_S))
    tools = list(direct)
    routes = {t["name"] if isinstance(t, dict) and "name" in t else t["function"]["name"]:
              (session, _real(t), True) for t in direct}
    if metrika_enabled and platform == PLATFORM_YANDEX_DIRECT:
        try:
            m = await asyncio.wait_for(
                stack.enter_async_context(open_session(mode, PLATFORM_YANDEX_METRIKA)), MCP_INIT_TIMEOUT_S)
            m_tools = (to_metrika_anthropic_tools if conv is _conv_anthropic else _conv_openai_metrika)(
                (await asyncio.wait_for(m.list_tools(), MCP_INIT_TIMEOUT_S)).tools)
            tools += m_tools
            for t in m_tools:
                name = t["name"] if "name" in t else t["function"]["name"]
                routes[name] = (m, name[len(METRIKA_PREFIX):], False)
        except Exception as e:  # noqa: BLE001 — Метрика не обязательна для не-metrika кейсов
            print(f"[warn] Метрика недоступна ({type(e).__name__}: {e}) — кейс пойдёт без "
                  f"metrika_* тулов; metrika-кейсы провалятся из-за харнесса, не модели",
                  file=sys.stderr)
    return tools, routes


def _real(t):
    name = t["name"] if isinstance(t, dict) and "name" in t else t["function"]["name"]
    return name[len(METRIKA_PREFIX):] if name.startswith(METRIKA_PREFIX) else name


def _conv_anthropic(list_tools_result):
    return to_anthropic_tools(list_tools_result.tools)


def _conv_openai(list_tools_result):
    return to_openai_tools(list_tools_result.tools)


def _conv_openai_metrika(mcp_tools):
    return to_openai_tools(mcp_tools, METRIKA_READ_ONLY_TOOLS, METRIKA_PREFIX)


async def _exec_tool(routes, name, args):
    route = routes.get(name)
    if route is None or not is_allowed(route[1]):
        return f"Инструмент '{name}' недоступен: только чтение.", True
    session, real, _ = route
    try:
        result = await asyncio.wait_for(session.call_tool(real, args or {}), MCP_TOOL_CALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        return f"Инструмент '{real}' не ответил за {MCP_TOOL_CALL_TIMEOUT_S}с.", True
    content = "\n".join(getattr(b, "text", "") or "" for b in result.content)
    is_err = bool(result.isError)
    # Клампим и ошибочные результаты: огромный error-payload из live-MCP не должен
    # раздувать контекст и стоимость.
    content = clamp_tool_result(content, name=real, budget_chars=TOOL_RESULT_CHAR_BUDGET)
    return content, is_err


# ============================ ANTHROPIC (Claude / GLM) ============================
async def _stream_final(client, params):
    async with client.messages.stream(**params) as stream:
        async for _t in stream.text_stream:
            pass
        return await stream.get_final_message()


def _acc_anthropic(usage, u):
    usage["input_tokens"] += u.input_tokens or 0
    usage["tokens_out"] += u.output_tokens or 0
    usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
    usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0


def _text_of(final):
    return "".join(b.text for b in final.content if b.type == "text")


async def run_anthropic(history, *, model, base_url="", api_key, thinking=None, effort=None,
                        mode="fixed", platform=PLATFORM_YANDEX_DIRECT, metrika_enabled=False,
                        system_prompt=SYSTEM_PROMPT, cache_nonce="", max_tokens=None) -> dict:
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = AsyncAnthropic(**kwargs)
    if max_tokens is None:
        # Токены adaptive thinking входят в max_tokens — thinking-вариантам нужен запас,
        # иначе ответ обрезается и вариант получает незаслуженный ноль.
        max_tokens = MAX_OUTPUT_TOKENS_THINKING if thinking == "adaptive" else MAX_OUTPUT_TOKENS
    usage = dict(_EMPTY_USAGE)
    trace, answer = [], ""
    system = [{"type": "text", "text": _sys(system_prompt, cache_nonce), "cache_control": {"type": "ephemeral"}}]
    messages = list(history)

    def _params(allow_tools, tools):
        params = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            params["tools"] = tools  # история содержит tool_use-блоки — tools обязателен
            if not allow_tools:
                params["tool_choice"] = {"type": "none"}
        if thinking == "adaptive":
            params["thinking"] = {"type": "adaptive"}
        if effort and effort != "none":
            params["output_config"] = {"effort": effort}
        return params

    def _done(err=None):
        return {"answer": answer, "tool_trace": trace, **usage, "error": err}

    try:
        async with AsyncExitStack() as stack:
            tools, routes = await _assemble(stack, mode, platform, metrika_enabled, _conv_anthropic)
            completed = False
            for _ in range(MAX_TOOL_ITERATIONS):
                final = await retry_call(lambda: _stream_final(client, _params(True, tools)))
                _acc_anthropic(usage, final.usage)
                messages.append({"role": "assistant", "content": final.content})
                if final.stop_reason == "max_tokens":
                    answer = _text_of(final)
                    return _done(f"max_tokens: ответ обрезан (лимит {max_tokens} токенов)")
                if final.stop_reason != "tool_use":
                    answer = _text_of(final)
                    completed = True
                    break
                results = []
                for block in final.content:
                    if block.type != "tool_use":
                        continue
                    content, is_err = await _exec_tool(routes, block.name, block.input)
                    trace.append({"name": block.name, "input": block.input, "is_error": is_err})
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": content, "is_error": is_err})
                results = clamp_turn_results(results, budget_chars=TURN_TOOL_RESULTS_CHAR_BUDGET,
                                             floor_chars=TURN_TOOL_RESULT_FLOOR_CHARS)
                messages.append({"role": "user", "content": results})
            if not completed:
                # Бюджет итераций исчерпан — финальный вызов с теми же tools/thinking
                # (история содержит tool_use/tool_result — без tools API вернёт 400),
                # но с запретом новых вызовов через tool_choice none.
                final = await retry_call(lambda: _stream_final(client, _params(False, tools)))
                _acc_anthropic(usage, final.usage)
                answer = _text_of(final)
                if final.stop_reason == "max_tokens":
                    return _done(f"max_tokens: ответ обрезан (лимит {max_tokens} токенов)")
    except (RateLimitError, APIError) as e:
        return _done(f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        return _done(f"{type(e).__name__}: {e}")
    return _done(None)


# ============================ OPENAI (GPT) ============================
async def run_openai(history, *, model, mode="fixed", platform=PLATFORM_YANDEX_DIRECT,
                     metrika_enabled=False, reasoning_effort=None, system_prompt=SYSTEM_PROMPT,
                     cache_nonce="", max_tokens=None, **_ignore) -> dict:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    if max_tokens is None:
        # У reasoning-моделей внутренние токены тоже входят в лимит — запас как у thinking.
        max_tokens = MAX_OUTPUT_TOKENS_THINKING if reasoning_effort else MAX_OUTPUT_TOKENS
    usage = dict(_EMPTY_USAGE)
    trace, answer = [], ""

    def _done(err=None):
        return {"answer": answer, "tool_trace": trace, **usage, "error": err}

    try:
        async with AsyncExitStack() as stack:
            tools, routes = await _assemble(stack, mode, platform, metrika_enabled, _conv_openai)
            messages = [{"role": "system", "content": _sys(system_prompt, cache_nonce)}] + list(history)

            async def _create(with_tools):
                kw = {"model": model, "messages": messages, "max_completion_tokens": max_tokens}
                if with_tools:
                    kw["tools"] = tools
                    kw["tool_choice"] = "auto"
                if reasoning_effort:
                    kw["reasoning_effort"] = reasoning_effort
                return await retry_call(lambda: client.chat.completions.create(**kw))

            completed = False
            for _ in range(MAX_TOOL_ITERATIONS):
                resp = await _create(True)
                _acc_openai(usage, resp.usage)
                choice = resp.choices[0]
                msg = choice.message
                if choice.finish_reason == "length":
                    answer = msg.content or ""
                    return _done(f"length: ответ обрезан (лимит {max_tokens} токенов)")
                if not msg.tool_calls:
                    answer = msg.content or ""
                    completed = True
                    break
                messages.append({"role": "assistant", "content": msg.content,
                                 "tool_calls": [{"id": tc.id, "type": "function",
                                                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                                for tc in msg.tool_calls]})
                pending = []
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    content, is_err = await _exec_tool(routes, tc.function.name, args)
                    trace.append({"name": tc.function.name, "input": args, "is_error": is_err})
                    pending.append({"type": "tool_result", "tool_use_id": tc.id, "content": content, "is_error": is_err})
                pending = clamp_turn_results(pending, budget_chars=TURN_TOOL_RESULTS_CHAR_BUDGET,
                                             floor_chars=TURN_TOOL_RESULT_FLOOR_CHARS)
                for p in pending:
                    messages.append({"role": "tool", "tool_call_id": p["tool_use_id"], "content": p["content"]})
            if not completed:
                resp = await _create(False)
                _acc_openai(usage, resp.usage)
                choice = resp.choices[0]
                answer = choice.message.content or ""
                if choice.finish_reason == "length":
                    return _done(f"length: ответ обрезан (лимит {max_tokens} токенов)")
    except Exception as e:  # noqa: BLE001
        return _done(f"{type(e).__name__}: {e}")
    return _done(None)


def _acc_openai(usage, u):
    if not u:
        return
    prompt = getattr(u, "prompt_tokens", 0) or 0
    details = getattr(u, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    usage["input_tokens"] += max(0, prompt - cached)
    usage["cache_read_tokens"] += cached
    usage["tokens_out"] += getattr(u, "completion_tokens", 0) or 0
