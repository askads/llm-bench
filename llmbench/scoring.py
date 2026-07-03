"""Ключевые метрики (Tool-Use, Numeric-Accuracy) + стоимость.

Numeric и Tool-Use считаются В КОДЕ (детерминированно), судьям не отдаются — LLM плох ровно
в той арифметике, в которой мы сомневаемся. Анкоринг защищает от «правильное число по
неправильной причине»: metric-алиас (что за метрика) обязателен всегда, entity-алиас
(какая кампания) — в кейсах, где в ответе фигурирует несколько кампаний.
"""
from __future__ import annotations

import re

from llmbench.core import (CACHE_MULT, MODEL_CACHE_MULT, MODEL_RATES, _DEFAULT_CACHE,
                           _DEFAULT_RATES, family)

# ======================= NUMERIC =======================
ANCHOR_WINDOW = 40
_ABS_FLOOR = 0.05
# Пробельные разделители тысяч: обычный пробел, NBSP, узкий NBSP, тонкий пробел —
# модели используют все четыре («58 800» ≠ два числа 58 и 800).
_SPACES = " \u00a0\u202f\u2009"  # \u00a0=NBSP, \u202f=узкий NBSP, \u2009=тонкий
_THOUSANDS_SPACE = rf"\d{{1,3}}(?:[{_SPACES}]\d{{3}})+"
_THOUSANDS_COMMA = r"\d{1,3}(?:,\d{3})+"  # US-стиль: 58,800 (группы строго по 3 цифры)
_INT = rf"(?:{_THOUSANDS_SPACE}|{_THOUSANDS_COMMA}|\d+)"
_NUM = rf"{_INT}(?:[.,]\d+)?"
_SCALE = r"(?:\s*(тыс\.?|тысяч[аи]?|млн\.?|миллион[аов]*|млрд\.?|миллиард[аов]*))?"
_TOKEN_RE = re.compile(_NUM + _SCALE, re.IGNORECASE)
_SCALE_FACTOR = {"тыс": 1e3, "тысяч": 1e3, "млн": 1e6, "миллион": 1e6, "млрд": 1e9, "миллиард": 1e9}
_US_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")

# Направленный absence-матчинг. Дистракторы — слова ДРУГИХ метрик: число после
# «CPA … расход» — это значение расхода, не CPA. Временные/структурные слова (период,
# неделя, дата, кампания) сюда НЕ входят: «CPA за неделю 180 ₽» — выдуманный CPA, а не
# контекст. Даты-значения отсекает _looks_like_date, «N конверсий» — _unit_after.
_GAP_AFTER = 30
_GAP_BEFORE = 14
_DISTRACTORS = ["расход", "потрач", "потрат", "клик", "показ", "ставк", "бюджет", "визит",
                "посещени", "пользовател", "ctr", "импресс", "цена клика", "cpc"]
# Число с такой единицей сразу после — не «значение CPA»: «(0 конверсий)», «5 дней».
_UNITS_AFTER = ["клик", "показ", "конверси", "визит", "пользовател", "объявлени",
                "%", "дн", "недел", "мес", "год", "₽/клик"]
# Классы единицы СРАЗУ после числа — определяют, к какой метрике число относится по своей
# собственной единице (а не по близости слова): «5150 ₽» money, «1530 кликов»/«12%» count.
_MONEY_UNITS = ["₽", "руб", "rub", "р.", "₸", "$"]
_COUNT_UNITS = ["клик", "показ", "impress", "конверси", "визит", "посещени", "пользовател",
                "объявлени", "достижени", "%", "дн", "недел", "мес", "год"]
