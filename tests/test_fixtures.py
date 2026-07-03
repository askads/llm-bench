"""Офлайн self-test (БЕЗ сети/денег) — собирается в CI.

Проверяет фундамент дорогого прогона: парсер/матчер чисел (самое хрупкое), заглушку MCP
(формы tool_result + совместимость с конвертерами), целостность кейсов и отсутствие
коллизий golden-значений, стоимость. Агрегация/отчёт — в test_report.py, конвейер ранера —
в test_runner_e2e.py.
"""
import asyncio
import json

from llmbench import fixtures as acc
from llmbench import scoring as S
from llmbench.cases import CASES, by_id
from llmbench.core import (METRIKA_PREFIX, READ_ONLY_TOOLS, TOOL_RESULT_CHAR_BUDGET,
                           to_anthropic_tools, to_metrika_anthropic_tools, to_openai_tools)
from llmbench.mcp import _DIRECT_TOOLS, _METRIKA_TOOLS, FakeMCPSession


def test_parse_russian_number_formats():
    cases = {"расход 58 800 ₽": 58800.0, "CPA 1200 ₽": 1200.0, "CTR 2,44%": 2.44,
             "примерно 1,2 тыс ₽": 1200.0, "3,4 млн показов": 3_400_000.0, "1234.56": 1234.56,
             "цена клика 60 ₽": 60.0, "512 300 показов": 512300.0,
             # NBSP / узкий NBSP — модели часто форматируют тысячи именно так
             "расход 58 800 ₽": 58800.0, "показов 512 300": 512300.0,
             # US-стиль: запятая как разделитель тысяч, а не десятичная
             "spent 58,800 RUB": 58800.0, "total 1,234,567": 1234567.0}
    for text, expected in cases.items():
        nums = S.parse_numbers(text)
        assert any(abs(n["value"] - expected) < 0.01 for n in nums), f"{text!r} -> {[n['value'] for n in nums]}"


def test_presence_entity_attribution():
    fact = {"value": 5150, "tolerance": 0.05, "aliases": ["CPA"], "entity": ["рся"]}
    rivals = ["москва", "поиск-москва"]
    # своя кампания перед числом → зачёт
    assert S.check_presence("РСЯ-Россия: CPA 5 150 ₽", fact, rivals)
    # чужая кампания ближе → числу нельзя верить
    assert not S.check_presence("Поиск-Москва: CPA 5150 ₽. РСЯ-Россия: CPA 1200 ₽.", fact, rivals)
    # entity вообще не упомянута → нет атрибуции
    assert not S.check_presence("средний CPA по аккаунту: 5000", fact, rivals)
    # metric-алиас обязателен даже при верной entity
    assert not S.check_presence("РСЯ-Россия: 5150", {**fact, "aliases": ["CPC"]}, rivals)
    # факт без entity — мягкий режим (однокампейн-кейсы)
    assert S.check_presence("Расход составил 41 200 ₽",
                            {"value": 41200, "tolerance": 0.02, "aliases": ["расход"]})


def test_anchoring_and_absence():
    ans = "Поиск-Москва: расход 58 800 ₽, CPA 1200 ₽. РСЯ-Россия: расход 41 200 ₽, CPA 5150 ₽."
    # other_metrics=['расход'] разводит числа расхода и CPA; sibling_values=[5150,1200] —
    # пул целевых значений для позиционного сопоставления (как передаёт score_numeric)
    assert S.check_presence(ans, {"value": 5150, "tolerance": 0.05, "aliases": ["CPA"], "entity": ["рся"]},
                            ["москва"], ["расход"], [5150, 1200])
    assert not S.check_presence(ans, {"value": 9999, "tolerance": 0.02, "aliases": ["расход"]})
    fact = {"kind": "absent", "key": "cpa", "aliases": ["CPA", "стоимость конверси"]}
    assert not S.check_absence_violation("Конверсии не настроены, CPA посчитать нельзя.", fact)
    assert S.check_absence_violation("CPA составляет 180 ₽.", fact)
    assert not S.check_absence_violation("Конверсий 0, поэтому стоимость конверсии не определить.", fact)
    # число с единицей после — не значение CPA
    assert not S.check_absence_violation("CPA рассчитать нельзя (0 конверсий).", fact)
    # даты в окне после алиаса — не значение CPA
    assert not S.check_absence_violation("CPA за период 2026-06-01 — 2026-06-07 не посчитать.", fact)
    # типовая формулировка выдуманного CPA ловится расширенным алиасом
    fact_full = {"kind": "absent", "key": "cpa",
                 "aliases": ["CPA", "стоимость конверси", "стоимость одной конверси"]}
    assert S.check_absence_violation("Стоимость одной конверсии — 180 ₽.", fact_full)


