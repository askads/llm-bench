"""Агрегация per-run записей и сборка markdown-отчёта.

Вынесено из runner.py, чтобы логика, производящая публикуемые числа, была тестируемой
без SDK-зависимостей (anthropic/openai/mcp) и покрывалась CI.

Контракт записи (rec): case, dimension, turn_type, tool, numeric, has_golden, soft_quality,
soft_russian, cost, cost_wasted, retried, error, composite. У упавших прогонов (error != None)
метрики и composite равны None — они не входят в средние, но видны в колонке Err.
"""
from __future__ import annotations

import statistics


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def composite(rec):
    """Сводный балл прогона: среднее доступных компонент (tool всегда; numeric — если есть
    golden-факты; оценки судей — если судьи работали). У упавших прогонов — None."""
    if rec.get("error"):
        return None
    parts = [rec["tool"]]
    if rec["has_golden"]:
        parts.append(rec["numeric"])
    if rec["soft_quality"] is not None:
        parts.append(rec["soft_quality"])
    if rec["soft_russian"] is not None:
        parts.append(rec["soft_russian"])
    parts = [p for p in parts if p is not None]
    return round(sum(parts) / len(parts), 3) if parts else None


def agg(records):
    ok = [r for r in records if not r["error"]]
    num = [r for r in ok if r["has_golden"]]
    edge = [r for r in ok if r["dimension"] == "edge" and r["soft_quality"] is not None]
    comps = [r["composite"] for r in ok if r["composite"] is not None]

    def spd(tt):
        rs = [r for r in ok if r["turn_type"] == tt]
        c, cost = _mean([r["composite"] for r in rs]), _mean([r["cost"] for r in rs])
        return round(c / cost, 1) if (c is not None and cost) else None

    # Stability: разброс между ПОВТОРАМИ одного кейса, усреднённый по кейсам — а не pstdev
    # по всем записям (там доминировала бы разница сложности кейсов, а не шум модели).
    by_case = {}
    for r in ok:
        if r["composite"] is not None:
            by_case.setdefault(r["case"], []).append(r["composite"])
    sigmas = [statistics.pstdev(v) for v in by_case.values() if len(v) >= 2]

    return {"numeric": _mean([r["numeric"] for r in num]), "tool": _mean([r["tool"] for r in ok]),
            "edge": _mean([r["soft_quality"] for r in edge]),
            "russian": _mean([r["soft_russian"] for r in ok]),
            "composite": _mean(comps), "cost_avg": _mean([r["cost"] for r in ok]),
            "score_per_dollar": {"single": spd("single"), "multi": spd("multi")},
            "stddev_composite": round(sum(sigmas) / len(sigmas), 3) if sigmas else None,
            "errors": sum(1 for r in records if r["error"]),
            "retried": sum(1 for r in records if r.get("retried")),
            "n_runs": len(records),
            # полная стоимость варианта, ВКЛЮЧАЯ упавшие прогоны и потраченные повторы
            "cost_total": round(sum((r["cost"] or 0) + (r.get("cost_wasted") or 0)
                                    for r in records), 5)}


def pareto(aggregates):
    items = [(k, a["composite"], a["cost_avg"]) for k, a in aggregates.items()
             if a["composite"] is not None and a["cost_avg"] is not None and a["cost_avg"] > 0]
    return [k for k, c, cost in items
            if not any(c2 >= c and cost2 <= cost and (c2, cost2) != (c, cost) for _, c2, cost2 in items)]


def _f(x):
    return "—" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


MODEL_DISPLAY = {
    "claude-sonnet-4-6": "Sonnet 4.6", "claude-opus-4-8": "Opus 4.8",
    "glm-4.6": "GLM-4.6", "glm-5": "GLM-5", "gpt-5": "GPT-5", "gpt-4.1": "GPT-4.1",
}


def describe(v, lang="ru"):
    """Вариант → (LLM, Thinking, Effort). Thinking: adaptive/reasoning/нет(en: no); у GLM effort не рычаг (—)."""
    llm = MODEL_DISPLAY.get(v["model"], v["model"])
    no = "no" if lang == "en" else "нет"
    if v.get("reasoning_effort"):
        return llm, "reasoning", v["reasoning_effort"]
    if v["engine"] == "openai":
        return llm, no, "—"
    thinking = "adaptive" if v.get("thinking") == "adaptive" else no
    effort = "—" if v["vendor"] == "zai" else (v.get("effort") or "—")
    return llm, thinking, effort


