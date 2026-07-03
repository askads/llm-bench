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


def describe(v):
    """Вариант → (LLM, Thinking, Effort). Thinking: adaptive/reasoning/нет; у GLM effort не рычаг (—)."""
    llm = MODEL_DISPLAY.get(v["model"], v["model"])
    if v.get("reasoning_effort"):
        return llm, "reasoning", v["reasoning_effort"]
    if v["engine"] == "openai":
        return llm, "нет", "—"
    thinking = "adaptive" if v.get("thinking") == "adaptive" else "нет"
    effort = "—" if v["vendor"] == "zai" else (v.get("effort") or "—")
    return llm, thinking, effort


GLOSSARY = """## Термины (как читать таблицу)

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


def build_md(aggregates, meta):
    o = ["# Сравнение моделей для AskAds (Claude / GLM / GPT)\n"]
    total = sum(a["n_runs"] for a in aggregates.values())
    judges = ', '.join(meta['judges']) if isinstance(meta['judges'], list) else meta['judges']
    commit = f" · код `{meta['git_commit']}`" if meta.get("git_commit") else ""
    o.append(f"_Запуск от {meta['ts']} × **{len(meta['variants'])} вариантов** "
             f"(модель × thinking/effort) × **{meta['n_cases']} тест-кейсов** × **{meta['repeat']} повтора** "
             f"= {total} запусков · режим {meta['mode']} · вход одинаковый для всех "
             f"(фикстуры версии `{meta['fixture_version']}`){commit}._\n")
    o.append("**Как считалось.** Claude/GLM — наш агентный движок; GPT — отдельный OpenAI-цикл "
             "(askads на Anthropic, GPT в тот же движок не встроить) → его tool-use сопоставим не на 100%. "
             f"**Tools Use/Accuracy** считает код; **Edge Cases/Lang quality** — LLM-судьи ({judges}; "
             f"нейтрален: **{meta['neutral'] or '—'}**). Судьи вторичны — вес на ключевых метриках.\n")
    o.append(GLOSSARY)
    o.append("## Все варианты (сорт. по Score)\n")
    o.append("| LLM | Thinking | Effort | Accuracy | Tools<br>Use | Edge<br>Cases | Lang<br>quality | "
             "Cost<br>per Answer | Score<br>per USD (s) | Score<br>per USD (m) | Stability | Err | Score |")
    o.append("|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    front = set(pareto(aggregates))
    by_label = {v["label"]: v for v in meta["variants"]}
    for label, a in sorted(aggregates.items(), key=lambda kv: (kv[1]["composite"] is None, -(kv[1]["composite"] or 0))):
        llm, thinking, effort = describe(by_label[label])
        spd = a["score_per_dollar"]
        cost = "—" if a["cost_avg"] is None else f"${a['cost_avg']:.5f}"
        err = f"{a['errors']}/{a['n_runs']}" + (f" ·{a['retried']}R" if a.get("retried") else "")
        stability = _f(round(5 - a["stddev_composite"], 3) if a["stddev_composite"] is not None else None)
        o.append(f"| {llm}{' ⭐' if label in front else ''} | {thinking} | {effort} | {_f(a['numeric'])} | "
                 f"{_f(a['tool'])} | {_f(a['edge'])} | {_f(a['russian'])} | {cost} | {_f(spd['single'])} | "
                 f"{_f(spd['multi'])} | {stability} | {err} | {_f(a['composite'])} |")
    o.append(f"\n⭐ — **лучший баланс «качество/цена»** (нельзя стать и качественнее, и дешевле одновременно): "
             f"**{', '.join(front) or '—'}**.\n")
    if meta.get("baseline_desc"):
        o.append(f"_Для ориентира: текущий прод askads — {meta['baseline_desc']}._\n")
    o.append("\n## Известные ограничения\n")
    for line in meta["caveats"]:
        o.append(f"- {line}")
    if meta.get("jsonl"):
        o.append(f"\n_Сырые per-run данные: `{meta['jsonl']}` — отчёт пересобирается из них "
                 f"командой `python -m llmbench.runner --report-from <файл>`._")
    return "\n".join(o)
