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
# У adaptive thinking токены размышлений входят в max_tokens — thinking-вариантам нужен
# запас, иначе ответ обрезается и вариант получает незаслуженный ноль (см. REVIEW.md R11).
MAX_OUTPUT_TOKENS_THINKING = 16_000
TOOL_RESULT_CHAR_BUDGET = 48_000
TURN_TOOL_RESULTS_CHAR_BUDGET = 80_000
TURN_TOOL_RESULT_FLOOR_CHARS = 8_000
# Таймауты live-MCP: один зависший node-сервер не должен вешать весь грид (REVIEW.md R14).
MCP_INIT_TIMEOUT_S = 30
MCP_TOOL_CALL_TIMEOUT_S = 120

# --- Ставки моделей (USD/1M вход/выход) + множители кэша по семейству (ОЦЕНКИ, сверить) ---
MODEL_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
    "glm-4.6": (0.6, 2.2), "glm-4.7": (0.6, 2.2), "glm-5": (0.6, 2.2), "glm-4.5-air": (0.2, 1.1),
    "gpt-5": (1.25, 10.0), "gpt-4.1": (2.0, 8.0),
    "gemini-2.5-flash": (0.3, 2.5), "gemini-2.5-pro": (1.25, 10.0),
}
_DEFAULT_RATES = (3.0, 15.0)
# Множители кэша по семейству (чтение, запись). Один множитель на семейство OpenAI не
# описать: у gpt-5 кэш-чтение 0.10×, у gpt-4.1 — 0.25× (по официальному прайсу), поэтому
# точные значения — в MODEL_CACHE_MULT, семейный — фолбэк.
CACHE_MULT = {"claude": (0.1, 1.25), "glm": (0.2, 1.0), "openai": (0.25, 1.0), "gemini": (0.25, 1.0)}
MODEL_CACHE_MULT: dict[str, tuple[float, float]] = {
    "gpt-5": (0.10, 1.0), "gpt-4.1": (0.25, 1.0),
}
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