# Явные маркеры идентификатора: число после них — не выдуманный CPA, а id (не absence-нарушение).
_ID_MARKERS = ["id", "№", "counter", "счётчик", "счетчик"]
# Реальная дата: ISO (2026-06-01) или DD.MM(.YYYY) / DD/MM. Диапазон «1500-2000» датой НЕ
# считается — одиночный дефис между многозначными числами это диапазон значений.
_DATE_TOKEN_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b")


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
        digits = re.sub(rf"[{_SPACES}]", "", core).strip()
        if _US_THOUSANDS_RE.fullmatch(digits):
            digits = digits.replace(",", "")  # 58,800 → 58800 (US-тысячи)
        else:
            digits = digits.replace(",", ".")  # 2,44 → 2.44 (русская десятичная)
        if not digits:
            continue
        try:
            value = float(digits) * _scale_of(scale_word)
        except ValueError:
            continue
        out.append({"value": value, "start": m.start(), "end": m.end(), "raw": m.group(0).strip()})
    return out


def _occurrences(low, alias):
    out, i = [], low.find(alias)
    while i != -1:
        out.append((i, i + len(alias)))
        i = low.find(alias, i + 1)
    return out


def _alias_near(low, span, aliases):
    a, b = span
    window = low[max(0, a - ANCHOR_WINDOW): b + ANCHOR_WINDOW]
    return any(al.lower() in window for al in aliases)


def _within(value, target, tol_rel):
    return abs(value - target) <= max(abs(target) * tol_rel, _ABS_FLOOR)


# ---- разбор markdown-таблиц (метрика в шапке столбца, кампания в строке) ----
def _line_bounds(low, pos):
    s = low.rfind("\n", 0, pos) + 1
    e = low.find("\n", pos)
    return s, (len(low) if e == -1 else e)


def _row_at(low, pos):
    s, e = _line_bounds(low, pos)
    return low[s:e], s


def _cell_index(row, col_in_row):
    return row.count("|", 0, col_in_row)


def _is_separator_row(row):
    core = row.replace("|", "").replace(":", "").replace("-", "").replace(" ", "")
    return "-" in row and core == ""


def _prev_rows(low, line_start):
    """Строки таблицы над строкой, начинающейся в line_start (ближайшая первой)."""
    end = line_start - 1  # позиция '\n' перед нашей строкой (или -1 для первой)
    while end > 0:
        s = low.rfind("\n", 0, end) + 1
        row = low[s:end]
        if "|" not in row:
            break
        yield row
        end = s - 1


def _header_cell(low, line_start, col):
    """Ячейка шапки таблицы в столбце col: строка НАД разделителем (|---|), иначе — верхняя
    не-разделительная строка блока (не промежуточные data-строки)."""
    rows = list(_prev_rows(low, line_start))  # ближайшая первой
    sep = next((i for i, r in enumerate(rows) if _is_separator_row(r)), None)
    if sep is not None and sep + 1 < len(rows):
        header = rows[sep + 1]
    else:
        header = next((r for r in rows if not _is_separator_row(r)), None)
    if header is None:
        return None
    cells = header.split("|")
    return cells[col] if col < len(cells) else None


def _alias_in_table(low, span, aliases):
    """Алиас привязан к числу таблицы: в самой строке (key-value / «CPA: 5150») ИЛИ в шапке
    столбца (транспонированная таблица — кампания/метрика в шапке столбца, а не в строке)."""
    row, s = _row_at(low, span[0])
    if any(al.lower() in row for al in aliases):
        return True
    hc = _header_cell(low, s, _cell_index(row, span[0] - s))
    return hc is not None and any(al.lower() in hc for al in aliases)


def _metric_in_column(low, span, metric):
    if not _in_table_row(low, span[0]):
        return False
    return _alias_in_table(low, span, metric)


def _unit_class_after(low, n_end):
    """Класс единицы сразу после числа: 'money' (₽/руб), 'count' (клики/показы/%…), None."""
    tail = low[n_end:n_end + 16].lstrip(_SPACES + " -–—():")
    if any(tail.startswith(u) for u in _MONEY_UNITS):
        return "money"
    if any(tail.startswith(u) for u in _COUNT_UNITS):
        return "count"
    return None


