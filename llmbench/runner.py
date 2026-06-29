"""Ранер: сетка вариантов (модель × thinking/effort/reasoning) × кейсы × repeat.

Режимы MCP: --mode fixed (фикстуры, детерминированно, CI) | live (реальные MCP-серверы +
токены из env). Движок и MCP развязаны от askads. Вердикт — каждый вариант vs baseline.

  # детерминированный model-бенч (нужны ключи моделей):
  python -m llmbench.runner --mode fixed --repeat 2
  # против РЕАЛЬНЫХ тулов (нужны npm-серверы + токены кабинета):
  python -m llmbench.runner --mode live --variants "GLM-4.6 disabled" --judges off
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

from llmbench import scoring
from llmbench.cases import CASES
from llmbench.engines import run_anthropic, run_openai
from llmbench.fixtures import FIXTURE_VERSION

ZAI = "https://api.z.ai/api/anthropic"


def _v(label, vendor, engine, model, **kw):
    return {"label": label, "vendor": vendor, "engine": engine, "model": model,
            "base_url": kw.get("base_url", ""), "key_env": kw["key_env"],
            "thinking": kw.get("thinking"), "effort": kw.get("effort"),
            "reasoning_effort": kw.get("reasoning_effort"), "is_baseline": kw.get("is_baseline", False)}


VARIANTS = [
    _v("Sonnet disabled/high", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="disabled", effort="high", is_baseline=True),
    _v("Sonnet disabled/low", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="disabled", effort="low"),
    _v("Sonnet adaptive/low", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="low"),
    _v("Sonnet adaptive/medium", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="medium"),
    _v("Sonnet adaptive/high", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="high"),
    _v("Opus disabled/high", "anthropic", "anthropic", "claude-opus-4-8", key_env="ANTHROPIC_API_KEY", thinking="disabled", effort="high"),
    _v("Opus adaptive/high", "anthropic", "anthropic", "claude-opus-4-8", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="high"),
    _v("Opus adaptive/max", "anthropic", "anthropic", "claude-opus-4-8", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="max"),
    _v("GLM-4.6 disabled", "zai", "anthropic", "glm-4.6", base_url=ZAI, key_env="ZAI_API_KEY", thinking="disabled", effort="high"),
    _v("GLM-4.6 thinking", "zai", "anthropic", "glm-4.6", base_url=ZAI, key_env="ZAI_API_KEY", thinking="adaptive", effort="high"),
    _v("GLM-5 disabled", "zai", "anthropic", "glm-5", base_url=ZAI, key_env="ZAI_API_KEY", thinking="disabled", effort="high"),
    _v("GLM-5 thinking", "zai", "anthropic", "glm-5", base_url=ZAI, key_env="ZAI_API_KEY", thinking="adaptive", effort="high"),
    _v("GPT-5 reasoning low", "openai", "openai", "gpt-5", key_env="OPENAI_API_KEY", reasoning_effort="low"),
    _v("GPT-5 reasoning medium", "openai", "openai", "gpt-5", key_env="OPENAI_API_KEY", reasoning_effort="medium"),
    _v("GPT-5 reasoning high", "openai", "openai", "gpt-5", key_env="OPENAI_API_KEY", reasoning_effort="high"),
    _v("GPT-4.1", "openai", "openai", "gpt-4.1", key_env="OPENAI_API_KEY"),
]

_USAGE = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "tokens_out")


async def _run_case(variant, case, nonce, mode, safe=False):
    thinking = "disabled" if safe else variant.get("thinking")
    effort = "high" if safe else variant.get("effort")
    reasoning = None if safe else variant.get("reasoning_effort")
    history, trace, answer, err = [], [], "", None
    usage = {k: 0 for k in _USAGE}
    for user_msg in case.turns:
        history.append({"role": "user", "content": user_msg})
        if variant["engine"] == "openai":
            done = await run_openai(history, model=variant["model"], mode=mode, platform=case.platform,
                                    metrika_enabled=case.metrika_enabled, reasoning_effort=reasoning, cache_nonce=nonce)
        else:
            done = await run_anthropic(history, model=variant["model"], base_url=variant["base_url"],
                                       api_key=os.environ.get(variant["key_env"], ""), thinking=thinking,
                                       effort=effort, mode=mode, platform=case.platform,
                                       metrika_enabled=case.metrika_enabled, cache_nonce=nonce)
        if done.get("error"):
            err = done["error"]
            break
        answer = done.get("answer", "") or ""
        trace += done.get("tool_trace", []) or []
        for k in usage:
            usage[k] += done.get(k, 0) or 0
        history.append({"role": "assistant", "content": answer})
    return {"answer": answer, "tool_trace": trace, "usage": usage, "error": err}


async def _run_case_fb(variant, case, nonce, mode):
    res = await _run_case(variant, case, nonce, mode)
    if res["error"] and (variant.get("thinking") not in (None, "disabled")
                         or variant.get("reasoning_effort") or variant.get("effort") not in (None, "high")):
        res2 = await _run_case(variant, case, nonce, mode, safe=True)
        if not res2["error"]:
            return res2, True
    return res, False


def _composite(rec):
    parts = [rec["tool"]]
    if rec["has_golden"]:
        parts.append(rec["numeric"])
    if rec["soft_quality"] is not None:
        parts.append(rec["soft_quality"])
    return round(sum(parts) / len(parts), 3) if parts else None


async def _score(case, variant, rr, candidate_vendors, use_judges, judges):
    tool = scoring.score_tooluse(rr["tool_trace"], case.trace)
    numeric = scoring.score_numeric(rr["answer"], case.golden_facts)
    soft_q = soft_r = None
    if use_judges and not tool["fail_fast"] and not rr.get("error"):
        from llmbench.judges import run_panel
        panel = await run_panel("\n".join(case.turns), case.rubric, rr["answer"], candidate_vendors, judges)
        soft_q, soft_r = panel["primary"]["quality"], panel["primary"]["russian"]
    rec = {"case": case.id, "dimension": case.dimension, "turn_type": case.turn_type,
           "tool": tool["score"], "tool_failfast": tool["fail_fast"], "numeric": numeric["score"],
           "has_golden": bool(case.golden_facts), "soft_quality": soft_q, "soft_russian": soft_r,
           "cost": scoring.cost_from_done(variant["model"], rr["usage"]), "error": rr.get("error")}
    rec["composite"] = _composite(rec)
    return rec


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def _agg(records):
    num = [r for r in records if r["has_golden"]]
    edge = [r for r in records if r["dimension"] == "edge" and r["soft_quality"] is not None]
    comps = [r["composite"] for r in records if r["composite"] is not None]

    def spd(tt):
        rs = [r for r in records if r["turn_type"] == tt]
        c, cost = _mean([r["composite"] for r in rs]), _mean([r["cost"] for r in rs])
        return round(c / cost, 1) if (c and cost) else None

    return {"numeric": _mean([r["numeric"] for r in num]), "tool": _mean([r["tool"] for r in records]),
            "edge": _mean([r["soft_quality"] for r in edge]), "russian": _mean([r["soft_russian"] for r in records]),
            "composite": _mean(comps), "cost_avg": _mean([r["cost"] for r in records]),
            "score_per_dollar": {"single": spd("single"), "multi": spd("multi")},
            "stddev_composite": round(statistics.pstdev(comps), 3) if len(comps) >= 2 else None,
            "errors": sum(1 for r in records if r["error"]), "n_runs": len(records)}


def _pareto(agg):
    items = [(k, a["composite"], a["cost_avg"]) for k, a in agg.items() if a["composite"] is not None and a["cost_avg"]]
    return [k for k, c, cost in items
            if not any(c2 >= c and cost2 <= cost and (c2, cost2) != (c, cost) for _, c2, cost2 in items)]


def _f(x):
    return "—" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


MODEL_DISPLAY = {
    "claude-sonnet-4-6": "Sonnet 4.6", "claude-opus-4-8": "Opus 4.8",
    "glm-4.6": "GLM-4.6", "glm-5": "GLM-5", "gpt-5": "GPT-5", "gpt-4.1": "GPT-4.1",
}


def _describe(v):
    """Вариант → (LLM, Thinking) для отображения. У GLM усилие — не рычаг, не показываем."""
    llm = MODEL_DISPLAY.get(v["model"], v["model"])
    if v.get("reasoning_effort"):
        thinking = f"reasoning · {v['reasoning_effort']}"
    elif v["engine"] == "openai":
        thinking = "—"
    else:
        t = v.get("thinking") or "disabled"
        thinking = t if v["vendor"] == "zai" else f"{t} · {v.get('effort')}"
    return llm, thinking


_GLOSSARY = """## Термины (как читать таблицу)

