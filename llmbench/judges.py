"""Панель судей: {Claude, GPT, GLM, (опц.) Gemini}. Оценивают ТОЛЬКО мягкое (quality по
рубрике, russian) — числа и тулы судьям не отдаём (они в коде, scoring.py).

Нейтральность: первичный субъективный балл — среднее судей, чей вендор ∉ кандидатам. Каждый
судья — свой клиент из фиксированных кредов (изоляция). Доступность судьи — по наличию ключа.

Детерминизм: temperature=0 везде, где провайдер это позволяет (GPT non-reasoning, GLM,
Gemini). Claude-судья на Opus 4.8 сэмплинг-параметры не принимает (400) — не отправляем.
Шкала оценок 0–5 — та же, что у кодовых метрик (пол = 0, а не 1)."""
from __future__ import annotations

import asyncio
import os
import statistics

from anthropic import AsyncAnthropic

from llmbench.core import extract_json, retry_call

ZAI_BASE_URL = "https://api.z.ai/api/anthropic"
JUDGE_CLAUDE_MODEL = "claude-opus-4-8"
JUDGE_GLM_MODEL = "glm-4.6"
JUDGE_GPT_MODEL = os.environ.get("BENCH_GPT_JUDGE_MODEL", "gpt-4.1")
JUDGE_GEMINI_MODEL = os.environ.get("BENCH_GEMINI_JUDGE_MODEL", "gemini-2.5-flash")

# Reasoning-модели OpenAI не принимают temperature и требуют запас на внутренние токены.
_OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def available_judges() -> list[dict]:
    out = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append({"name": "Claude", "vendor": "anthropic", "model": JUDGE_CLAUDE_MODEL, "kind": "anthropic"})
    if os.environ.get("OPENAI_API_KEY"):
        out.append({"name": "GPT", "vendor": "openai", "model": JUDGE_GPT_MODEL, "kind": "openai"})
    if os.environ.get("ZAI_API_KEY"):
        out.append({"name": "GLM", "vendor": "zai", "model": JUDGE_GLM_MODEL, "kind": "anthropic-glm"})
    if os.environ.get("GOOGLE_API_KEY"):
        out.append({"name": "Gemini", "vendor": "google", "model": JUDGE_GEMINI_MODEL, "kind": "gemini"})
    return out


def _client_for(judge):
    if judge["kind"] == "openai":
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    if judge["kind"] == "gemini":
        from google import genai
        return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    if judge["kind"] == "anthropic-glm":
        return AsyncAnthropic(api_key=os.environ["ZAI_API_KEY"], base_url=ZAI_BASE_URL)
    return AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _prompt(question, rubric, answer):
    return ("Ты — строгий судья ответов ассистента-аналитика Яндекс Директа. Оцени ТОЛЬКО "
            "качество по рубрике и язык — НЕ проверяй арифметику.\n\n"
            f"Вопрос:\n{question}\n\nРубрика:\n{rubric}\n\nОтвет:\n{answer or '(пустой ответ)'}\n\n"
            "Оцени 0–5 (5 — лучший, 0 — полностью мимо): quality (соответствие рубрике: "
            "интерпретация, корректность выводов, поведение в краевом случае, без выдуманных "
            "причин); russian (естественность и "
            'ясность). Верни СТРОГО JSON без markdown: {"quality":N,"russian":N,"note":"кратко"}')


async def _call(judge, prompt):
    client = _client_for(judge)
    if judge["kind"] == "openai":
        reasoning = judge["model"].startswith(_OPENAI_REASONING_PREFIXES)
        kw = {"model": judge["model"], "messages": [{"role": "user", "content": prompt}],
              # у reasoning-моделей внутренние токены входят в лимит — даём запас
              "max_completion_tokens": 2000 if reasoning else 300}
        if not reasoning:
            kw["temperature"] = 0
        resp = await retry_call(lambda: client.chat.completions.create(**kw))
        return resp.choices[0].message.content or ""
    if judge["kind"] == "gemini":
        resp = await retry_call(lambda: client.aio.models.generate_content(
            model=judge["model"], contents=prompt, config={"temperature": 0}))
        return resp.text or ""
    kw = {"model": judge["model"], "max_tokens": 400,
          "messages": [{"role": "user", "content": prompt}]}
    if judge["kind"] == "anthropic-glm":
        kw["temperature"] = 0  # Opus 4.8 сэмплинг-параметры отвергает — только для GLM
    resp = await retry_call(lambda: client.messages.create(**kw))
    return "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "") == "text")


def _num(v):
    """float в допустимой шкале 0–5; всё остальное (в т.ч. NaN, 45, '—') → None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 5.0 else None


async def _judge_one(judge, question, rubric, answer):
    try:
        data = extract_json(await _call(judge, _prompt(question, rubric, answer))) or {}
    except Exception as e:  # noqa: BLE001
        return {"vendor": judge["vendor"], "quality": None, "russian": None, "note": f"err:{type(e).__name__}"}
    return {"vendor": judge["vendor"], "quality": _num(data.get("quality")),
            "russian": _num(data.get("russian")), "note": str(data.get("note", ""))[:200]}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def _stdev(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.pstdev(xs), 3) if len(xs) >= 2 else None


async def run_panel(question, rubric, answer, candidate_vendors, judges=None):
    panel = judges if judges is not None else available_judges()
    results = await asyncio.gather(*[_judge_one(j, question, rubric, answer) for j in panel])
    by_name = {j["name"]: r for j, r in zip(panel, results)}
    neutral = [j["name"] for j in panel if j["vendor"] not in candidate_vendors]
    use = neutral or [j["name"] for j in panel]
    return {
        "judges": by_name, "neutral_names": neutral,
        "primary": {"quality": _mean([by_name[n]["quality"] for n in use]),
                    "russian": _mean([by_name[n]["russian"] for n in use]),
                    "neutral_available": bool(neutral)},
        "panel_stddev": {"quality": _stdev([r["quality"] for r in results])},
    }