GLOSSARY_RU = """## Термины (как читать таблицу)

- **Accuracy** (0–5) — точность чисел: верно ли посчитаны CTR/CPC/CPA/расход, не выдуманы ли
  цифры и той ли кампании они приписаны (entity-анкоринг). **В коде** (детерминированно).
- **Tools Use** (0–5) — корректность инструментов: вызвал нужные тулы (успешно) в нужном
  порядке, без лишних/запрещённых. **Код**.
- **Edge Cases** (0–5) — поведение в краевых случаях (пустой отчёт, отказ менять ставку,
  уточнение). **LLM-судьи** — оценивают и прогоны с нарушениями по тулам.
- **Lang quality** (0–5) — естественность и ясность русского. Судьи.
- **Score** (0–5) — сводный балл прогона = среднее доступных компонент: Tools Use (всегда),
  Accuracy (если у кейса есть golden-факты), Edge Cases/Lang quality (если судьи работали).
  Состав компонент зависит от кейса, поэтому Score сравним между вариантами (кейсы у всех
  одни), но НЕ равен среднему четырёх колонок слева. Упавшие прогоны в Score не входят — см. Err.
- **Cost per Answer** — средняя стоимость успешного прогона (USD); **Score per USD (s/m)** —
  «качество на доллар» (Score ÷ цена) для одно-/многошаговых диалогов; выше = выгоднее.
- **Stability** (0–5) — `5 − средний разброс (σ) Score между повторами одного кейса`:
  выше = стабильнее. Осмысленна при repeat ≥ 2.
- **Err** — `упавшие/все прогоны` (ошибки API, обрезка лимитом токенов); суффикс `·NR` —
  N прогонов удались только после повтора тем же конфигом. Упавшие прогоны исключены из
  всех метрик, но их стоимость входит в полную стоимость прогона.
- **Thinking** — думает ли модель перед ответом: `adaptive` (Claude/GLM), `reasoning` (GPT-5), `нет`.
- **Effort** — бюджет «усилий» на ответ (`low/medium/high/max`); отдельная от thinking
  настройка (при выключенном thinking влияет слабо). У GLM не настраивается (`—`).
- **⭐** — **лучший баланс «качество/цена»**: вариант, который нельзя «побить» — нет другого,
  который и качественнее, и дешевле. _(В оптимизации — «Pareto-фронт».)_
"""


GLOSSARY_EN = """## Terms (how to read the table)

- **Accuracy** (0–5) — numeric correctness: are CTR/CPC/CPA/spend computed right, nothing made
  up, and are the numbers attributed to the right campaign (entity anchoring). **In code**
  (deterministic).
- **Tools Use** (0–5) — tool correctness: called the right tools (successfully) in the right
  order, nothing extra/forbidden. **Code**.
- **Edge Cases** (0–5) — behavior in edge cases (empty report, refusing to change a bid,
  clarifying). **LLM judges** — they also score runs with tool violations.
- **Lang quality** (0–5) — naturalness and clarity of the Russian. Judges.
- **Score** (0–5) — a run's overall score = mean of the available components: Tools Use
  (always), Accuracy (if the case has golden facts), Edge Cases/Lang quality (if judges ran).
  The component set depends on the case, so Score is comparable across variants (everyone runs
  the same cases) but is NOT equal to the mean of the four left columns. Failed runs are
  excluded from Score — see Err.
- **Cost per Answer** — mean cost of a successful run (USD); **Score per USD (s/m)** — "quality
  per dollar" (Score ÷ cost) for single-/multi-step dialogs; higher = better value.
- **Stability** (0–5) — `5 − mean spread (σ) of Score between repeats of the same case`:
  higher = more stable. Meaningful at repeat ≥ 2.
- **Err** — `failed/all runs` (API errors, token-limit truncation); suffix `·NR` — N runs
  succeeded only after a retry with the same config. Failed runs are excluded from all metrics,
  but their cost is included in the total run cost.
- **Thinking** — whether the model thinks before answering: `adaptive` (Claude/GLM),
  `reasoning` (GPT-5), `no`.
- **Effort** — the "effort" budget per answer (`low/medium/high/max`); separate from thinking
  (weak effect when thinking is off). Not configurable for GLM (`—`).
- **⭐** — **best quality/price balance**: a variant that can't be beaten — no other is both
  better and cheaper. _(In optimization — the "Pareto frontier".)_
"""