def _wrong_unit(low, n, value_kind):
    """Единица числа противоречит метрике факта: денежной метрике чужды count-числа
    («1530 кликов», «12%»), count-метрике (метрика Метрики) — денежные. Так число уходит
    к своей метрике по СВОЕЙ единице, а не по близости слова."""
    cls = _unit_class_after(low, n["end"])
    if value_kind == "count":
        return cls == "money"
    return cls == "count"  # money по умолчанию


def _nearest_metric_own(low, span, metric, others):
    """Проза: метка метрики стоит ПЕРЕД своим значением («CPA 1200», «расход 58 800»),
    поэтому число принадлежит ближайшей метке СЛЕВА; правые метки метят следующее число и
    учитываются, только если слева метки нет. Число отвергается лишь при ближайшей ЧУЖОЙ
    метке; если ближайшая — наша или метки рядом нет (список/сравнение называет метрику один
    раз), принимается — коллизий значений между метриками нет (гарантирует фикстура)."""
    left = right = None  # (dist, own) ближайших меток слева/справа в окне
    for own, aliases in ((True, metric), (False, others)):
        for al in aliases:
            for (s, e) in _occurrences(low, al.lower()):
                if e <= span[0]:
                    d = span[0] - e
                    if d <= ANCHOR_WINDOW and (left is None or d < left[0]):
                        left = (d, own)
                elif s >= span[1]:
                    d = s - span[1]
                    if d <= ANCHOR_WINDOW and (right is None or d < right[0]):
                        right = (d, own)
    if left is not None:
        return left[1]
    if right is not None:
        return right[1]
    return True  # метки рядом нет → чужая метрика не мешает


def _metric_ok(low, span, metric, others=()):
    # таблица: metric-алиас в строке/шапке столбца; проза: метка своей метрики есть в тексте
    # (список/сравнение называют её один раз) И к числу не ближе метка чужой метрики
    if _in_table_row(low, span[0]):
        return _metric_in_column(low, span, metric)
    if not any(al.lower() in low for al in metric):
        return False
    return _nearest_metric_own(low, span, metric, others)


def _in_table_row(low, pos):
    # строка markdown-таблицы начинается с «|» (лидирующая труба) — так одиночный «|» в
    # прозе не путается с таблицей
    return _row_at(low, pos)[0].lstrip().startswith("|")


def _entity_mentions(low, own, rivals):
    """Упоминания кампаний (свои own=True + соперники own=False) в порядке текста,
    перекрывающиеся («поиск-москва» ⊃ «москва») схлопнуты в одно."""
    ms = []
    for own_flag, aliases in ((True, own), (False, rivals)):
        for al in aliases:
            for (s, e) in _occurrences(low, al.lower()):
                ms.append((s, e, own_flag))
    ms.sort()
    merged = []
    for s, e, own_flag in ms:
        if merged and s < merged[-1][1]:
            ps, pe, pflag = merged[-1]
            merged[-1] = (ps, max(pe, e), pflag or own_flag)
        else:
            merged.append((s, e, own_flag))
    return merged


def check_presence(text, fact, rival_entities=(), other_metrics=(), sibling_values=()) -> bool:
    """Число зачитывается, только если metric-анкор ок (ближайшая метка метрики — наша в
    прозе / шапка столбца в таблице) И (для мультикампейн-кейсов) оно атрибутировано нужной
    кампании. Атрибуция кампании: в таблице — по строке; в прозе — позиционным
    сопоставлением, но ТОЛЬКО среди чисел, чьё значение соответствует одной из целевых
    метрик (`sibling_values`) — так посторонние числа («в 4 раза», «топ-3») не сдвигают
    выравнивание. Верно и для «Москва vs РСЯ: 1200 vs 5150», и для перестановки (отвергает)."""
    low, nums = text.lower(), parse_numbers(text)
    metric, entity = fact["aliases"], fact.get("entity") or []
    tol, kind = fact.get("tolerance", 0.05), fact.get("value_kind", "money")
    pool = list(sibling_values) or [fact["value"]]
    metric_nums = [n for n in nums
                   if not _wrong_unit(low, n, kind)
                   and _metric_ok(low, (n["start"], n["end"]), metric, other_metrics)]
    if not entity:
        return any(_within(n["value"], fact["value"], tol) for n in metric_nums)

    def _in_pool(n):
        return any(_within(n["value"], v, tol) for v in pool)

    mentions = _entity_mentions(low, entity, rival_entities)
    prose_nums = [n for n in metric_nums if not _in_table_row(low, n["start"]) and _in_pool(n)]
    prose_mentions = [m for m in mentions if not _in_table_row(low, m[0])]
    for n in metric_nums:
        if not _within(n["value"], fact["value"], tol):
            continue
        if _in_table_row(low, n["start"]):
            # кампания в строке числа ИЛИ в шапке его столбца (транспонированная таблица)
            if _alias_in_table(low, (n["start"], n["end"]), entity):
                return True
        elif _in_pool(n):
            i = prose_nums.index(n)
            if i < len(prose_mentions) and prose_mentions[i][2]:
                return True
    return False


