"""Самодостаточные агентные loop'ы (замена askads app.engine.claude.run_chat).

- run_anthropic: Claude натив + GLM через Anthropic-совместимый base_url (z.ai). Стриминг,
  thinking/effort, cache_control, обрезка, маршрутизация тулов (+ префикс metrika_).
- run_openai: GPT через chat.completions + function-tools (reasoning_effort для gpt-5).

Оба принимают режим сессии ('live' | 'fixed') и платформу — MCP-слой решает, спавнить
реальный сервер или фикстуры. Возвращают done-подобный dict:
{answer, tool_trace, input_tokens, cache_read_tokens, cache_write_tokens, tokens_out, error}.
"""
from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack

from anthropic import APIError, AsyncAnthropic, RateLimitError

from llmbench.core import (MAX_OUTPUT_TOKENS, MAX_TOOL_ITERATIONS, METRIKA_PREFIX,
                           METRIKA_READ_ONLY_TOOLS, PLATFORM_YANDEX_DIRECT, PLATFORM_YANDEX_METRIKA,
                           READ_ONLY_TOOLS, SYSTEM_PROMPT, TOOL_RESULT_CHAR_BUDGET,
                           TURN_TOOL_RESULTS_CHAR_BUDGET, TURN_TOOL_RESULT_FLOOR_CHARS,
                           clamp_tool_result, clamp_turn_results, is_allowed, retry_call,
                           to_anthropic_tools, to_metrika_anthropic_tools, to_openai_tools)
from llmbench.mcp import open_session

_EMPTY_USAGE = {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "tokens_out": 0}


def _sys(system_prompt, nonce):
    return system_prompt + (f"\n\n[bench:{nonce}]" if nonce else "")


async def _assemble(stack, mode, platform, metrika_enabled, conv):
    """Открывает сессию(и), собирает tools + карту маршрутов {имя: (session, real, is_primary)}."""
    session = await stack.enter_async_context(open_session(mode, platform))
    direct = conv(await session.list_tools())
    tools = list(direct)
    routes = {t["name"] if isinstance(t, dict) and "name" in t else t["function"]["name"]:
              (session, _real(t), True) for t in direct}
    if metrika_enabled and platform == PLATFORM_YANDEX_DIRECT:
        try:
            m = await stack.enter_async_context(open_session(mode, PLATFORM_YANDEX_METRIKA))
            m_tools = (to_metrika_anthropic_tools if conv is _conv_anthropic else _conv_openai_metrika)(
                (await m.list_tools()).tools)
            tools += m_tools
            for t in m_tools:
                name = t["name"] if "name" in t else t["function"]["name"]
                routes[name] = (m, name[len(METRIKA_PREFIX):], False)
        except Exception:  # noqa: BLE001 — Метрика не обязательна
            pass
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
    result = await session.call_tool(real, args or {})
    content = "\n".join(getattr(b, "text", "") or "" for b in result.content)
    is_err = bool(result.isError)
    if not is_err:
        content = clamp_tool_result(content, name=real, budget_chars=TOOL_RESULT_CHAR_BUDGET)
    return content, is_err


# ============================ ANTHROPIC (Claude / GLM) ============================
async def run_anthropic(history, *, model, base_url="", api_key, thinking=None, effort=None,
                        mode="fixed", platform=PLATFORM_YANDEX_DIRECT, metrika_enabled=False,
                        system_prompt=SYSTEM_PROMPT, cache_nonce="", max_tokens=MAX_OUTPUT_TOKENS) -> dict:
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = AsyncAnthropic(**kwargs)
    usage = dict(_EMPTY_USAGE)
    trace, answer = [], ""
    system = [{"type": "text", "text": _sys(system_prompt, cache_nonce), "cache_control": {"type": "ephemeral"}}]
    messages = list(history)
    try:
        async with AsyncExitStack() as stack:
            tools, routes = await _assemble(stack, mode, platform, metrika_enabled, _conv_anthropic)
            completed = False
            for _ in range(MAX_TOOL_ITERATIONS):
                params = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
                if tools:
                    params["tools"] = tools
                if thinking == "adaptive":
                    params["thinking"] = {"type": "adaptive"}
                if effort and effort != "none":
                    params["output_config"] = {"effort": effort}
                async with client.messages.stream(**params) as stream:
                    async for _t in stream.text_stream:
                        pass
                    final = await stream.get_final_message()
                u = final.usage
                usage["input_tokens"] += u.input_tokens or 0
                usage["tokens_out"] += u.output_tokens or 0
                usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
                usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
                messages.append({"role": "assistant", "content": final.content})
                if final.stop_reason != "tool_use":
                    answer = "".join(b.text for b in final.content if b.type == "text")
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
                params = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
                async with client.messages.stream(**params) as stream:
                    async for _t in stream.text_stream:
                        pass
                    final = await stream.get_final_message()
                answer = "".join(b.text for b in final.content if b.type == "text")
                usage["tokens_out"] += final.usage.output_tokens or 0
                usage["input_tokens"] += final.usage.input_tokens or 0
    except (RateLimitError, APIError) as e:
        return {"answer": answer, "tool_trace": trace, **usage, "error": f"{type(e).__name__}: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"answer": answer, "tool_trace": trace, **usage, "error": f"{type(e).__name__}: {e}"}
    return {"answer": answer, "tool_trace": trace, **usage, "error": None}


# ============================ OPENAI (GPT) ============================
async def run_openai(history, *, model, mode="fixed", platform=PLATFORM_YANDEX_DIRECT,
                     metrika_enabled=False, reasoning_effort=None, system_prompt=SYSTEM_PROMPT,
                     cache_nonce="", **_ignore) -> dict:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    usage = dict(_EMPTY_USAGE)
    trace, answer = [], ""
    try:
        async with AsyncExitStack() as stack:
            tools, routes = await _assemble(stack, mode, platform, metrika_enabled, _conv_openai)
            messages = [{"role": "system", "content": _sys(system_prompt, cache_nonce)}] + list(history)

            async def _create(with_tools):
                kw = {"model": model, "messages": messages}
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
                msg = resp.choices[0].message
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
                answer = resp.choices[0].message.content or ""
    except Exception as e:  # noqa: BLE001
        return {"answer": answer, "tool_trace": trace, **usage, "error": f"{type(e).__name__}: {e}"}
    return {"answer": answer, "tool_trace": trace, **usage, "error": None}


def _acc_openai(usage, u):
    if not u:
        return
    prompt = getattr(u, "prompt_tokens", 0) or 0
    details = getattr(u, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    usage["input_tokens"] += max(0, prompt - cached)
    usage["cache_read_tokens"] += cached
    usage["tokens_out"] += getattr(u, "completion_tokens", 0) or 0