def test_score_numeric():
    facts = [{"key": "a", "value": 100, "tolerance": 0.02, "required": True, "aliases": ["расход"]},
             {"key": "b", "value": 50, "tolerance": 0.02, "required": True, "aliases": ["клик", "CPC"]}]
    assert S.score_numeric("расход 100 ₽, CPC 50 ₽", facts)["score"] == 5.0
    assert S.score_numeric("расход 100 ₽", facts)["score"] == 2.5
    av = [{"kind": "absent", "key": "x", "aliases": ["CPA"]}]
    assert S.score_numeric("CPA 5 ₽", av)["score"] == 0.0
    assert S.score_numeric("CPA не посчитать", av)["score"] == 5.0


def test_swapped_attribution_scores_zero():
    """Перепутанные местами CPA двух кампаний не должны давать 5.0 (REVIEW.md R2)."""
    c = by_id("tool_multi_step")
    swapped = "Поиск-Москва: CPA 5150 ₽. РСЯ-Россия: CPA 1200 ₽."
    assert S.score_numeric(swapped, c.golden_facts)["score"] == 0.0
    good = "РСЯ-Россия: CPA 5 150 ₽ (перерасход). Поиск-Москва: CPA 1 200 ₽ — эффективна."
    assert S.score_numeric(good, c.golden_facts)["score"] == 5.0


def test_attribution_comparative_and_table():
    """Корректные ответы в сравнительном и табличном формате (их рекомендует промпт)
    получают 5.0, а перестановки — 0.0. Позиционное сопоставление + строка/столбец таблицы."""
    c = by_id("tool_multi_step")

    def sc(a):
        return S.score_numeric(a, c.golden_facts)["score"]

    # сравнительная проза «A vs B: X vs Y»
    assert sc("Поиск-Москва vs РСЯ-Россия — CPA 1 200 ₽ против 5 150 ₽.") == 5.0
    assert sc("РСЯ-Россия (CPA 5 150 ₽ против 1 200 ₽ у Поиск-Москва).") == 5.0
    assert sc("Поиск-Москва vs РСЯ-Россия: CPA 5 150 ₽ против 1 200 ₽.") == 0.0  # перестановка
    # смесь метрик в строке не «протекает» в CPA (расход не зачитывается как CPA)
    assert sc("РСЯ-Россия: расход 41 200 ₽, CPA 5 150 ₽. Поиск-Москва: расход 58 800 ₽, CPA 1 200 ₽.") == 5.0
    # markdown-таблица: метрика в шапке столбца, кампания в строке
    tbl = ("| Кампания | Расход | CPA |\n|---|---|---|\n"
           "| РСЯ-Россия | 41 200 ₽ | 5 150 ₽ |\n| Поиск-Москва | 58 800 ₽ | 1 200 ₽ |")
    assert sc(tbl) == 5.0
    assert sc(tbl.replace("5 150", "TMP").replace("1 200", "5 150").replace("TMP", "1 200")) == 0.0
    # среднее по аккаунту не приписывается кампании
    assert sc("Средний CPA по аккаунту: 5000 ₽.") == 0.0
    # вспомогательные числа (клики/показы/CTR/конверсии) не «протекают» в CPA-набор и не
    # ломают позиционное сопоставление (иначе развёрнутый корректный ответ → 0.0)
    assert sc("РСЯ-Россия: 512 300 показов, 1530 кликов, CPA 5 150 ₽. "
              "Поиск-Москва: 40 210 показов, 980 кликов, CPA 1 200 ₽.") == 5.0
    assert sc("РСЯ-Россия: CPA 5 150 ₽ при 8 конверсиях. "
              "Поиск-Москва: CPA 1 200 ₽ при 49 конверсиях.") == 5.0
    # key-value и in-cell таблицы, и одиночная труба в прозе
    assert sc("| Кампания | Показатель | Значение |\n|---|---|---|\n"
              "| РСЯ-Россия | CPA | 5 150 ₽ |\n| Поиск-Москва | CPA | 1 200 ₽ |") == 5.0
    assert sc("CPA (РСЯ-Россия | Поиск-Москва): 5 150 | 1 200 ₽.") == 5.0
    # транспонированная таблица: кампании в шапке столбцов, метрики в строках — кампания
    # берётся из шапки столбца, а не из строки
    tr = ("| Метрика | РСЯ-Россия | Поиск-Москва |\n|---|---|---|\n"
          "| Расход | 41 200 ₽ | 58 800 ₽ |\n| CPA | 5 150 ₽ | 1 200 ₽ |")
    assert sc(tr) == 5.0
    assert sc("| Метрика | РСЯ-Россия | Поиск-Москва |\n|---|---|---|\n| CPA | 1 200 ₽ | 5 150 ₽ |") == 0.0
    # count-слово рядом с настоящим денежным CPA НЕ перетягивает его (число money по своей
    # единице ₽, а не по близости «конверсий»/«%»); иначе корректный ответ падал бы в 0.0
    assert sc("CPA РСЯ-Россия снизился до 5 150 ₽ (доля 12%). Поиск-Москва: CPA 1 200 ₽.") == 5.0
    assert sc("У РСЯ-Россия CPA вырос на 30% до 5 150 ₽. У Поиск-Москва CPA 1 200 ₽.") == 5.0
    # список с метрикой в «шапке» строки и посторонним числом-ранжиром «в 4 раза»/«1)»
    assert sc("Топ по CPA: 1) РСЯ-Россия — 5 150 ₽; 2) Поиск-Москва — 1 200 ₽.") == 5.0
    assert sc("У РСЯ-Россия CPA 5150₽ — в 4 раза выше, чем у Поиск-Москва (1200₽).") == 5.0
    # метка чужой метрики СРАЗУ ПОСЛЕ значения («CPA 1 200 ₽ (расход …)») не крадёт его:
    # метки метрик стоят ПЕРЕД значением, левая метка приоритетнее правой
    assert sc("РСЯ-Россия: CPA 5 150 ₽ (расход 41 200 ₽). Поиск-Москва: CPA 1 200 ₽ (расход 58 800 ₽).") == 5.0
    # «5150 потрачено» — 5150 это расход, не CPA (ближайшая левая/правая метка — расход)
    assert sc("РСЯ-Россия потратила 5150 ₽ на рекламу.") == 0.0


