"""Кейсы бенчмарка: вопрос/turns + trace-спека + golden_facts + рубрика.

golden_facts выводятся из fixtures.metrics() — единый источник правды с тем, что видит модель.
Покрытие: tool-use (многошаг/мультитёрн/Метрика), numeric (точность + absence «не выдумывать
CPA»), edge (пустой срез, отказ менять ставку, уточнение, clamp-robustness).

Анкоринг фактов: `aliases` — metric-алиасы (обязательны всегда); `entity` — алиасы кампании,
обязательны в кейсах, где в ответе фигурирует несколько кампаний (иначе число одной кампании
зачтётся другой). В однокампейн-кейсах entity не требуем: вопрос уже фиксирует кампанию, а
модель не обязана повторять её название рядом с каждым числом.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from llmbench import fixtures as acc
from llmbench.core import PLATFORM_YANDEX_DIRECT

_M = {cid: acc.metrics(cid) for cid in (12345, 12346, 12348)}


@dataclass
class BenchCase:
    id: str
    dimension: str
    rubric: str
    turns: list[str]
    trace: dict
    golden_facts: list[dict] = field(default_factory=list)
    platform: str = PLATFORM_YANDEX_DIRECT
    metrika_enabled: bool = False

    @property
    def turn_type(self) -> str:
        return "multi" if len(self.turns) > 1 else "single"


def _fact(key, value, aliases, *, entity=None, tol=0.05, required=True, value_kind="money"):
    # value_kind: 'money' (₽-метрика: CPA/расход/CPC) или 'count' (счётная: достижения цели).
    # Определяет, по какой единице числа отбираются в набор метрики (см. scoring._wrong_unit).
    return {"key": key, "value": value, "tolerance": tol, "required": required,
            "aliases": aliases, "entity": entity, "value_kind": value_kind}


def _absent(key, aliases):
    return {"key": key, "kind": "absent", "aliases": aliases}


_RSYA = ["рся", "rsya"]
_MSK = ["москва", "поиск-москва"]

CASES: list[BenchCase] = [
    BenchCase(
        id="tool_multi_step", dimension="tool",
        rubric="Через инструменты получить статистику и назвать РСЯ-Россия как перерасходчика "
               "(очень высокий CPA) и Поиск-Москва как эффективную (низкий CPA), опираясь на числа.",
        turns=["Сравни мои кампании по эффективности за последнюю неделю: где сливается бюджет, "
               "а где отдача лучше? Возьми данные через инструменты."],
        trace={"tools": ["get_statistics"], "allow": ["list_campaigns", "get_account_info"], "max_calls": 4},
        # В ответе несколько кампаний → у каждого факта обязателен entity-якорь.
        golden_facts=[_fact("cpa_rsya", _M[12346]["cpa"], ["CPA", "стоимость конверси", "цена конверси"],
                            entity=_RSYA, tol=0.05),
                      _fact("cpa_poisk", _M[12345]["cpa"], ["CPA", "стоимость конверси", "цена конверси"],
                            entity=_MSK, tol=0.05),
                      _fact("cost_rsya", _M[12346]["cost"], ["расход", "потрат", "потрач"],
                            entity=_RSYA, tol=0.02, required=False),
                      _fact("cost_poisk", _M[12345]["cost"], ["расход", "потрат", "потрач"],
                            entity=_MSK, tol=0.02, required=False)]),
    BenchCase(
        id="multi_turn_rsya", dimension="tool",
        rubric="Во втором ответе назвать расход и CPA кампании РСЯ-Россия из данных; числа корректны.",
        turns=["Какие у меня есть кампании?",
               "Покажи по РСЯ-Россия за последнюю неделю: сколько потратили и какой CPA?"],
        trace={"tools": ["get_statistics"], "allow": ["list_campaigns"], "max_calls": 4},
        golden_facts=[_fact("cost_rsya", _M[12346]["cost"], ["расход", "потрат", "потрач"], tol=0.02),
                      _fact("cpa_rsya", _M[12346]["cpa"], ["CPA", "стоимость конверси"], tol=0.05)]),
    BenchCase(
        id="numeric_cpc_poisk", dimension="numeric",
        rubric="Назвать расход и среднюю цену клика (CPC) Поиск-Москва из данных; числа корректны.",
        turns=["Сколько я потратил на Поиск-Москва за последнюю неделю и какая средняя цена клика?"],
        trace={"tools": ["get_statistics"], "allow": ["list_campaigns"], "max_calls": 3},
        golden_facts=[_fact("cost_poisk", _M[12345]["cost"], ["расход", "потрат", "потрач"], tol=0.02),
                      _fact("cpc_poisk", _M[12345]["cpc"], ["клик", "CPC", "цена клика"], tol=0.05)]),
    BenchCase(
        id="empty_period", dimension="edge",
        rubric="Отчёт пуст (0 показов/кликов). Прямо сказать, что за период по Поиск-Регионы данных "
               "нет, предложить проверить даты/кампанию. НЕ выдумывать причины и цифры.",
        turns=["Покажи статистику по кампании Поиск-Регионы за последнюю неделю."],
        trace={"tools": ["get_statistics"], "allow": ["list_campaigns"], "max_calls": 3}),
    BenchCase(
        id="refuse_change_bid", dimension="edge",
        rubric="Просьба ИЗМЕНИТЬ ставку. Сервис только на чтение — объяснить, что менять нельзя, и "
               "предложить, что можно проанализировать. Тулы для смены не вызывать.",
        turns=["Подними ставку на кампании Поиск-Москва до 80 рублей."],
        trace={"forbid_tools": True, "max_calls": 0}),
    BenchCase(
        id="ambiguous_clarify", dimension="edge",
        rubric="Вопрос без контекста и референта. Задать уточняющий вопрос (какая кампания/метрика/"
               "период), а не лезть за данными вслепую и не выдумывать ответ.",
        turns=["Слушай, это вообще нормально?"],
        trace={"forbid_tools": True, "max_calls": 0}),
    BenchCase(
        id="cpa_not_configured", dimension="numeric",
        rubric="По кампании Бренд конверсии не настроены (Conversions='--'). Сказать, что CPA посчитать "
               "нельзя, предложить настроить цели. Может назвать расход и CPC. НЕ выдумывать CPA.",
        turns=["Какая стоимость конверсии (CPA) по кампании Бренд за последнюю неделю?"],
        trace={"tools": ["get_statistics"], "allow": ["list_campaigns"], "max_calls": 3},
        golden_facts=[_absent("cpa_brand", ["CPA", "стоимость конверси", "цена конверси",
                                            "стоимость одной конверси", "цена одной конверси",
                                            "конверсия обходится", "обходится в", "за конверси"]),
                      _fact("cost_brand", _M[12348]["cost"], ["расход", "потрат", "потрач"],
                            tol=0.02, required=False),
                      _fact("cpc_brand", _M[12348]["cpc"], ["клик", "CPC"], tol=0.05, required=False)]),
    BenchCase(
        id="clamp_robustness", dimension="edge",
        rubric="Ключевых слов очень много, результат обрезан. Корректно отработать неполные данные "
               "(сказать, что список большой/показан частично, или сузить запрос), не выдумывать итоги.",
        turns=["В кампании Поиск-Семантика очень много ключевых слов — какие самые дорогие по ставке?"],
        trace={"tools": ["list_keywords"], "allow": ["list_campaigns", "get_statistics"], "max_calls": 4}),
    BenchCase(
        id="metrika_conversions", dimension="tool", metrika_enabled=True,
        rubric="Вопрос про конверсии цели на сайте (Метрика). Пройти metrika_list_counters → "
               "metrika_list_goals → metrika_get_statistics и назвать число достижений «Оформление заказа».",
        turns=["Сколько конверсий «Оформление заказа» принёс сайт за последнюю неделю по Метрике?"],
        trace={"tools": ["metrika_list_counters", "metrika_list_goals", "metrika_get_statistics"],
               "ordered": True, "max_calls": 5},
        # 'цел' убран: подстрочно матчил «в целом»; берём формы, не встречающиеся в других словах.
        # value_kind=count: 612 — число достижений цели, единица не денежная.
        golden_facts=[_fact("goal_reaches", acc.METRIKA_GOAL_REACHES,
                            ["Оформление заказа", "конверси", "достижени", "цель", "цели", "целей"],
                            tol=0.02, value_kind="count")]),
]


def by_id(case_id):
    return next((c for c in CASES if c.id == case_id), None)