def _is_suffix_alias(alias):
    a = alias.strip().lower()
    return a.startswith("за ") or a.startswith("/")


def _clean_gap(low, a, b):
    return not any(d in low[a:b] for d in _DISTRACTORS)


def _unit_after(low, n_end):
    tail = low[n_end:n_end + 16].lstrip(_SPACES + "-–—")
    return any(tail.startswith(u) for u in _UNITS_AFTER)


def _looks_like_id(low, n):
    """Число — идентификатор (id кампании/счётчика), а не выдуманный CPA: целое без денежной
    единицы после и с явным id-маркером слева («id 12348», «счётчик 99001»). «180 ₽» после
    «кампании» не id (есть ₽), поэтому выдуманный CPA по-прежнему ловится."""
    if "." in n["raw"] or "," in n["raw"]:
        return False
    after = low[n["end"]:n["end"] + 8]
    if "₽" in after or "руб" in after:
        return False
    before = low[max(0, n["start"] - 12):n["start"]]
    return any(m in before for m in _ID_MARKERS)


def _looks_like_date(low, n_start, n_end):
    """Число — часть реальной даты (внутри ISO/DD.MM-токена), а не диапазона значений."""
    for m in _DATE_TOKEN_RE.finditer(low):
        if m.start() <= n_start and n_end <= m.end():
            return True
    return False


def check_absence_violation(text, fact) -> bool:
    """Нарушение: у CPA-алиаса стоит число в позиции значения (модель выдумала).
    Числа с единицей после («0 конверсий»), даты и куски дат нарушением не считаются."""
    low, nums = text.lower(), parse_numbers(text)
    plausible = [n for n in nums
                 if not _unit_after(low, n["end"]) and not _looks_like_date(low, n["start"], n["end"])
                 and not _looks_like_id(low, n)]
    for alias in fact["aliases"]:
        for (a_start, a_end) in _occurrences(low, alias.lower()):
            if _is_suffix_alias(alias):
                if any(a_start - _GAP_BEFORE <= n["end"] <= a_start and _clean_gap(low, n["end"], a_start)
                       for n in plausible):
                    return True
            elif any(a_end <= n["start"] <= a_end + _GAP_AFTER and _clean_gap(low, a_end, n["start"])
                     for n in plausible):
                return True
    return False


def _rivals_of(fact, golden_facts):
    own = {a.lower() for a in (fact.get("entity") or [])}
    return sorted({a for g in golden_facts
                   for a in (g.get("entity") or []) if a.lower() not in own})


def _other_metrics_of(fact, golden_facts):
    """Metric-метки ДРУГИХ метрик (не этой) — для развода «расход 58 800» vs «CPA 1200».
    Поддерживающие числа (клики/показы/%) отсекаются раньше по СВОЕЙ единице (_wrong_unit),
    поэтому их маркеры сюда не нужны — конкурируют только настоящие метки метрик."""
    own = {a.lower() for a in fact["aliases"]}
    return sorted({a for g in golden_facts if g.get("kind") != "absent"
                   for a in g["aliases"] if a.lower() not in own})


