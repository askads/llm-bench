"""Панель судей: {Claude, GPT, GLM, (опц.) Gemini}. Оценивают ТОЛЬКО мягкое (quality по
рубрике, russian) — числа и тулы судьям не отдаём (они в коде, scoring.py).

Нейтральность: первичный субъективный балл — среднее судей, чей вендор ∉ кандидатам. Каждый
судья — свой клиент из фиксированных кредов (изоляция). Доступность судьи — по наличию ключа.
"""
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
            "Оцени 1–5 (5 — лучший): quality (соответствие рубрике: интерпретация, корректность "
            "выводов, поведение в краевом случае, без выдуманных причин); russian (естественность и "
            'ясность). Верни СТРОГО JSON без markdown: {"quality":N,"russian":N,"note":"кратко"}')


async def _call(judge, prompt):
    client = _client_for(judge)
    if judge["kind"] == "openai":
        resp = await retry_call(lambda: client.chat.completions.create(
            model=judge["model"], max_tokens=300, messages=[{"role": "user", "content": prompt}]))
        return resp.choices[0].message.content or ""
    if judge["kind"] == "gemini":
        resp = await retry_call(lambda: client.aio.models.generate_content(model=judge["model"], contents=prompt))
        return resp.text or ""
    resp = await retry_call(lambda: client.messages.create(
        model=judge["model"], max_tokens=400, messages=[{"role": "user", "content": prompt}]))
    return "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "") == "text")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