def test_absence_no_overcorrection():
    """Выдуманный CPA рядом с временным/структурным словом ловится; дефис-диапазон — тоже;
    легитимные «нельзя посчитать» и настоящие даты — не ложное срабатывание (REVIEW.md R17/R18)."""
    c = by_id("cpa_not_configured")

    def viol(a):
        return S.check_absence_violation(a, c.golden_facts[0])

    for fabricated in ["CPA за неделю 180 ₽.", "CPA за период составил 180 ₽.",
                       "CPA по кампании 180 ₽.", "Стоимость конверсии за неделю 180 ₽.",
                       "CPA: 1500-2000 ₽ в зависимости от периода."]:
        assert viol(fabricated), fabricated
    for legit in ["CPA рассчитать нельзя (0 конверсий).",
                  "CPA за период 2026-06-01 — 2026-06-07 не посчитать.",
                  "Конверсии не настроены, CPA посчитать нельзя.",
                  # id кампании рядом с CPA — не выдуманное значение (есть id-маркер, нет ₽)
                  "CPA по кампании Бренд (id 12348) настроить нельзя — цели не заданы."]:
        assert not viol(legit), legit
    assert S.score_numeric("По кампании Бренд конверсии не настроены, CPA посчитать нельзя. "
                           "Расход 9 000 ₽, CPC — 6 ₽.", c.golden_facts)["score"] == 5.0


def test_no_golden_value_collisions():
    """Значение одного golden-факта не должно попадать в допуск другого, и никакая
    метрика фикстур не должна маскироваться под required-факт (кейс 1230≈1200)."""
    fixture_values = []
    for cid in (12345, 12346, 12348):
        m = acc.metrics(cid)
        fixture_values += [(cid, k, v) for k, v in m.items() if isinstance(v, (int, float)) and v]
    for c in CASES:
        req = [f for f in c.golden_facts if f.get("kind") != "absent" and f.get("required", True)]
        for f in req:
            tol = max(abs(f["value"]) * f.get("tolerance", 0.05), 0.05)
            for (cid, metric, v) in fixture_values:
                if abs(v - f["value"]) < 1e-9:  # само значение факта
                    continue
                assert abs(v - f["value"]) > tol, \
                    f"{c.id}/{f['key']}: {v} ({cid}.{metric}) в допуске {f['value']}±{tol}"


def test_toolcheck():
    trace = [{"name": "list_campaigns"}, {"name": "get_statistics"}]
    assert S.score_tooluse(trace, {"tools": ["list_campaigns", "get_statistics"], "ordered": True, "max_calls": 2})["score"] == 5.0
    assert S.score_tooluse([], {"forbid_tools": True, "max_calls": 0})["score"] == 5.0
    viol = S.score_tooluse([{"name": "get_statistics"}], {"forbid_tools": True, "max_calls": 0})
    assert viol["score"] == 0.0 and viol["fail_fast"]
    miss = S.score_tooluse([], {"tools": ["get_statistics"], "max_calls": 2})
    assert miss["fail_fast"] and miss["score"] == 0.0
    allow = S.score_tooluse([{"name": "list_campaigns"}, {"name": "get_statistics"}],
                            {"tools": ["get_statistics"], "allow": ["list_campaigns"], "max_calls": 3})
    assert allow["score"] == 5.0 and not allow["extra"]
    # упавший вызов required-тула НЕ засчитывается как выполненный
    errored = S.score_tooluse([{"name": "get_statistics", "is_error": True}],
                              {"tools": ["get_statistics"], "max_calls": 2})
    assert errored["fail_fast"] and "get_statistics" in errored["missing"]
    # без max_calls бюджет не проверяется (раньше был фиктивный дефолт 99)
    many = S.score_tooluse([{"name": "get_statistics"}] * 10, {"tools": ["get_statistics"]})
    assert not many["over_budget"]