def _sibling_values_of(fact, golden_facts):
    """Значения фактов ТОЙ ЖЕ метрики (те же aliases) с entity — пул целевых значений для
    позиционного сопоставления (сравнивают именно эти кампании по этой метрике)."""
    own = {a.lower() for a in fact["aliases"]}
    return [g["value"] for g in golden_facts
            if g.get("kind") != "absent" and g.get("entity")
            and {a.lower() for a in g["aliases"]} == own]


def score_numeric(text, golden_facts) -> dict:
    presence = [f for f in golden_facts if f.get("kind") != "absent"]
    absence = [f for f in golden_facts if f.get("kind") == "absent"]
    required = [f for f in presence if f.get("required", True)]
    violations = [f["key"] for f in absence if check_absence_violation(text, f)]
    req_ok = [f["key"] for f in required
              if check_presence(text, f, _rivals_of(f, golden_facts),
                                _other_metrics_of(f, golden_facts), _sibling_values_of(f, golden_facts))]
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


def _ok_names(trace):
    return [t.get("name", "") for t in (trace or []) if not t.get("is_error")]


def _is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(any(x == n for x in it) for n in needle)


def score_tooluse(trace, spec) -> dict:
    actual = _names(trace)
    if spec.get("forbid_tools"):
        ok = len(actual) == 0
        return {"score": 5.0 if ok else 0.0, "fail_fast": not ok, "missing": [],
                "extra": actual, "over_budget": False, "order_ok": True, "actual": actual}
    ok_calls = _ok_names(trace)  # required-тул засчитан, только если вызов НЕ упал
    required = list(spec.get("tools", []))
    allowed = set(required) | set(spec.get("allow", []))
    present = [t for t in required if t in ok_calls]
    missing = [t for t in required if t not in ok_calls]
    extra = [t for t in actual if t not in allowed]
    max_calls = spec.get("max_calls")
    over_budget = max_calls is not None and len(actual) > max_calls
    order_ok = (not spec.get("ordered", False)) or _is_subsequence(required, ok_calls)
    score = 5.0 * (len(present) / len(required)) if required else 5.0
    score -= 1.0 * len(extra) + (1.0 if over_budget else 0) + (0 if order_ok else 1.0)
    return {"score": round(max(0.0, min(5.0, score)), 2), "fail_fast": bool(missing),
            "missing": missing, "extra": extra, "over_budget": over_budget,
            "order_ok": order_ok, "actual": actual}


# ======================= COST =======================
_warned_unknown_models: set[str] = set()


def _rates(model):
    rates = MODEL_RATES.get(model)
    if rates is None:
        if model not in _warned_unknown_models:
            _warned_unknown_models.add(model)
            print(f"[warn] нет тарифа для модели {model!r} — беру дефолт {_DEFAULT_RATES} "
                  f"USD/1M; добавь её в core.MODEL_RATES, иначе Cost/Pareto врут")
        return _DEFAULT_RATES
    return rates


def _cache_mult(model):
    return MODEL_CACHE_MULT.get(model) or CACHE_MULT.get(family(model), _DEFAULT_CACHE)


def cost_from_done(model: str, done: dict) -> float:
    """USD за прогон из РЕАЛЬНЫХ токенов done с провайдер-специфичным кэшом."""
    in_rate, out_rate = _rates(model)
    read_mult, write_mult = _cache_mult(model)
    inp = done.get("input_tokens", 0) or 0
    cr = done.get("cache_read_tokens", 0) or 0
    cw = done.get("cache_write_tokens", 0) or 0
    out = done.get("tokens_out", 0) or 0
    return ((inp + cr * read_mult + cw * write_mult) * in_rate + out * out_rate) / 1_000_000


# Правило решения / вердикт (SWITCH/STAY vs baseline) убраны намеренно: отчёт — чисто
# сравнительный (сводная таблица + Pareto-фронт), без рекомендации «перейти/остаться».
