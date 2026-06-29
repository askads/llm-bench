"""Фундамент харнесса: allowlists, конверсия схем тулов, обрезка результатов, ставки,
бюджеты, системный промпт и retry-хелперы. Самодостаточно — без зависимостей от askads.

Эти куски вынесены из askads (app.engine.tools / truncate / prompt, app.config,
scripts.bench_models), чтобы репозиторий гонялся автономно против твоих MCP-серверов.
"""
from __future__ import annotations

import asyncio
import json

# --- Платформы (какой MCP-сервер и набор env) ---
PLATFORM_YANDEX_DIRECT = "yandex_direct"
PLATFORM_YANDEX_METRIKA = "yandex_metrika"
PLATFORM_VK = "vk"

# --- Read-only allowlist (Claude получает ТОЛЬКО не-мутирующие тулы) ---
_YANDEX_DIRECT_READ_ONLY = frozenset({
    "get_account_info", "get_quota", "get_regions", "get_dictionaries",
    "list_campaigns", "list_ad_groups", "list_ads", "list_keywords",
    "get_bid_modifiers", "get_statistics", "get_sitelinks", "get_callouts",
    "get_vcards", "get_ad_images", "get_ad_videos", "get_creatives",
})
_VK_ADS_READ_ONLY = frozenset({"get_user_info", "get_throttling", "list_ad_plans", "list_banners"})
METRIKA_READ_ONLY_TOOLS = frozenset({"list_counters", "list_goals", "get_statistics"})
METRIKA_PREFIX = "metrika_"  # снимает коллизию get_statistics Метрики с Директом
READ_ONLY_TOOLS = _YANDEX_DIRECT_READ_ONLY | _VK_ADS_READ_ONLY | METRIKA_READ_ONLY_TOOLS


def is_allowed(name: str) -> bool:
    return name in READ_ONLY_TOOLS


# --- Конверсия MCP Tool[] → формат провайдера (детерминированный порядок для prompt-кэша) ---
def to_anthropic_tools(mcp_tools, allow=READ_ONLY_TOOLS, prefix: str = "") -> list[dict]:
    allowed = sorted((t for t in mcp_tools if t.name in allow), key=lambda t: t.name)
    return [{"name": prefix + t.name, "description": t.description or "",
             "input_schema": t.inputSchema or {"type": "object", "properties": {}}} for t in allowed]


def to_metrika_anthropic_tools(mcp_tools) -> list[dict]:
    return to_anthropic_tools(mcp_tools, METRIKA_READ_ONLY_TOOLS, METRIKA_PREFIX)


def to_openai_tools(mcp_tools, allow=READ_ONLY_TOOLS, prefix: str = "") -> list[dict]:
    allowed = sorted((t for t in mcp_tools if t.name in allow), key=lambda t: t.name)
    return [{"type": "function", "function": {
        "name": prefix + t.name, "description": t.description or "",
        "parameters": t.inputSchema or {"type": "object", "properties": {}}}} for t in allowed]


# --- Бюджеты/дефолты движка ---
MAX_TOOL_ITERATIONS = 8
MAX_OUTPUT_TOKENS = 4096
TOOL_RESULT_CHAR_BUDGET = 48_000
TURN_TOOL_RESULTS_CHAR_BUDGET = 80_000
TURN_TOOL_RESULT_FLOOR_CHARS = 8_000

# --- Ставки моделей (USD/1M вход/выход) + множители кэша по семейству (ОЦЕНКИ, сверить) ---
MODEL_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
    "glm-4.6": (0.6, 2.2), "glm-4.7": (0.6, 2.2), "glm-5": (0.6, 2.2), "glm-4.5-air": (0.2, 1.1),
    "gpt-5": (1.25, 10.0), "gpt-4.1": (2.0, 8.0),
    "gemini-2.5-flash": (0.3, 2.5), "gemini-2.5-pro": (1.25, 10.0),
}
_DEFAULT_RATES = (3.0, 15.0)
CACHE_MULT = {"claude": (0.1, 1.25), "glm": (0.2, 1.0), "openai": (0.5, 1.0), "gemini": (0.25, 1.0)}
_DEFAULT_CACHE = (0.1, 1.25)