# Локализация проз-строк отчёта. Колонки таблицы (Accuracy/Tools Use/…) — англ. в обеих
# версиях, поэтому переводим только заголовки/пояснения/оговорки; числа считает agg().
_LANG = {
    "ru": {
        "title": "# Сравнение моделей для AskAds (Claude / GLM / GPT)\n",
        "cross": "🇷🇺 Русский · [🇬🇧 English](results.en.md)\n",
        "runline": ("_Запуск от {ts} × **{nv} вариантов** (модель × thinking/effort) × "
                    "**{nc} тест-кейсов** × **{rep} повтора** = {total} запусков · режим {mode} · "
                    "вход одинаковый для всех (фикстуры версии `{fx}`){commit}._\n"),
        "commit": " · код `{c}`",
        "how": ("**Как считалось.** Claude/GLM — наш агентный движок; GPT — отдельный OpenAI-цикл "
                "(askads на Anthropic, GPT в тот же движок не встроить) → его tool-use сопоставим не "
                "на 100%. **Tools Use/Accuracy** считает код; **Edge Cases/Lang quality** — LLM-судьи "
                "({judges}; нейтрален: **{neutral}**). Судьи вторичны — вес на ключевых метриках.\n"),
        "glossary": GLOSSARY_RU,
        "variants_h": "## Все варианты (сорт. по Score)\n",
        "star": ("\n⭐ — **лучший баланс «качество/цена»** (нельзя стать и качественнее, и дешевле "
                 "одновременно): **{front}**.\n"),
        "baseline": "_Для ориентира: текущий прод askads — {desc}._\n",
        "limits_h": "\n## Известные ограничения\n",
        "jsonl": ("\n_Сырые per-run данные: `{jsonl}` — отчёт пересобирается из них командой "
                  "`python -m llmbench.runner --report-from <файл>`._"),
        "none": "—",
    },
    "en": {
        "title": "# Model comparison for AskAds (Claude / GLM / GPT)\n",
        "cross": "[🇷🇺 Русский](results.ru.md) · 🇬🇧 English\n",
        "runline": ("_Run from {ts} × **{nv} variants** (model × thinking/effort) × "
                    "**{nc} test cases** × **{rep} repeats** = {total} runs · mode {mode} · "
                    "identical input for all (fixtures version `{fx}`){commit}._\n"),
        "commit": " · code `{c}`",
        "how": ("**How it was measured.** Claude/GLM — our agentic engine; GPT — a separate OpenAI "
                "loop (askads is on Anthropic, GPT can't be plugged into the same engine) → its "
                "tool-use isn't 100% comparable. **Tools Use/Accuracy** are computed in code; **Edge "
                "Cases/Lang quality** — LLM judges ({judges}; neutral: **{neutral}**). Judges are "
                "secondary — weight is on the key metrics.\n"),
        "glossary": GLOSSARY_EN,
        "variants_h": "## All variants (sorted by Score)\n",
        "star": ("\n⭐ — **best quality/price balance** (can't become both better and cheaper at "
                 "once): **{front}**.\n"),
        "baseline": "_For reference: current askads production — {desc}._\n",
        "limits_h": "\n## Known limitations\n",
        "jsonl": ("\n_Raw per-run data: `{jsonl}` — the report is rebuilt from it with "
                  "`python -m llmbench.runner --report-from <file>`._"),
        "none": "—",
    },
}

_TABLE_HEADER = ("| LLM | Thinking | Effort | Accuracy | Tools<br>Use | Edge<br>Cases | "
                 "Lang<br>quality | Cost<br>per Answer | Score<br>per USD (s) | "
                 "Score<br>per USD (m) | Stability | Err | Score |")
_TABLE_SEP = "|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"


def build_md(aggregates, meta, lang="ru"):
    t = _LANG.get(lang, _LANG["ru"])
    total = sum(a["n_runs"] for a in aggregates.values())
    judges = ', '.join(meta['judges']) if isinstance(meta['judges'], list) else meta['judges']
    nv = meta.get('neutral')
    neutral = (', '.join(nv) if isinstance(nv, list) else (str(nv) if nv else "")) or t["none"]
    commit = t["commit"].format(c=meta['git_commit']) if meta.get("git_commit") else ""
    o = [t["title"], t["cross"]]
    o.append(t["runline"].format(ts=meta['ts'], nv=len(meta['variants']), nc=meta['n_cases'],
                                 rep=meta['repeat'], total=total, mode=meta['mode'],
                                 fx=meta['fixture_version'], commit=commit))
    o.append(t["how"].format(judges=judges, neutral=neutral))
    o.append(t["glossary"])
    o.append(t["variants_h"])
    o.append(_TABLE_HEADER)
    o.append(_TABLE_SEP)
    front = set(pareto(aggregates))
    by_label = {v["label"]: v for v in meta["variants"]}
    for label, a in sorted(aggregates.items(), key=lambda kv: (kv[1]["composite"] is None, -(kv[1]["composite"] or 0))):
        llm, thinking, effort = describe(by_label[label], lang)
        spd = a["score_per_dollar"]
        cost = t["none"] if a["cost_avg"] is None else f"${a['cost_avg']:.5f}"
        err = f"{a['errors']}/{a['n_runs']}" + (f" ·{a['retried']}R" if a.get("retried") else "")
        stability = _f(round(5 - a["stddev_composite"], 3) if a["stddev_composite"] is not None else None)
        o.append(f"| {llm}{' ⭐' if label in front else ''} | {thinking} | {effort} | {_f(a['numeric'])} | "
                 f"{_f(a['tool'])} | {_f(a['edge'])} | {_f(a['russian'])} | {cost} | {_f(spd['single'])} | "
                 f"{_f(spd['multi'])} | {stability} | {err} | {_f(a['composite'])} |")
    o.append(t["star"].format(front=', '.join(front) or t["none"]))
    baseline = next((v for v in meta["variants"] if v.get("is_baseline")), None)
    if baseline:
        bl_llm, bl_th, bl_ef = describe(baseline, lang)
        o.append(t["baseline"].format(desc=f"{bl_llm} (thinking {bl_th}, effort {bl_ef})"))
    o.append(t["limits_h"])
    caveats = meta["caveats"]
    if isinstance(caveats, dict):
        caveats = caveats.get(lang) or caveats.get("ru") or []
    for line in caveats:
        o.append(f"- {line}")
    if meta.get("jsonl"):
        o.append(t["jsonl"].format(jsonl=meta['jsonl']))
    return "\n".join(o)