def test_tool_converters():
    direct = to_anthropic_tools(_DIRECT_TOOLS)
    names = [t["name"] for t in direct]
    assert names == sorted(names) and set(names) <= READ_ONLY_TOOLS
    assert all(t["name"].startswith(METRIKA_PREFIX) for t in to_metrika_anthropic_tools(_METRIKA_TOOLS))
    oa = to_openai_tools(_DIRECT_TOOLS)
    assert all(t["type"] == "function" and "parameters" in t["function"] for t in oa)


def test_resolver_shapes_and_numbers():
    s = FakeMCPSession("yandex_direct")
    camps = json.loads(asyncio.run(s.call_tool("list_campaigns", {})).content[0].text)["campaigns"]
    assert len(camps) == len(acc.CAMPAIGNS)
    rep = asyncio.run(s.call_tool("get_statistics", {"campaignIds": [12346]})).content[0].text
    assert "5150.00" in rep and "41200.00" in rep
    empty = asyncio.run(s.call_tool("get_statistics", {"campaignIds": [12347]})).content[0].text
    assert json.loads(empty)["rowsTotal"] == 0
    big = asyncio.run(s.call_tool("list_keywords", {"campaignIds": [12349]})).content[0].text
    assert len(big) > TOOL_RESULT_CHAR_BUDGET


def test_metrika_resolver():
    s = FakeMCPSession("yandex_metrika")
    assert "Оформление заказа" in asyncio.run(s.call_tool("list_goals", {})).content[0].text
    stats = json.loads(asyncio.run(s.call_tool("get_statistics", {})).content[0].text)
    assert acc.METRIKA_GOAL_REACHES in stats["totals"]


def test_cases_integrity():
    known = {t.name for t in _DIRECT_TOOLS} | {METRIKA_PREFIX + t.name for t in _METRIKA_TOOLS}
    seen = set()
    for c in CASES:
        assert c.id not in seen and c.turns and all(c.turns) and c.rubric.strip()
        seen.add(c.id)
        assert c.dimension in {"tool", "numeric", "edge"}
        for name in list(c.trace.get("tools", [])) + list(c.trace.get("allow", [])):
            assert name in known, f"{c.id}: {name}"
        for f in c.golden_facts:
            assert f.get("aliases") and (f.get("kind") == "absent" or "value" in f)
    assert any(c.turn_type == "multi" for c in CASES)
    assert any(c.trace.get("forbid_tools") for c in CASES)
    # алиас 'цел' запрещён: подстрочно матчит «в целом»
    for c in CASES:
        for f in c.golden_facts:
            assert "цел" not in [a.lower() for a in f["aliases"]], f"{c.id}: голый алиас 'цел'"


def test_golden_match_fixtures():
    assert acc.metrics(12346)["cpa"] == 5150.0
    assert acc.metrics(12345)["cpc"] == 60.0
    assert acc.metrics(12348)["cpa"] is None


def test_cost():
    done = {"input_tokens": 1000, "cache_read_tokens": 10000, "cache_write_tokens": 0, "tokens_out": 500}
    # GLM дешевле Claude по ставкам
    assert S.cost_from_done("glm-4.6", done) < S.cost_from_done("claude-sonnet-4-6", done)
    # кэш-чтение дешевле полного входа
    full = {"input_tokens": 11000, "cache_read_tokens": 0, "cache_write_tokens": 0, "tokens_out": 500}
    assert S.cost_from_done("claude-sonnet-4-6", done) < S.cost_from_done("claude-sonnet-4-6", full)
    # точные кэш-множители OpenAI: gpt-5 0.10×, gpt-4.1 0.25× (а не семейные 0.5×)
    cached_1m = {"input_tokens": 0, "cache_read_tokens": 1_000_000, "cache_write_tokens": 0, "tokens_out": 0}
    assert abs(S.cost_from_done("gpt-5", cached_1m) - 1.25 * 0.10) < 1e-9
    assert abs(S.cost_from_done("gpt-4.1", cached_1m) - 2.00 * 0.25) < 1e-9


def test_fixture_version():
    assert acc.FIXTURE_VERSION
