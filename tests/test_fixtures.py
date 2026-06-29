"""Офлайн self-test (БЕЗ сети/денег) — единственное, что собирает CI.

Проверяет фундамент дорогого прогона: парсер/матчер чисел (самое хрупкое), заглушку MCP
(формы tool_result + совместимость с конвертерами), целостность кейсов, стоимость, правило.
"""
import asyncio
import json

from llmbench import fixtures as acc
from llmbench import scoring as S
from llmbench.cases import CASES
from llmbench.core import (METRIKA_PREFIX, READ_ONLY_TOOLS, TOOL_RESULT_CHAR_BUDGET,
                           to_anthropic_tools, to_metrika_anthropic_tools, to_openai_tools)
from llmbench.mcp import _DIRECT_TOOLS, _METRIKA_TOOLS, FakeMCPSession


def test_parse_russian_number_formats():
    cases = {"расход 58 800 ₽": 58800.0, "CPA 1200 ₽": 1200.0, "CTR 2,44%": 2.44,
             "примерно 1,2 тыс ₽": 1200.0, "3,4 млн показов": 3_400_000.0, "1234.56": 1234.56,
             "цена клика 60 ₽": 60.0, "512 300 показов": 512300.0}
    for text, expected in cases.items():
        nums = S.parse_numbers(text)
        assert any(abs(n["value"] - expected) < 0.01 for n in nums), f"{text!r} -> {[n['value'] for n in nums]}"


def test_anchoring_and_absence():
    ans = "Поиск-Москва: расход 58 800 ₽, CPA 1200 ₽. РСЯ-Россия: расход 41 200 ₽, CPA 5150 ₽."
    assert S.check_presence(ans, {"value": 5150, "tolerance": 0.05, "aliases": ["РСЯ", "CPA"]})
    assert not S.check_presence(ans, {"value": 9999, "tolerance": 0.02, "aliases": ["расход"]})
    fact = {"kind": "absent", "key": "cpa", "aliases": ["CPA", "стоимость конверси"]}
    assert not S.check_absence_violation("Конверсии не настроены, CPA посчитать нельзя.", fact)
    assert S.check_absence_violation("CPA составляет 180 ₽.", fact)
    assert not S.check_absence_violation("Конверсий 0, поэтому стоимость конверсии не определить.", fact)


def test_score_numeric():
    facts = [{"key": "a", "value": 100, "tolerance": 0.02, "required": True, "aliases": ["расход"]},
             {"key": "b", "value": 50, "tolerance": 0.02, "required": True, "aliases": ["клик", "CPC"]}]
    assert S.score_numeric("расход 100 ₽, CPC 50 ₽", facts)["score"] == 5.0
    assert S.score_numeric("расход 100 ₽", facts)["score"] == 2.5
    av = [{"kind": "absent", "key": "x", "aliases": ["CPA"]}]
    assert S.score_numeric("CPA 5 ₽", av)["score"] == 0.0
    assert S.score_numeric("CPA не посчитать", av)["score"] == 5.0


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
    assert 612 in json.loads(asyncio.run(s.call_tool("get_statistics", {})).content[0].text)["totals"]


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


def test_golden_match_fixtures():
    assert acc.metrics(12346)["cpa"] == 5150.0
    assert acc.metrics(12345)["cpc"] == 60.0
    assert acc.metrics(12348)["cpa"] is None


def test_cost_and_decision():
    done = {"input_tokens": 1000, "cache_read_tokens": 10000, "cache_write_tokens": 0, "tokens_out": 500}
    assert S.cost_from_done("glm-4.6", done) < S.cost_from_done("claude-sonnet-4-6", done)
    good = {"numeric": 5.0, "tool": 5.0, "edge": 4.5, "score_per_dollar": {"single": 100, "multi": 200}}
    base = {"numeric": 5.0, "tool": 5.0, "edge": 4.8, "score_per_dollar": {"single": 30, "multi": 40}}
    assert S.decide(good, base)["verdict"] == "SWITCH"
    bad = {"numeric": 2.0, "tool": 5.0, "edge": 4.5, "score_per_dollar": {"single": 100, "multi": 200}}
    assert S.decide(bad, base)["verdict"] == "STAY"


def test_fixture_version():
    assert acc.FIXTURE_VERSION