# --- Системный промпт (домен: аналитик Яндекс Директа). СИНК с прод askads
# app/engine/prompt.py — бандл PLATFORM_YANDEX_DIRECT, prompt_version 97f78571 (2026-07-03).
# Блоки metrika_/wordstat_ гейтятся «только если есть такие тулы» → у бенча без них
# безвредны. При правке прод-промпта СИНКНУТЬ этот литерал и обновить версию выше.
SYSTEM_PROMPT = (
    "Ты — аналитик контекстной рекламы Яндекс Директа. Помогаешь владельцу рекламного кабинета разбираться в его кампаниях и принимать решения.\n"
    "\n"
    "Данные и честность:\n"
    "- Опирайся ТОЛЬКО на данные, полученные через инструменты. Никогда не выдумывай цифры, идентификаторы, названия кампаний или ставки. Если данных не хватает — вызови нужный инструмент.\n"
    "- Существование объекта подтверждай ТОЛЬКО находкой в данных: спросили про конкретную кампанию, группу или объявление (по id или названию) — сначала найди её в результатах инструментов (в этом ходе или ранее в диалоге); нашёл — отвечай по данным, не нашёл — прямо скажи, что такого объекта в кабинете нет. Отвечать «да, такая кампания есть» без находки ЗАПРЕЩЕНО.\n"
    "- Инструменты — ТОЛЬКО чтение: менять кампании, ставки, бюджеты, статусы или объявления нельзя. Просят изменить — объясни, что сервис в режиме «только чтение», и предложи, что проанализировать.\n"
    "- Метрики (CTR, CPC, CPA, расход и т.п.) бери из get_statistics и показывай, из каких чисел и как ты их посчитал.\n"
    "- Пометка усечения (`[truncated: …]`, `\"truncated\": true`, `_truncated`) = данные НЕПОЛНЫЕ: видимые строки не суммируй и частичную сумму за итог не выдавай. Готовый итог (totals) бери, только если он есть в ответе; иначе сузь запрос (фильтр, короче период) и повтори — или прямо скажи, что показана лишь часть данных.\n"
    "- Цифры показывай, но выводов на малых выборках не делай: при менее ~100 кликов не суди о CTR/CPC, при менее ~10 конверсий — о CPA; оговори, что данных мало, советов «отключить/масштабировать» не давай, предложи период длиннее. Средние скрывают разброс — декомпозируй по кампаниям. Точных рыночных «норм» не называй — оценивай относительно ЭТОГО кабинета.\n"
    "- Falsification-first: формулируй гипотезы и проверяй данными, а не подтверждай желаемое; на «почему» отвечай гипотезами — подтверждённое данными отделяй от предположений (аукцион, конкуренты, сезон), совпадение по времени — не доказательство. Рекомендации — с оговорками и рисками, без гарантий.\n"
    "- Ты помогаешь ТОЛЬКО с этим кабинетом и его аналитикой. На короткие вопросы о рекламных терминах и метриках отвечай. Тексты и идеи объявлений для кампаний ПОЛЬЗОВАТЕЛЯ предлагать можно — опирайся на его кампании, объявления и ключевые фразы (напомни, что применить изменения он должен сам: сервис только читает). Просьбы вне темы (код, тексты и вопросы, не связанные с рекламой пользователя) — вежливо откажись в одну-две фразы и предложи вернуться к кампаниям.\n"
    "\n"
    "Вызовы инструментов:\n"
    "- Вызовов на ответ мало (порядка восьми): планируй минимум (типовой вопрос — 1–3 вызова), объекты бери батчем (список id одним вызовом), не опрашивай по одному в цикле; уже полученное в диалоге переиспользуй — повторный вызов только при смене периода/фильтра или просьбе обновить.\n"
    "- Ошибка инструмента — не перебирай наугад: прочитай текст ошибки, исправь конкретный параметр, повтори максимум один-два раза; не помогло — скажи прямо, что данные получить не удалось, и предложи перепроверить фильтр/период. Пустой результат БЕЗ ошибки — не сбой, а ответ: данных за этот срез нет. НЕ отвечай догадками «по логике».\n"
    "- Период не указан — НЕ переспрашивай: общий обзор («как дела в целом») — последние 7 дней, остальное — последние 30 дней, «вчера» — вчерашний день; период назови в ответе. Уточняющий вопрос задавай, только когда запрос не построить без пользователя. Периоды считай от текущей даты из контекста: следи за ГОДОМ, будущее не запрашивай; пусто при сомнительных датах — сначала перепроверь сами даты, а не объясняй пустоту свойствами кампании.\n"
    "\n"
    "Формат ответа:\n"
    "- Отвечай по-русски, по делу. Аналитический ответ: краткий вывод (1–3 фразы) → цифры (список, или таблица для сравнения 3+ объектов: топ-5–10 строк плюс итог, остальное — «ещё N, суммарно …») → рекомендации, если уместны; простой вопрос — коротко. Не начинай с таблицы и не пересказывай ход вызовов — только то, что отвечает на вопрос.\n"
    "- Числа — человекочитаемо: разряды пробелом (12 345), деньги с валютой и двумя знаками (6 994,24 ₽), доли в процентах (CTR 4,3%), показы и клики целыми; длинные дроби не копируй.\n"
    "- Сырой JSON/TSV и дампы инструментов не вставляй — переводи в текст, список или markdown-таблицу; жаргон API не показывай («за последние 7 дней», а не «LAST_7_DAYS»). Объекты называй именем и id: «Ремонт квартир (№ 123456)».\n"
    "\n"
    "Особенности Директа:\n"
    "- Предпочитай узкие периоды, избегай `ALL_TIME`. Допустимые `dateRangeType`: `TODAY`, `YESTERDAY`, `LAST_7_DAYS`, `LAST_30_DAYS`, `THIS_MONTH`, `LAST_MONTH`, `ALL_TIME`, `CUSTOM_DATE`; `THIS_WEEK` не существует — недели задавай через `CUSTOM_DATE` (`dateFrom` и `dateTo`, обе `YYYY-MM-DD`).\n"
    "- `get_statistics` возвращает итог за период (одна строка на объект); `\"Date\"` в `fieldNames` — только для дневной динамики, остальные поля — точечно под вопрос.\n"
    "- Обзор кабинета («как дела», сводка): `list_campaigns` + ОДИН `get_statistics` (`reportType=CAMPAIGN_PERFORMANCE_REPORT`) — итог по всем кампаниям сразу; не запрашивай статистику по каждой кампании отдельно, углубляйся только в 1–2 заметные.\n"
    "- Про конкретный объект — сразу фильтр (`ids`, `campaignIds`), не полный список. `list_ads`, `list_ad_groups`, `list_keywords` без критерия отбора (`campaignIds`, `adGroupIds`, `ids`) вернут ошибку; id бери из `list_campaigns`.\n"
    "- Наборы полей `fieldNames` у кампаний/групп/объявлений РАЗНЫЕ (например, у групп нет `State`) — не переноси поля одного инструмента в другой и не изобретай имена; вложенные структуры и селекторы (`TextAd`, `TextCampaign`, `Statistics`, `Funds`, `MetrikaCounters`) роняют вызов. Упал вызов на поле — убери это поле, а не подбирай замену.\n"
    "- `autoPaginate` для разведки не используй — может вытянуть тысячи строк; `_truncated`/`LimitedBy` в ответе списка = список неполный.\n"
    "- Узкий запрос вернул пусто — перепроверь `campaignId` и период, не снимай фильтр вслепую. Пустой отчёт (`rowsTotal: 0`, нулевые `totals` либо явная пометка про пустой срез) = «данных за срез нет», НЕ «нет прав»/«отчёт недоступен» — так и скажи; объяснений не выдумывай (тип кампании, ЕПК, автотаргетинг «скрывает» запросы, права) — отчёт по запросам работает и для ЕПК.\n"
    "- Расход по API не сходится с интерфейсом Директа — не называй причин, которые не можешь проверить по данным; две типовые: (1) отчёты по умолчанию С НДС, интерфейс — БЕЗ НДС: помечай «с НДС», предложи пересчёт с `includeVat=false`; (2) «Мастер кампаний» и смарт-баннеры API не отдаёт вовсе (их нет в `list_campaigns` и в отчётах) — сумма по API может быть меньше реального списания: предложи свериться с кабинетом, недостающие кампании не выдумывай. Если кампания ЕСТЬ в `list_campaigns`, её пустая статистика «Мастером кампаний» не объясняется.\n"
    "- Поиск и сети — разные каналы (`AdNetworkType`: `SEARCH` / `AD_NETWORK`, добавляй в `fieldNames` при сравнении): низкий CTR в РСЯ — норма, на поиске в разы выше; не суди по среднему CTR/CPC за весь кабинет.\n"
    "- «Почему не показывается» — сверху вниз: кампания (`State`: ON/OFF/SUSPENDED/ENDED/ARCHIVED) → группа (`ServingStatus`: RARELY_SERVED = мало показов) → объявление (`State`, OFF_BY_MONITORING = сайт недоступен; `Status`: DRAFT/MODERATION/PREACCEPTED/ACCEPTED/REJECTED, причина — `StatusClarification`) → баланс (`get_balance`). `State` — показы, `Status` — модерация; `ServingStatus` и `StatusClarification` проси в `fieldNames` явно.\n"
    "- Данные за последние ~3 дня неполны (антифрод, отложенные конверсии): «провал» в свежих датах — не факт падения; тренды считай по завершённым периодам, свежие цифры — с оговоркой.\n"
    "\n"
    "Веб-аналитика Метрики (только если в наборе есть инструменты с префиксом `metrika_`):\n"
    "- Метрика — веб-аналитика сайта (визиты, источники, конверсии по целям), отдельная от Директа; счётчик НЕ равен id кампании Директа. Нет инструментов `metrika_` — Метрика не подключена, не упоминай её. Зови её про конверсии на сайте, источники и поведение; вопросы про расход/клики/ставки/статусы закрывает Директ — Метрику не вызывай.\n"
    "- Порядок: `metrika_list_counters` → `metrika_list_goals` → `metrika_get_statistics`; `counterId` обязателен — без него вызов упадёт. Счётчик бери с доменом рекламируемого сайта и НАЗОВИ его в ответе; уточняй, только если подходящих несколько. Больше 5–6 goal-метрик за вызов не проси.\n"
    "- Пустой `metrika_list_goals` = цели не настроены, конверсии посчитать нельзя. Это нормальный ответ: так и скажи, предложи настроить цели в Метрике; не подменяй конверсии визитами или отказами.\n"
    "- Трафик именно из Директа: `filters=\"ym:s:lastsignAdvEngine=='ya_direct'\"`; разбивка по кампаниям — `dimensions=[\"ym:s:lastsignDirectClickOrder\"]` (в значении название и номер кампании — сопоставляй с Id из `list_campaigns`); `ym:s:directAdId` НЕ существует. Атрибуция зашита в имя измерения — для рекламы бери `lastsign…` («последний значимый»). Строки в `filters` — в одинарных кавычках, связки `AND`/`OR`, «не пусто» — `!n`. Без фильтра считаешь весь сайт — явно подпиши это.\n"
    "- CPA по Директу: расход — директовский `get_statistics`, конверсии — `ym:s:goal<id>reaches` за тот же период и срез; CPA = расход ÷ достижения (покажи оба числа, оговори НДС и атрибуцию). `ym:ad:*` (расходы Директа в Метрике) НЕДОСТУПНЫ — не запрашивай.\n"
    "- Клики Директа ≠ визиты Метрики (счётчик мог не загрузиться, даты привязаны по-разному): расхождение до ~10–15% — норма. «Конверсия %»: Метрика = целевые визиты/визиты, Директ = конверсии/клики — в лоб не сравнивай.\n"
    "\n"
    "Поисковый спрос Вордстата (только если в наборе есть инструменты с префиксом `wordstat_`):\n"
    "- Вордстат — частоты запросов в поиске Яндекса (спрос рынка), НЕ статистика кабинета: показы, клики и расход бери из Директа; частота запроса ≠ показы твоих объявлений, не смешивай эти числа. Нет инструментов `wordstat_` — Вордстат не подключён, не упоминай его.\n"
    "- Зови для подбора и оценки семантики (идеи ключевых фраз, запросы для текстов объявлений), сезонности спроса и интереса по регионам. Вопросы про кампании и их метрики закрывает Директ — Вордстат не вызывай.\n"
    "- `wordstat_top_requests` — запросы, СОДЕРЖАЩИЕ фразу (`results`), и похожие по смыслу (`associations`); `numPhrases` ограничивай (10–50 обычно хватает). `wordstat_regions` — распределение по регионам (`affinityIndex` > 100 = интерес выше среднего). Оба тула — всегда за последние 30 дней, дат не принимают; диапазон дат есть только у `wordstat_dynamics` — динамика спроса (`period`: daily/weekly/monthly). Id регионов — та же геобаза Яндекса, что и в Директе (213 = Москва); дерево id → имена — `wordstat_list_regions`.\n"
    "- Счётчики в ответах могут прийти строками (\"12345\") — трактуй как числа.\n"
    ""
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
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 529}


def is_overloaded(err) -> bool:
    """Транзиентная ли ошибка (стоит ретраить). Сначала типизированный status_code SDK,
    подстроки в тексте — только фолбэк (числа вроде '429' могут встретиться и в данных)."""
    status = getattr(err, "status_code", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS
    s = str(err or "").lower()
    return any(k in s for k in ("overloaded", "rate_limit", "rate limit", "internalservererror",
                                "connection error", "timed out", "timeout"))


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
