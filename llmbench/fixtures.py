"""Замороженный фейк-кабинет Яндекс Директа (+ Метрика) — детерминированные входы для
fixed-input model-бенча. Единый источник правды: golden_facts кейсов выводятся отсюда.

Формы tool_result приближены к боевому mcp-yandex-direct. Известный fidelity-gap (см. README):
get_statistics здесь отдаёт TSV С ЗАГОЛОВКОМ ради детерминизма (боевой бывает headerless).
"""
from __future__ import annotations

import json

FIXTURE_VERSION = "2026-06-29"
CURRENCY = "RUB"
STANDARD_WINDOW = "LAST_7_DAYS"

CAMPAIGNS: list[dict] = [
    {"Id": 12345, "Name": "Поиск-Москва", "Type": "TEXT_CAMPAIGN", "State": "ON",
     "Status": "ACCEPTED", "Currency": CURRENCY, "DailyBudget": 12000, "StartDate": "2026-01-15"},
    {"Id": 12346, "Name": "РСЯ-Россия", "Type": "TEXT_CAMPAIGN", "State": "ON",
     "Status": "ACCEPTED", "Currency": CURRENCY, "DailyBudget": 9000, "StartDate": "2026-02-01"},
    {"Id": 12347, "Name": "Поиск-Регионы", "Type": "TEXT_CAMPAIGN", "State": "ON",
     "Status": "ACCEPTED", "Currency": CURRENCY, "DailyBudget": 5000, "StartDate": "2026-05-20"},
    {"Id": 12348, "Name": "Бренд", "Type": "TEXT_CAMPAIGN", "State": "ON",
     "Status": "ACCEPTED", "Currency": CURRENCY, "DailyBudget": 3000, "StartDate": "2026-03-10"},
    {"Id": 12349, "Name": "Поиск-Семантика", "Type": "TEXT_CAMPAIGN", "State": "ON",
     "Status": "ACCEPTED", "Currency": CURRENCY, "DailyBudget": 15000, "StartDate": "2026-04-01"},
]

# conversions=None → цели не настроены (CPA называть нельзя); stats=None → пустой срез.
STATS: dict[int, dict | None] = {
    12345: {"Impressions": 40210, "Clicks": 980, "Cost": 58800.0, "Conversions": 49},
    12346: {"Impressions": 512300, "Clicks": 1230, "Cost": 41200.0, "Conversions": 8},
    12347: None,
    12348: {"Impressions": 22000, "Clicks": 1500, "Cost": 9000.0, "Conversions": None},
    12349: {"Impressions": 88000, "Clicks": 2100, "Cost": 73500.0, "Conversions": 35},
}


def metrics(campaign_id: int) -> dict:
    s = STATS.get(campaign_id)
    if not s:
        return {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0,
                "ctr_pct": 0.0, "cpc": 0.0, "cpa": None, "conv_rate_pct": None}
    impr, clicks, cost, conv = s["Impressions"], s["Clicks"], s["Cost"], s["Conversions"]
    return {
        "impressions": impr, "clicks": clicks, "cost": cost, "conversions": conv,
        "ctr_pct": (clicks / impr * 100) if impr else 0.0,
        "cpc": (cost / clicks) if clicks else 0.0,
        "cpa": (cost / conv) if conv else None,
        "conv_rate_pct": (conv / clicks * 100) if (conv and clicks) else None,
    }


def list_campaigns_result(ids: list[int] | None = None) -> str:
    rows = [c for c in CAMPAIGNS if (ids is None or c["Id"] in ids)]
    return json.dumps({"campaigns": rows}, ensure_ascii=False)


_STAT_COLUMNS = ["CampaignId", "CampaignName", "Impressions", "Clicks", "Cost",
                 "Ctr", "AvgCpc", "Conversions", "CostPerConversion"]


def _fmt(v) -> str:
    if v is None:
        return "--"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def empty_report_json(report_type: str = "CAMPAIGN_PERFORMANCE_REPORT") -> str:
    return json.dumps({"reportType": report_type, "rowsTotal": 0,
                       "totals": {"Impressions": 0, "Clicks": 0, "Cost": 0.0, "Conversions": 0},
                       "rows": []}, ensure_ascii=False)


def campaign_report_tsv(ids: list[int] | None = None) -> str:
    cids = ids if ids is not None else [c["Id"] for c in CAMPAIGNS]
    present = [cid for cid in cids if STATS.get(cid)]
    if not present:
        return empty_report_json()
    lines = ["\t".join(_STAT_COLUMNS)]
    for cid in present:
        m = metrics(cid)
        name = next((c["Name"] for c in CAMPAIGNS if c["Id"] == cid), str(cid))
        lines.append("\t".join(_fmt(x) for x in [
            cid, name, m["impressions"], m["clicks"], m["cost"],
            round(m["ctr_pct"], 2), round(m["cpc"], 2), m["conversions"],
            round(m["cpa"], 2) if m["cpa"] is not None else None]))
    return "\n".join(lines)


def keywords_big_result(campaign_id: int = 12349, n: int = 1600) -> str:
    """Большой list_keywords — намеренно > бюджета обрезки, пробивает clamp."""
    kws = [{"Id": 70000 + i, "Keyword": f"купить товар {i} в москве недорого",
            "CampaignId": campaign_id, "AdGroupId": 60000 + (i // 20),
            "Bid": 1500 + (i % 50) * 10, "State": "ON", "Status": "ACCEPTED"} for i in range(n)]
    return json.dumps({"keywords": kws}, ensure_ascii=False)


METRIKA_COUNTERS = [{"id": 99001, "name": "Магазин — основной", "site2": "shop.example.ru"}]
METRIKA_GOALS = [
    {"id": 5001, "name": "Оформление заказа", "type": "action", "IsRetargeting": 0},
    {"id": 5002, "name": "Добавление в корзину", "type": "action", "IsRetargeting": 0},
]


def metrika_counters_result() -> str:
    return json.dumps({"counters": METRIKA_COUNTERS}, ensure_ascii=False)


def metrika_goals_result() -> str:
    return json.dumps({"goals": METRIKA_GOALS}, ensure_ascii=False)


def metrika_stats_result() -> str:
    return json.dumps({"totals": [18450, 14200, 612, 3.32], "total_rows": 1, "sampled": False,
                       "sample_share": 1.0,
                       "data": [{"dimensions": [], "metrics": [18450, 14200, 612, 3.32]}]},
                      ensure_ascii=False)
