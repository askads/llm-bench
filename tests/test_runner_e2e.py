"""Тесты конвейера ранера со заглушённым движком (без сети/денег).

Покрывают самые опасные для валидности места: отсутствие подмены конфига в ретрае (R1),
неутечку нонса (R7), строгие фильтры (R12), round-trip JSONL → отчёт (R13), учёт
стоимости упавших попыток (R28).
"""
import asyncio
import json

import pytest

from llmbench import runner
from llmbench.cases import by_id


def test_nonce_does_not_leak_case_or_config():
    n = runner._nonce("refuse_change_bid", "Sonnet adaptive/high", 0)
    assert "refuse" not in n and "adaptive" not in n and "Sonnet" not in n
    assert len(n) == 12 and n.isalnum()
    # детерминирован (кэш-стабильность между повторами прогона)
    assert n == runner._nonce("refuse_change_bid", "Sonnet adaptive/high", 0)
    assert n != runner._nonce("refuse_change_bid", "Sonnet adaptive/high", 1)


def test_filter_or_die_typo_exits(monkeypatch):
    with pytest.raises(SystemExit):
        runner._filter_or_die(runner.VARIANTS, ["nonexistent-typo"], "--variants", lambda v: v["label"])
    picked = runner._filter_or_die(runner.VARIANTS, ["GLM-4.6 disabled"], "--variants", lambda v: v["label"])
    assert len(picked) == 1 and picked[0]["label"] == "GLM-4.6 disabled"
    assert len(runner._filter_or_die(runner.VARIANTS, None, "--variants", lambda v: v["label"])) == len(runner.VARIANTS)


def test_retry_keeps_same_config_no_substitution(monkeypatch):
    """Ретрай упавшего прогона идёт ТЕМ ЖЕ конфигом — никакой подмены thinking/effort (R1)."""
    seen = []

    async def fake_anthropic(history, **kw):
        seen.append({"thinking": kw.get("thinking"), "effort": kw.get("effort")})
        if len(seen) == 1:
            return {"answer": "", "tool_trace": [], "input_tokens": 100, "cache_read_tokens": 0,
                    "cache_write_tokens": 0, "tokens_out": 10, "error": "APIError: 529"}
        return {"answer": "ok", "tool_trace": [], "input_tokens": 100, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "tokens_out": 10, "error": None}

    monkeypatch.setattr(runner, "run_anthropic", fake_anthropic)
    v = {"label": "Sonnet adaptive/low", "vendor": "anthropic", "engine": "anthropic",
         "model": "claude-sonnet-4-6", "base_url": "", "key_env": "ANTHROPIC_API_KEY",
         "thinking": "adaptive", "effort": "low", "reasoning_effort": None}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    case = by_id("numeric_cpc_poisk")
    res, wasted = asyncio.run(runner._run_case_retried(v, case, "nonce", "fixed", retries=1))
    assert res["error"] is None and res["answer"] == "ok"
    # обе попытки — adaptive/low, а НЕ disabled/high safe-mode
    assert all(s == {"thinking": "adaptive", "effort": "low"} for s in seen), seen
    # стоимость упавшей попытки не потеряна
    assert wasted > 0


def test_retried_flag_only_on_recovery(monkeypatch):
    """retried=True только когда финал УДАЛСЯ после повтора; дважды упавший — просто ошибка."""
    async def always_fail(history, **kw):
        return {"answer": "", "tool_trace": [], "input_tokens": 50, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "tokens_out": 5, "error": "APIError: 529"}

    monkeypatch.setattr(runner, "run_anthropic", always_fail)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    v = {"label": "X", "vendor": "anthropic", "engine": "anthropic", "model": "claude-sonnet-4-6",
         "base_url": "", "key_env": "ANTHROPIC_API_KEY", "thinking": "disabled", "effort": "high",
         "reasoning_effort": None}
    case = by_id("numeric_cpc_poisk")
    rr, wasted = asyncio.run(runner._run_case_retried(v, case, "n", "fixed", retries=1))
    rec = asyncio.run(runner._score(case, v, rr, {"anthropic"}, False, [], wasted))
    assert rec["error"] and rec["retried"] is False and wasted > 0  # ошибка, не «recovery»


def test_errored_run_scored_as_error_not_perfect(monkeypatch):
    """Ошибка API не должна давать forbid_tools-кейсу незаслуженные 5.0 по тулам (R3)."""
    async def fake_anthropic(history, **kw):
        return {"answer": "", "tool_trace": [], "input_tokens": 50, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "tokens_out": 0, "error": "APITimeoutError"}

    monkeypatch.setattr(runner, "run_anthropic", fake_anthropic)
    v = {"label": "X", "vendor": "anthropic", "engine": "anthropic", "model": "claude-sonnet-4-6",
         "base_url": "", "key_env": "ANTHROPIC_API_KEY", "thinking": "disabled", "effort": "high",
         "reasoning_effort": None}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    case = by_id("refuse_change_bid")  # forbid_tools
    rr, wasted = asyncio.run(runner._run_case_retried(v, case, "n", "fixed", retries=0))
    rec = asyncio.run(runner._score(case, v, rr, {"anthropic"}, False, [], wasted))
    assert rec["error"] and rec["tool"] is None and rec["composite"] is None


def test_jsonl_roundtrip_report_from(tmp_path, monkeypatch):
    """Отчёт пересобирается из JSONL без запусков (R13)."""
    jsonl = tmp_path / "runs.jsonl"
    meta = {"ts": "2026-07-03 12:00 UTC", "mode": "fixed", "repeat": 2, "n_cases": 1,
            "variants": [{"label": "V", "model": "claude-opus-4-8", "engine": "anthropic",
                          "vendor": "anthropic", "thinking": "adaptive", "effort": "high"}],
            "judges": ["Claude"], "neutral": [], "fixture_version": "2026-07-03",
            "git_commit": "abc1234", "jsonl": str(jsonl), "baseline_desc": None, "caveats": ["c"]}
    rec = {"case": "a", "dimension": "numeric", "turn_type": "single", "tool": 5.0, "numeric": 5.0,
           "has_golden": True, "soft_quality": 5.0, "soft_russian": 5.0, "cost": 0.01,
           "cost_wasted": 0.0, "retried": False, "error": None, "composite": 5.0}
    lines = [{"type": "meta", "meta": meta},
             {"type": "run", "variant": "V", "case": "a", "repeat": 0, "rec": rec,
              "answer": "...", "tool_trace": [], "usage": {}}]
    jsonl.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    out = tmp_path / "report.md"
    runner._report_from(str(jsonl), str(out))
    md = out.read_text(encoding="utf-8")
    assert "Opus 4.8" in md and "abc1234" in md
