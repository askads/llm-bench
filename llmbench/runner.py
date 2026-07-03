"""Ранер: сетка вариантов (модель × thinking/effort/reasoning) × кейсы × repeat.

Режимы MCP: --mode fixed (фикстуры, детерминированно, CI) | live (реальные MCP-серверы +
токены из env). Движок и MCP развязаны от askads. Каждый прогон пишется в JSONL
(results/runs-<ts>.jsonl): ответы, трейсы, usage, оценки — отчёт пересобирается из него
без повторных трат (`--report-from`).

  # детерминированный model-бенч (нужны ключи моделей):
  python -m llmbench.runner --mode fixed --repeat 2
  # против РЕАЛЬНЫХ тулов (нужны npm-серверы + токены кабинета):
  python -m llmbench.runner --mode live --variants "GLM-4.6 disabled" --judges off
  # пересобрать отчёт из сырых данных (бесплатно):
  python -m llmbench.runner --report-from results/runs-20260703-120000.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from llmbench import report, scoring
from llmbench.cases import CASES
from llmbench.core import MODEL_RATES, PLATFORM_YANDEX_METRIKA
from llmbench.engines import run_anthropic, run_openai
from llmbench.fixtures import FIXTURE_VERSION

ZAI = "https://api.z.ai/api/anthropic"
# Ранер пишет СГЕНЕРИРОВАННЫЙ отчёт, а не курируемые model-comparison-grid.ru/en.md —
# чтобы прогон не затирал ручную сборку (Топ-3, прозу, двуязычие). Курируемые отчёты
# правятся из этого файла вручную.
DEFAULT_OUT = "results/model-comparison-grid.generated.md"


def _v(label, vendor, engine, model, **kw):
    return {"label": label, "vendor": vendor, "engine": engine, "model": model,
            "base_url": kw.get("base_url", ""), "key_env": kw["key_env"],
            "thinking": kw.get("thinking"), "effort": kw.get("effort"),
            "reasoning_effort": kw.get("reasoning_effort"), "is_baseline": kw.get("is_baseline", False)}


# У GLM effort не рычаг (отчёт показывает «—») — и не отправляем его в z.ai.
VARIANTS = [
    _v("Sonnet disabled/high", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="disabled", effort="high", is_baseline=True),
    _v("Sonnet disabled/low", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="disabled", effort="low"),
    _v("Sonnet adaptive/low", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="low"),
    _v("Sonnet adaptive/medium", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="medium"),
    _v("Sonnet adaptive/high", "anthropic", "anthropic", "claude-sonnet-4-6", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="high"),
    _v("Opus disabled/high", "anthropic", "anthropic", "claude-opus-4-8", key_env="ANTHROPIC_API_KEY", thinking="disabled", effort="high"),
    _v("Opus adaptive/high", "anthropic", "anthropic", "claude-opus-4-8", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="high"),
    _v("Opus adaptive/max", "anthropic", "anthropic", "claude-opus-4-8", key_env="ANTHROPIC_API_KEY", thinking="adaptive", effort="max"),
    _v("GLM-4.6 disabled", "zai", "anthropic", "glm-4.6", base_url=ZAI, key_env="ZAI_API_KEY", thinking="disabled"),
    _v("GLM-4.6 thinking", "zai", "anthropic", "glm-4.6", base_url=ZAI, key_env="ZAI_API_KEY", thinking="adaptive"),
    _v("GLM-5 disabled", "zai", "anthropic", "glm-5", base_url=ZAI, key_env="ZAI_API_KEY", thinking="disabled"),
    _v("GLM-5 thinking", "zai", "anthropic", "glm-5", base_url=ZAI, key_env="ZAI_API_KEY", thinking="adaptive"),
    _v("GPT-5 reasoning low", "openai", "openai", "gpt-5", key_env="OPENAI_API_KEY", reasoning_effort="low"),
    _v("GPT-5 reasoning medium", "openai", "openai", "gpt-5", key_env="OPENAI_API_KEY", reasoning_effort="medium"),
    _v("GPT-5 reasoning high", "openai", "openai", "gpt-5", key_env="OPENAI_API_KEY", reasoning_effort="high"),
    _v("GPT-4.1", "openai", "openai", "gpt-4.1", key_env="OPENAI_API_KEY"),
]

_USAGE = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "tokens_out")


def _nonce(case_id, label, ri):
    """Кэш-бастер БЕЗ утечки: говорящий id кейса (refuse_change_bid…) и конфиг варианта
    в системном промпте подсказывали модели суть теста — отдаём только хэш."""
    return hashlib.sha1(f"{case_id}:{label}:{ri}".encode()).hexdigest()[:12]


async def _run_case(variant, case, nonce, mode):
    history, trace, answer, err = [], [], "", None
    usage = {k: 0 for k in _USAGE}
    for user_msg in case.turns:
        history.append({"role": "user", "content": user_msg})
        if variant["engine"] == "openai":
            done = await run_openai(history, model=variant["model"], mode=mode, platform=case.platform,
                                    metrika_enabled=case.metrika_enabled,
                                    reasoning_effort=variant.get("reasoning_effort"), cache_nonce=nonce)
        else:
            done = await run_anthropic(history, model=variant["model"], base_url=variant["base_url"],
                                       api_key=os.environ.get(variant["key_env"], ""),
                                       thinking=variant.get("thinking"), effort=variant.get("effort"),
                                       mode=mode, platform=case.platform,
                                       metrika_enabled=case.metrika_enabled, cache_nonce=nonce)
        for k in usage:
            usage[k] += done.get(k, 0) or 0
        if done.get("error"):
            err = done["error"]
            # частичный ответ упавшего тёрна — в JSONL для отладки; в скоринг не попадёт
            answer = done.get("answer", "") or ""
            break
        answer = done.get("answer", "") or ""
        trace += done.get("tool_trace", []) or []
        history.append({"role": "assistant", "content": answer})
    return {"answer": answer, "tool_trace": trace, "usage": usage, "error": err}


async def _run_case_retried(variant, case, nonce, mode, retries=1):
    """Ошибка → повтор ТЕМ ЖЕ конфигом (транзиентные ретраи уже внутри движка). Никакой
    подмены thinking/effort: результат под лейблом варианта обязан быть получен этим
    конфигом (см. REVIEW.md R1). Стоимость упавших попыток возвращаем отдельно."""
    wasted = 0.0
    res = await _run_case(variant, case, nonce, mode)
    for _ in range(retries):
        if not res["error"]:
            break
        wasted += scoring.cost_from_done(variant["model"], res["usage"])
        res = await _run_case(variant, case, nonce, mode)
    return res, wasted


async def _score(case, variant, rr, candidate_vendors, use_judges, judges, wasted=0.0):
    rec = {"case": case.id, "dimension": case.dimension, "turn_type": case.turn_type,
           "tool": None, "tool_failfast": None, "numeric": None,
           "has_golden": bool(case.golden_facts), "soft_quality": None, "soft_russian": None,
           "cost": scoring.cost_from_done(variant["model"], rr["usage"]),
           # retried = «удался только после повтора»: упавший финал повтором не считаем
           "cost_wasted": round(wasted, 6), "retried": wasted > 0 and not rr.get("error"),
           "error": rr.get("error"), "judges_detail": None}
    if not rr.get("error"):
        tool = scoring.score_tooluse(rr["tool_trace"], case.trace)
        numeric = scoring.score_numeric(rr["answer"], case.golden_facts)
        rec.update({"tool": tool["score"], "tool_failfast": tool["fail_fast"], "numeric": numeric["score"]})
        # Судьи оценивают и прогоны с нарушениями по тулам (fail_fast): иначе худшие
        # edge-прогоны выпадали из колонки Edge Cases и завышали её (survivorship bias).
        if use_judges:
            from llmbench.judges import run_panel
            panel = await run_panel("\n".join(case.turns), case.rubric, rr["answer"], candidate_vendors, judges)
            rec["soft_quality"], rec["soft_russian"] = panel["primary"]["quality"], panel["primary"]["russian"]
            rec["judges_detail"] = panel["judges"]
    rec["composite"] = report.composite(rec)
    return rec


def _git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parent.parent, timeout=5)
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 — метаданные best-effort
        return None


def _build_caveats(mode, repeat, neutral):
    caveats = [
        "**GPT гоняли через отдельную обвязку** (askads на Anthropic, GPT в его движок не вставить) — "
        "точность работы GPT с инструментами сравнима с Claude/GLM не идеально (другой формат вызова тулов).",
        "**Цены и скидки за кэш (кэш-хит)** — по прайс-листам; сверить с реальными счетами.",
        "**Модель могла подмениться**: ответ API на имя `glm-5`/`gpt-5` ещё не гарантирует, что под капотом именно она.",
    ]
    if not neutral:
        caveats.append("**Независимого судьи нет**: ответы оценивают те же компании, чьи модели и "
                       "сравниваются — возможно завышение «своей» модели; оценки судей вспомогательные, "
                       "вес на ключевых метриках Tools Use/Accuracy (их считает код).")
    if repeat < 3:
        caveats.append(f"**Мало повторов** ({repeat}) — Stability на {repeat} точках доверять рано; "
                       "в Accuracy «уверенно неверное число» = «не названо».")
    if mode == "fixed":
        caveats.append("Режим `fixed`: модели видят аккуратные тестовые данные (фикстуры), а не «грязный» "
                       "реальный вывод API (для этого `--mode live`).")
    return caveats


def _filter_or_die(items, patterns, what, key):
    if not patterns:
        return list(items)
    picked = [it for it in items if any(p.lower() in key(it).lower() for p in patterns)]
    if not picked:
        names = "\n  ".join(key(it) for it in items)
        sys.exit(f"Фильтр {what} {patterns!r} не совпал ни с чем — не запускаю ничего "
                 f"(раньше тут молча уезжал ПОЛНЫЙ грид). Доступно:\n  {names}")
    return picked


def _report_from(path, out_arg):
    meta, recs_by = None, {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj["type"] == "meta":
            meta = obj["meta"]
        elif obj["type"] == "run":
            recs_by.setdefault(obj["variant"], []).append(obj["rec"])
    if not meta or not recs_by:
        sys.exit(f"{path}: нет meta/run записей — это не лог ранера")
    aggregates = {label: report.agg(rs) for label, rs in recs_by.items()}
    out = Path(out_arg or DEFAULT_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.build_md(aggregates, meta), encoding="utf-8")
    print(f"Отчёт пересобран из {path} → {out}")


async def _run_variant(v, cases, args, judges_all, candidate_vendors, jsonl_path, jsonl_lock):
    sem = asyncio.Semaphore(args.concurrency)

    async def one(case, ri):
        async with sem:
            rr, wasted = await _run_case_retried(v, case, _nonce(case.id, v["label"], ri), args.mode)
            rec = await _score(case, v, rr, candidate_vendors, args.judges != "off", judges_all, wasted)
        line = {"type": "run", "variant": v["label"], "case": case.id, "repeat": ri, "rec": rec,
                "answer": rr["answer"], "tool_trace": rr["tool_trace"], "usage": rr["usage"]}
        async with jsonl_lock:
            with jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"  {v['label']:<24} {case.id:<20} r{ri} tool={rec['tool']} num={rec['numeric']} "
              f"soft={rec['soft_quality']} ${rec['cost']:.5f}" + (" RETRY" if rec["retried"] else "")
              + (f" ERR={rec['error'][:60]}" if rec["error"] else ""))
        return rec

    return await asyncio.gather(*[one(c, ri) for c in cases for ri in range(args.repeat)])


async def main():
    ap = argparse.ArgumentParser(description="Бенчмарк моделей на MCP-тулзах")
    ap.add_argument("--mode", choices=["fixed", "live"], default="fixed")
    ap.add_argument("--variants", nargs="*", default=None)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--judges", choices=["panel", "neutral", "off"], default="panel")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="параллельных прогонов внутри варианта (кейсы × повторы)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None, help=f"файл отчёта (дефолт {DEFAULT_OUT})")
    ap.add_argument("--report-from", default=None,
                    help="пересобрать отчёт из runs-*.jsonl без запусков (бесплатно)")
    args = ap.parse_args()

    if args.report_from:
        _report_from(args.report_from, args.out)
        return

    variants = _filter_or_die(VARIANTS, args.variants, "--variants", lambda v: v["label"])
    cases = _filter_or_die(CASES, args.cases, "--cases", lambda c: c.id)
    candidate_vendors = {v["vendor"] for v in variants}

    for v in variants:
        if v["model"] not in MODEL_RATES:
            print(f"[warn] модель {v['model']!r} ({v['label']}) без тарифа в core.MODEL_RATES — "
                  f"Cost/Pareto будут по дефолтной ставке")

    from llmbench.judges import available_judges
    judges_all = available_judges() if args.judges != "off" else []
    if args.judges == "neutral":
        judges_all = [j for j in judges_all if j["vendor"] not in candidate_vendors]
        if not judges_all:
            print("[warn] --judges neutral, но нейтральных судей нет (добавь GOOGLE_API_KEY?) — "
                  "судейство ВЫКЛЮЧЕНО, колонки Edge/Lang будут пустыми")
    neutral = [j["name"] for j in judges_all if j["vendor"] not in candidate_vendors]

    n_runs = len(variants) * len(cases) * args.repeat
    print(f"mode={args.mode} · вариантов={len(variants)} · кейсов={len(cases)} · repeat={args.repeat}")
    print(f"Судьи: {[j['name'] for j in judges_all] or '—'} · нейтрален: {neutral or '—'}")
    print(f"Смета (все выбранные варианты): run ≈ {n_runs} · судейских ≈ {n_runs * len(judges_all)}")
    if args.dry_run:
        print("--dry-run.")
        return

    runnable = [v for v in variants if os.environ.get(v["key_env"])]
    for v in variants:
        if v not in runnable:
            print(f"  [skip] {v['label']}: нет {v['key_env']}")
    if not runnable:
        sys.exit("Ни одного варианта с ключом в env — нечего запускать.")

    if args.mode == "live":
        from llmbench.mcp import preflight_live
        platforms = {c.platform for c in cases}
        if any(c.metrika_enabled for c in cases):
            platforms.add(PLATFORM_YANDEX_METRIKA)
        problems = preflight_live(platforms)
        if problems:
            sys.exit("Live-режим не готов (проверь ДО трат):\n  " + "\n  ".join(problems))

    if os.environ.get("RUN_BENCH") != "1":
        sys.exit("Нужен RUN_BENCH=1 (защита от случайного платного запуска).")

    ts = datetime.now(timezone.utc)
    jsonl_path = Path(f"results/runs-{ts.strftime('%Y%m%d-%H%M%S')}.jsonl")
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_lock = asyncio.Lock()
    meta = {"ts": ts.strftime("%Y-%m-%d %H:%M UTC"), "mode": args.mode,
            "repeat": args.repeat, "n_cases": len(cases), "variants": runnable,
            "judges": [j["name"] for j in judges_all] or "—", "neutral": neutral,
            "fixture_version": FIXTURE_VERSION, "git_commit": _git_commit(),
            "jsonl": str(jsonl_path),
            "baseline_desc": None, "caveats": _build_caveats(args.mode, args.repeat, neutral)}
    baseline = next((v for v in runnable if v.get("is_baseline")), None)
    if baseline:
        meta["baseline_desc"] = "{} (thinking {}, effort {})".format(*report.describe(baseline))
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "meta": meta}, ensure_ascii=False) + "\n")

    aggregates = {}
    for v in runnable:
        recs = await _run_variant(v, cases, args, judges_all, candidate_vendors, jsonl_path, jsonl_lock)
        aggregates[v["label"]] = report.agg(recs)

    out = Path(args.out) if args.out else Path(DEFAULT_OUT)
    if not args.out and (args.variants or args.cases):
        print(f"[note] частичный прогон (--variants/--cases): отчёт {out} содержит только "
              f"выбранные варианты/кейсы — не путать с полным гридом")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.build_md(aggregates, meta), encoding="utf-8")
    total_cost = sum(a["cost_total"] for a in aggregates.values())
    total_errors = sum(a["errors"] for a in aggregates.values())
    print(f"\nГотово → {out} · сырые данные: {jsonl_path} · потрачено ≈ ${total_cost:.2f} "
          f"· ошибок {total_errors}/{sum(a['n_runs'] for a in aggregates.values())}")


if __name__ == "__main__":
    asyncio.run(main())
