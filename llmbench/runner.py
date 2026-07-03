"""Ранер: сетка вариантов (модель × thinking/effort/reasoning) × кейсы × repeat.

Режимы MCP: --mode fixed (фикстуры, детерминированно, CI) | live (реальные MCP-серверы +
токены из env). Движок и MCP развязаны от askads. Каждый прогон складывается в дата-папку
results/<date>/: сырой runs.jsonl (ответы, трейсы, usage, оценки) + двуязычный отчёт
results.ru.md и results.en.md. Отчёт пересобирается из runs.jsonl без повторных трат
(`--report-from`).

  # детерминированный model-бенч (нужны ключи моделей):
  python -m llmbench.runner --mode fixed --repeat 2
  # против РЕАЛЬНЫХ тулов (нужны npm-серверы + токены кабинета):
  python -m llmbench.runner --mode live --variants "GLM-4.6 disabled" --judges off
  # пересобрать отчёт (ru+en) из сырых данных (бесплатно):
  python -m llmbench.runner --report-from results/2026-07-03/runs.jsonl
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
# Каждый прогон — своя дата-папка results/<date>/ с сырым runs.jsonl и двуязычным отчётом
# (results.ru.md + results.en.md). Проза/Топ-3 дописываются руками поверх сгенерированного
# грида — прогон не затирает чужую дата-папку (при коллизии по дате добавляет время).
RESULTS_ROOT = "results"
REPORT_STEM = "results"  # results.ru.md / results.en.md
JSONL_NAME = "runs.jsonl"


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
    """Оговорки на обоих языках → {'ru': [...], 'en': [...]}; build_md берёт нужный по lang."""
    ru, en = [], []

    def add(r, e):
        ru.append(r)
        en.append(e)

    add("**GPT гоняли через отдельную обвязку** (askads на Anthropic, GPT в его движок не вставить) — "
        "точность работы GPT с инструментами сравнима с Claude/GLM не идеально (другой формат вызова тулов).",
        "**GPT was run via a separate wrapper** (askads is on Anthropic, GPT can't be plugged into its "
        "engine) — GPT's tool-use accuracy isn't perfectly comparable to Claude/GLM (a different tool-call format).")
    add("**Цены и скидки за кэш (кэш-хит)** — по прайс-листам; сверить с реальными счетами.",
        "**Prices and cache-read discounts** — from price lists; verify against real bills.")
    add("**Модель могла подмениться**: ответ API на имя `glm-5`/`gpt-5` ещё не гарантирует, что под капотом именно она.",
        "**The model may have been substituted**: the API answering to `glm-5`/`gpt-5` doesn't guarantee "
        "that's the model under the hood.")
    if not neutral:
        add("**Независимого судьи нет**: ответы оценивают те же компании, чьи модели и сравниваются — "
            "возможно завышение «своей» модели; оценки судей вспомогательные, вес на ключевых метриках "
            "Tools Use/Accuracy (их считает код).",
            "**No independent judge**: answers are scored by the same companies whose models are compared — "
            "possible self-model inflation; judge scores are auxiliary, weight is on the key metrics "
            "Tools Use/Accuracy (computed in code).")
    if repeat < 3:
        add(f"**Мало повторов** ({repeat}) — Stability на {repeat} точках доверять рано; "
            "в Accuracy «уверенно неверное число» = «не названо».",
            f"**Few repeats** ({repeat}) — Stability on {repeat} points is premature to trust; "
            "in Accuracy a \"confidently wrong number\" = \"not stated\".")
    if mode == "fixed":
        add("Режим `fixed`: модели видят аккуратные тестовые данные (фикстуры), а не «грязный» "
            "реальный вывод API (для этого `--mode live`).",
            "Mode `fixed`: models see clean test data (fixtures), not the \"messy\" real API output "
            "(use `--mode live` for that).")
    return {"ru": ru, "en": en}


def _filter_or_die(items, patterns, what, key):
    if not patterns:
        return list(items)
    picked = [it for it in items if any(p.lower() in key(it).lower() for p in patterns)]
    if not picked:
        names = "\n  ".join(key(it) for it in items)
        sys.exit(f"Фильтр {what} {patterns!r} не совпал ни с чем — не запускаю ничего "
                 f"(раньше тут молча уезжал ПОЛНЫЙ грид). Доступно:\n  {names}")
    return picked


def _run_dir(ts, out_arg=None):
    """Каталог прогона: --out override или results/<YYYY-MM-DD>/ (при коллизии по дате — +время,
    чтобы второй прогон за день не затирал первый)."""
    if out_arg:
        return Path(out_arg)
    base = Path(RESULTS_ROOT) / ts.strftime("%Y-%m-%d")
    if base.exists():
        base = Path(RESULTS_ROOT) / ts.strftime("%Y-%m-%d_%H%M%S")
    return base


def _write_reports(aggregates, meta, run_dir):
    """Двуязычный отчёт results.ru.md + results.en.md в run_dir → dict lang→Path."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for lang in ("ru", "en"):
        p = run_dir / f"{REPORT_STEM}.{lang}.md"
        p.write_text(report.build_md(aggregates, meta, lang=lang), encoding="utf-8")
        paths[lang] = p
    return paths


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
    # По умолчанию — рядом с исходным JSONL (в его дата-папке); --out переопределяет каталог.
    paths = _write_reports(aggregates, meta, out_arg or Path(path).parent)
    print(f"Отчёт пересобран из {path} → {paths['ru']} + {paths['en']}")


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
    ap.add_argument("--out", default=None,
                    help="каталог отчёта (дефолт results/<date>/); пишет results.ru.md + results.en.md")
    ap.add_argument("--report-from", default=None,
                    help="пересобрать отчёт (ru+en) из results/<date>/runs.jsonl без запусков (бесплатно)")
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
    run_dir = _run_dir(ts, args.out)
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = run_dir / JSONL_NAME
    jsonl_lock = asyncio.Lock()
    # baseline_desc в meta не пишем — build_md выводит его из variants (is_baseline) на нужном языке.
    meta = {"ts": ts.strftime("%Y-%m-%d %H:%M UTC"), "mode": args.mode,
            "repeat": args.repeat, "n_cases": len(cases), "variants": runnable,
            "judges": [j["name"] for j in judges_all] or "—", "neutral": neutral,
            "fixture_version": FIXTURE_VERSION, "git_commit": _git_commit(),
            "jsonl": str(jsonl_path),
            "caveats": _build_caveats(args.mode, args.repeat, neutral)}
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "meta": meta}, ensure_ascii=False) + "\n")

    aggregates = {}
    for v in runnable:
        recs = await _run_variant(v, cases, args, judges_all, candidate_vendors, jsonl_path, jsonl_lock)
        aggregates[v["label"]] = report.agg(recs)

    if args.variants or args.cases:
        print(f"[note] частичный прогон (--variants/--cases): отчёт в {run_dir} содержит только "
              f"выбранные варианты/кейсы — не путать с полным гридом")
    paths = _write_reports(aggregates, meta, run_dir)
    total_cost = sum(a["cost_total"] for a in aggregates.values())
    total_errors = sum(a["errors"] for a in aggregates.values())
    print(f"\nГотово → {paths['ru']} + {paths['en']} · сырые данные: {jsonl_path} · "
          f"потрачено ≈ ${total_cost:.2f} · ошибок {total_errors}/"
          f"{sum(a['n_runs'] for a in aggregates.values())}")


if __name__ == "__main__":
    asyncio.run(main())
