"""Кодовые метрики (Tool-Use, Numeric-Accuracy) + стоимость + правило решения.

Numeric и Tool-Use считаются В КОДЕ (детерминированно), судьям не отдаются — LLM плох ровно
в той арифметике, в которой мы сомневаемся. Анкоринг по aliases защищает от «правильное число
по неправильной причине».
"""
from __future__ import annotations

import re

from llmbench.core import CACHE_MULT, MODEL_RATES, _DEFAULT_CACHE, _DEFAULT_RATES, family

# ======================= NUMERIC =======================
ANCHOR_WINDOW = 40
_ABS_FLOOR = 0.05
_SPACES = "   "
_THOUSANDS = rf"\d{{1,3}}(?:[{_SPACES}]\d{{3}})+"
_INT = rf"(?:{_THOUSANDS}|\d+)"
_NUM = rf"{_INT}(?:[.,]\d+)?"
_SCALE = r"(?:\s*(тыс\.?|тысяч[аи]?|млн\.?|миллион[аов]*|млрд\.?|миллиард[аов]*))?"
_TOKEN_RE = re.compile(_NUM + _SCALE, re.IGNORECASE)
_SCALE_FACTOR = {"тыс": 1e3, "тысяч": 1e3, "млн": 1e6, "миллион": 1e6, "млрд": 1e9, "миллиард": 1e9}

# Направленный absence-матчинг.
_GAP_AFTER = 25
_GAP_BEFORE = 14
_DISTRACTORS = ["расход", "потрач", "клик", "показ", "ставк", "бюджет", "визит",
                "посещени", "пользовател", "ctr", "импресс", "цена клика"]


def _scale_of(word):
    if not word:
        return 1.0
    w = word.lower().rstrip(".")
    for key, factor in _SCALE_FACTOR.items():
        if w.startswith(key):
            return factor
    return 1.0


def parse_numbers(text: str) -> list[dict]:
    out = []
    for m in _TOKEN_RE.finditer(text or ""):
        core, scale_word = m.group(0), m.group(1)
        if scale_word:
            core = core[: core.lower().rfind(scale_word.lower())]
        digits = re.sub(rf"[{_SPACES}]", "", core).strip().replace(",", ".")
        if not digits:
            continue
        try:
            value = float(digits) * _scale_of(scale_word)
        except ValueError:
            continue
        out.append({"value": value, "start": m.start(), "end": m.end(), "raw": m.group(0).strip()})
    return out


def _alias_near(low, span, aliases):
    a, b = span
    window = low[max(0, a - ANCHOR_WINDOW): b + ANCHOR_WINDOW]
    return any(al.lower() in window for al in aliases)


def _within(value, target, tol_rel):
    return abs(value - target) <= max(abs(target) * tol_rel, _ABS_FLOOR)


def check_presence(text, fact) -> bool:
    low, nums = text.lower(), parse_numbers(text)
    return any(_within(n["value"], fact["value"], fact.get("tolerance", 0.05))
               and _alias_near(low, (n["start"], n["end"]), fact["aliases"]) for n in nums)


def _occurrences(low, alias):
    out, i = [], low.find(alias)
    while i != -1:
        out.append((i, i + len(alias)))
        i = low.find(alias, i + 1)
    return out


def _is_suffix_alias(alias):
    a = alias.strip().lower()
    return a.startswith("за ") or a.startswith("/")


def _clean_gap(low, a, b):
    return not any(d in low[a:b] for d in _DISTRACTORS)


def check_absence_violation(text, fact) -> bool:
    """Нарушение: у CPA-алиаса стоит ЛЮБОЕ число в позиции значения (модель выдумала)."""
    low, nums = text.lower(), parse_numbers(text)
    for alias in fact["aliases"]:
        for (a_start, a_end) in _occurrences(low, alias.lower()):
            if _is_suffix_alias(alias):
                if any(a_start - _GAP_BEFORE <= n["end"] <= a_start and _clean_gap(low, n["end"], a_start)
                       for n in nums):
                    return True
            elif any(a_end <= n["start"] <= a_end + _GAP_AFTER and _clean_gap(low, a_end, n["start"])
                     for n in nums):
                return True
    return False


def score_numeric(text, golden_facts) -> dict:
    presence = [f for f in golden_facts if f.get("kind") != "absent"]
    absence = [f for f in golden_facts if f.get("kind") == "absent"]
    required = [f for f in presence if f.get("required", True)]
    violations = [f["key"] for f in absence if check_absence_violation(text, f)]
    req_ok = [f["key"] for f in required if check_presence(text, f)]
    if violations:
        score = 0.0
    elif not required:
        score = 5.0
    else:
        score = 5.0 * len(req_ok) / len(required)
    return {"score": round(score, 2), "required_total": len(required), "required_ok": req_ok,
            "required_missing": [f["key"] for f in required if f["key"] not in req_ok],
            "absence_violations": violations}


# ======================= TOOL-USE =======================
def _names(trace):
    return [t.get("name", "") for t in (trace or [])]


def _is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(any(x == n for x in it) for n in needle)


def score_tooluse(trace, spec) -> dict:
    actual = _names(trace)
    if spec.get("forbid_tools"):
        ok = len(actual) == 0
        return {"score": 5.0 if ok else 0.0, "fail_fast": not ok, "missing": [],
                "extra": actual, "over_budget": False, "order_ok": True, "actual": actual}
    required = list(spec.get("tools", []))
    allowed = set(required) | set(spec.get("allow", []))
    present = [t for t in required if t in actual]
    missing = [t for t in required if t not in actual]
    extra = [t for t in actual if t not in allowed]
    over_budget = len(actual) > spec.get("max_calls", 99)
    order_ok = (not spec.get("ordered", False)) or _is_subsequence(required, actual)
    score = 5.0 * (len(present) / len(required)) if required else 5.0
    score -= 1.0 * len(extra) + (1.0 if over_budget else 0) + (0 if order_ok else 1.0)
    return {"score": round(max(0.0, min(5.0, score)), 2), "fail_fast": bool(missing),
            "missing": missing, "extra": extra, "over_budget": over_budget,
            "order_ok": order_ok, "actual": actual}


# ======================= COST =======================
def _rates(model):
    return MODEL_RATES.get(model, _DEFAULT_RATES)


def cost_from_done(model: str, done: dict) -> float:
    """USD за прогон из РЕАЛЬНЫХ токенов done с провайдер-специфичным кэшом."""
    in_rate, out_rate = _rates(model)
    read_mult, write_mult = CACHE_MULT.get(family(model), _DEFAULT_CACHE)
    inp = done.get("input_tokens", 0) or 0
    cr = done.get("cache_read_tokens", 0) or 0
    cw = done.get("cache_write_tokens", 0) or 0
    out = done.get("tokens_out", 0) or 0
    return ((inp + cr * read_mult + cw * write_mult) * in_rate + out * out_rate) / 1_000_000


# Правило решения / вердикт (SWITCH/STAY vs baseline) убраны намеренно: отчёт — чисто
# сравнительный (сводная таблица + Pareto-фронт), без рекомендации «перейти/остаться».