def family(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("glm"):
        return "glm"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("gemini"):
        return "gemini"
    return "claude"


# --- Системный промпт (домен: аналитик Яндекс Директа). Конфигурируемо — замени под свой кейс. ---
SYSTEM_PROMPT = (
    "Ты — аналитик контекстной рекламы Яндекс Директа. Помогаешь владельцу рекламного кабинета "
    "разбираться в его кампаниях и принимать решения.\n\n"
    "Правила:\n"
    "- Отвечай по-русски, по делу, структурировано (списки, при необходимости таблицы).\n"
    "- Опирайся ТОЛЬКО на данные, полученные через инструменты. Никогда не выдумывай цифры, "
    "идентификаторы, названия кампаний или ставки. Если данных не хватает — вызови нужный инструмент.\n"
    "- Инструменты доступны ТОЛЬКО на чтение. Ты не можешь менять кампании, ставки, бюджеты, "
    "статусы или объявления. Если пользователь просит что-то изменить — объясни, что сервис сейчас "
    "работает в режиме «только чтение», и предложи, что можно проанализировать.\n"
    "- Метрики (CTR, CPC, CPA, расход, доля показов и т.п.) бери из get_statistics и показывай, "
    "из каких чисел и как ты их посчитал.\n"
    "- Falsification-first: формулируй гипотезы и проверяй их данными, а не подтверждай желаемое. "
    "Рекомендации давай с оговорками и рисками — без оптимистичных обещаний и гарантий результата.\n"
    "- Если вопрос неоднозначный, уточни период, кампанию или метрику, но сначала постарайся "
    "получить разумный ответ из доступных данных.\n\n"
    "Как экономно запрашивать данные:\n"
    "- Предпочитай узкие периоды. Избегай `dateRangeType=ALL_TIME` — бери ограниченное окно "
    "(`LAST_7_DAYS`, `LAST_30_DAYS` или `CUSTOM_DATE` с конкретными датами).\n"
    "- Если инструмент вернул ошибку или пустой результат — прямо скажи, что данные получить не "
    "удалось, и предложи перепроверить фильтр/период. НЕ отвечай догадками без данных.\n"
    "- Пустой отчёт статистики (`rowsTotal: 0`, нулевые `totals`) означает «за этот срез данных нет», "
    "а НЕ «отчёт недоступен» или «нет прав». Так и скажи и предложи проверить `campaignId` и даты. "
    "НЕ выдумывай объяснений (тип кампании, автотаргетинг «скрывает» запросы, ограничения API).\n\n"
    "Веб-аналитика Метрики (только если в наборе есть инструменты с префиксом `metrika_`):\n"
    "- Порядок: `metrika_list_counters` → `metrika_list_goals` → `metrika_get_statistics`. "
    "`counterId` — счётчик Метрики, он НЕ равен `id` кампании Директа.\n"
    "- Зови Метрику, когда вопрос про конверсии на сайте, источники трафика или поведение."
)


# ============================ ОБРЕЗКА РЕЗУЛЬТАТОВ (L1) ============================
_NUDGE = "narrow the query (add a campaign filter or a shorter date range)"


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _tsv_marker(kept: int, total: int) -> str:
    return f"[truncated: returned {kept} of {total} rows, ~{max(total - kept, 0)} omitted — {_NUDGE}]"


def _truncate_tsv(text: str, budget: int) -> str:
    lines = text.split("\n")
    total = len(lines)
    room = max(budget - len(_tsv_marker(total, total)) - 1, 0)
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > room:
            break
        kept.append(line)
        used += len(line) + 1
    if len(kept) >= total:
        return text
    return "\n".join(kept) + "\n" + _tsv_marker(len(kept), total)


def _truncate_json_list(data: list, budget: int) -> str:
    total = len(data)
    used = len(_dumps({"truncated": True, "total": total, "returned": total, "note": _NUDGE, "data": []}))
    kept = []
    for item in data:
        if used + len(_dumps(item)) + 1 > budget:
            break
        kept.append(item)
        used += len(_dumps(item)) + 1
    if len(kept) >= total:
        return _dumps(data)
    return _dumps({"truncated": True, "total": total, "returned": len(kept), "note": _NUDGE, "data": kept})


def _truncate_json_dict(obj: dict, budget: int):
    array_key, best = None, -1
    for k, v in obj.items():
        if isinstance(v, list) and len(v) > best:
            array_key, best = k, len(v)
    if array_key is None:
        return None
    arr = obj[array_key]
    total = len(arr)
    scalars = {k: v for k, v in obj.items() if k != array_key}
    used = len(_dumps({**scalars, "truncated": True, "total": total, "returned": total, array_key: []}))
    kept = []
    for item in arr:
        if used + len(_dumps(item)) + 1 > budget:
            break
        kept.append(item)
        used += len(_dumps(item)) + 1
    if len(kept) >= total:
        return _dumps(obj)
    return _dumps({**scalars, "truncated": True, "total": total, "returned": len(kept), array_key: kept})


def _truncate_text(text: str, budget: int) -> str:
    marker = f"\n[truncated: {len(text)} chars → kept {budget}; {_NUDGE}]"
    return text[:max(budget - len(marker), 0)] + marker


def _truncate_to(text: str, budget: int) -> str:
    if budget <= 0 or len(text) <= budget:
        return text
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        try:
            data = json.loads(stripped)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, list):
            return _truncate_json_list(data, budget)
        if isinstance(data, dict):
            out = _truncate_json_dict(data, budget)
            if out is not None:
                return out
    if "\t" in text[:4000]:
        return _truncate_tsv(text, budget)
    return _truncate_text(text, budget)