- **Numeric** (0–5) — точность чисел: верно ли посчитаны CTR/CPC/CPA/расход и не выдуманы ли цифры. **В коде** (детерминированно).
- **Tool** (0–5) — корректность инструментов: вызвал нужные тулы в нужном порядке, без лишних/запрещённых. **Код**.
- **Edge** (0–5) — поведение в краевых случаях (пустой отчёт, отказ менять ставку, уточнение). **LLM-судьи**.
- **Rus** (0–5) — естественность и ясность русского. Судьи.
- **composite** (0–5) — сводный балл строки = среднее доступных измерений.
- **$/прог** — средняя стоимость прогона (USD); **score/$ (s/m)** — «качество на доллар» (composite ÷ цена) для single/multi-turn; выше = выгоднее.
- **σ** — разброс composite между повторами: меньше = стабильнее.
- **Thinking** — режим обдумывания + бюджет усилий: `disabled` (выкл), `adaptive` (Claude/GLM), `reasoning` (GPT-5); `low/medium/high/max`; `—` = без обдумывания.
- **⭐** — **лучший баланс «качество/цена»**: вариант, который нельзя «побить» — нет другого, который и качественнее, и дешевле. _(В оптимизации — «Pareto-фронт».)_
"""


def _build_md(agg, meta):
    o = ["# Бенчмарк моделей на MCP-тулзах askads (fixed-input)\n"]
    o.append(f"_Сгенерировано {meta['ts']} · FIXTURE_VERSION `{FIXTURE_VERSION}` · mode={meta['mode']} · "
             f"repeat={meta['repeat']} · вариантов: {len(meta['variants'])} · кейсов: {meta['n_cases']}_\n")
    o.append("Claude/GLM — Anthropic-движок; GPT — OpenAI-loop. Tool-Use/Numeric — в коде; "
             f"интерпретация/русский — судьи {meta['judges']} (нейтрален: **{meta['neutral'] or '—'}**).\n")
    o.append(_GLOSSARY)
    o.append("## Все варианты (сорт. по composite)\n")
    o.append("| LLM | Thinking | Numeric | Tool | Edge | Rus | $/прог | score/$ s | score/$ m | σ | composite |")
    o.append("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    front = set(_pareto(agg))
    by_label = {v["label"]: v for v in meta["variants"]}
    for label, a in sorted(agg.items(), key=lambda kv: (kv[1]["composite"] is None, -(kv[1]["composite"] or 0))):
        llm, thinking = _describe(by_label[label])
        spd = a["score_per_dollar"]
        o.append(f"| {llm}{' ⭐' if label in front else ''} | {thinking} | {_f(a['numeric'])} | {_f(a['tool'])} | "
                 f"{_f(a['edge'])} | {_f(a['russian'])} | ${a['cost_avg'] or 0:.5f} | {_f(spd['single'])} | "
                 f"{_f(spd['multi'])} | {_f(a['stddev_composite'])} | {_f(a['composite'])} |")
    o.append(f"\n⭐ — **лучший баланс «качество/цена»** (нельзя стать и качественнее, и дешевле одновременно): "
             f"**{', '.join(front) or '—'}**.\n")
    if meta.get("baseline_desc"):
        o.append(f"_Для ориентира: текущий прод askads — {meta['baseline_desc']}._\n")
    o.append("\n## Известные ограничения\n")
    for line in meta["caveats"]:
        o.append(f"- {line}")
    return "\n".join(o)


async def main():
    ap = argparse.ArgumentParser(description="Бенчмарк моделей на MCP-тулзах")
    ap.add_argument("--mode", choices=["fixed", "live"], default="fixed")
    ap.add_argument("--variants", nargs="*", default=None)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--judges", choices=["panel", "neutral", "off"], default="panel")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="results/model-comparison-grid.md")
    args = ap.parse_args()

    variants = [v for v in VARIANTS if not args.variants or any(n.lower() in v["label"].lower() for n in args.variants)] or list(VARIANTS)
    cases = [c for c in CASES if not args.cases or c.id in args.cases] or list(CASES)
    candidate_vendors = {v["vendor"] for v in variants}

    from llmbench.judges import available_judges
    judges_all = available_judges()
    if args.judges == "neutral":
        judges_all = [j for j in judges_all if j["vendor"] not in candidate_vendors]
    neutral = [j["name"] for j in judges_all if j["vendor"] not in candidate_vendors]

    n_runs = len(variants) * len(cases) * args.repeat
    print(f"mode={args.mode} · вариантов={len(variants)} · кейсов={len(cases)} · repeat={args.repeat}")
    print(f"Судьи: {[j['name'] for j in judges_all] or '—'} · нейтрален: {neutral or '—'}")
    print(f"Смета: run ≈ {n_runs} · судейских ≈ {n_runs * (len(judges_all) if args.judges != 'off' else 0)}")
    if args.dry_run:
        print("--dry-run."); return
    if os.environ.get("RUN_BENCH") != "1":
        print("Нужен RUN_BENCH=1."); return

    agg = {}
    for v in variants:
        if v["engine"] == "anthropic" and not os.environ.get(v["key_env"]):
            print(f"  [skip] {v['label']}: нет {v['key_env']}"); continue
        if v["engine"] == "openai" and not os.environ.get("OPENAI_API_KEY"):
            print(f"  [skip] {v['label']}: нет OPENAI_API_KEY"); continue
        recs = []
        for case in cases:
            for ri in range(args.repeat):
                rr, fb = await _run_case_fb(v, case, f"{case.id}:{v['label']}:{ri}", args.mode)
                rec = await _score(case, v, rr, candidate_vendors, args.judges != "off", judges_all)
                recs.append(rec)
                print(f"  {v['label']:<24} {case.id:<20} r{ri} tool={rec['tool']} num={rec['numeric']} "
                      f"soft={rec['soft_quality']} ${rec['cost']:.5f}" + (" FB" if fb else "")
                      + (f" ERR={rec['error'][:40]}" if rec['error'] else ""))
        agg[v["label"]] = _agg(recs)

    baseline = next((v for v in variants if v.get("is_baseline")), None)
    baseline_desc = "{} ({})".format(*_describe(baseline)) if baseline else None

    meta = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "mode": args.mode,
            "repeat": args.repeat, "n_cases": len(cases), "variants": variants,
            "judges": [j["name"] for j in judges_all] or "—", "neutral": neutral,
            "baseline_desc": baseline_desc,
            "caveats": [
                "GPT — отдельный OpenAI-loop (не Anthropic-движок) — tool-use не байт-в-байт с Claude/GLM.",
                "Без нейтрального судьи (если все вендоры — кандидаты) первичный мягкий балл = среднее панели (advisory).",
                "Ставки/кэш-множители gpt/gemini/glm — оценки; сверить с биллингом.",
                "glm-5/gpt-5: доступность ≠ идентичность ожидаемой модели — сверить.",
                "repeat — флаг шума; «неверное число == пропущенное» в numeric — упрощение.",
                "fixed-режим: чистые фикстуры не ловят robustness на грязном API-выводе (для этого --mode live).",
            ]}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_build_md(agg, meta), encoding="utf-8")
    print(f"\nГотово → {out}")


if __name__ == "__main__":
    asyncio.run(main())