def clamp_tool_result(text: str, *, name: str = "", budget_chars: int = TOOL_RESULT_CHAR_BUDGET) -> str:
    if budget_chars <= 0 or len(text) <= budget_chars:
        return text
    return _truncate_to(text, budget_chars)


def clamp_turn_results(results: list[dict], *, budget_chars: int, floor_chars: int) -> list[dict]:
    if budget_chars <= 0:
        return results

    def _content(r):
        c = r.get("content")
        return None if (not isinstance(c, str) or r.get("is_error")) else c

    sizes = [len(c) if (c := _content(r)) is not None else 0 for r in results]
    total = sum(sizes)
    if total <= budget_chars:
        return results
    excess = total - budget_chars
    reducible = [max(s - floor_chars, 0) if _content(r) is not None else 0 for s, r in zip(sizes, results)]
    pool = sum(reducible)
    if pool <= 0:
        return results
    frac = min(excess / pool, 1.0)
    out = []
    for i, r in enumerate(results):
        c = _content(r)
        if c is None or reducible[i] <= 0:
            out.append(r)
            continue
        target = max(sizes[i] - int(frac * reducible[i]), floor_chars)
        if target >= sizes[i]:
            out.append(r)
            continue
        nr = dict(r)
        nr["content"] = _truncate_to(c, target)
        out.append(nr)
    return out


# ============================ RETRY / JSON ============================
def is_overloaded(err) -> bool:
    s = str(err or "").lower()
    return any(k in s for k in ("529", "overloaded", "503", "rate_limit", "rate limit", "429", "internalservererror"))


async def retry_call(make_coro, attempts: int = 5, base: float = 2.0):
    """Экспоненциальный бэкофф на перегрузке (429/529/5xx)."""
    for i in range(attempts):
        try:
            return await make_coro()
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1 or not is_overloaded(e):
                raise
            await asyncio.sleep(base * (2 ** i))


def extract_json(text: str):
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1 or j < i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except (ValueError, TypeError):
        return None
