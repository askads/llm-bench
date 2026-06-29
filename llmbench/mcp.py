"""MCP-слой: ДВА режима получения tool_result.

- live  — спавним реальный npm MCP-сервер по stdio (твои mcp-yandex-direct / vk-ads / metrica),
          токены из env. Так гоняем модели против НАСТОЯЩИХ тулов.
- fixed — FakeMCPSession на замороженных фикстурах (детерминированный model-бенч, CI-safe).

Оба дают одинаковый контракт: `list_tools() -> .tools`, `call_tool(name, args) -> .content/.isError`.
Движок (engines.py) принимает фабрику сессий и не знает, какой режим под капотом.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from llmbench import fixtures as acc
from llmbench.core import PLATFORM_VK, PLATFORM_YANDEX_DIRECT, PLATFORM_YANDEX_METRIKA

# Корень репо (где node_modules с MCP-серверами).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Реестр серверов: пакет, путь к dist, обязательный токен из env (+ опц. язык/логин).
SERVERS = {
    PLATFORM_YANDEX_DIRECT: {"pkg": "mcp-yandex-direct", "token_env": "YANDEX_DIRECT_TOKEN",
                             "extra": {"YANDEX_DIRECT_LANG": "ru"}, "login_env": "YANDEX_DIRECT_LOGIN"},
    PLATFORM_YANDEX_METRIKA: {"pkg": "mcp-yandex-metrica", "token_env": "YANDEX_METRIKA_TOKEN",
                              "extra": {"YANDEX_METRIKA_LANG": "ru"}},
    PLATFORM_VK: {"pkg": "mcp-vk-ads", "token_env": "VK_ADS_TOKEN", "extra": {"VK_ADS_LANG": "ru"}},
}


def _server_path(platform: str) -> str:
    override = os.environ.get(f"MCP_PATH_{platform.upper()}")
    if override:
        return os.path.expanduser(override)
    pkg = SERVERS[platform]["pkg"]
    return os.path.join(_ROOT, "node_modules", pkg, "dist", "index.js")


@asynccontextmanager
async def live_session(platform: str):
    """Спавн реального MCP-сервера по stdio. Токен — из env (см. SERVERS)."""
    cfg = SERVERS[platform]
    token = os.environ.get(cfg["token_env"])
    if not token:
        raise RuntimeError(f"нет {cfg['token_env']} в env для live-режима ({platform})")
    env = {"PATH": os.environ.get("PATH", ""), cfg["token_env"]: token, **cfg.get("extra", {})}
    login_env = cfg.get("login_env")
    if login_env and os.environ.get(login_env):
        env[login_env] = os.environ[login_env]
    params = StdioServerParameters(command="node", args=[_server_path(platform)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            session.server_version = getattr(getattr(init, "serverInfo", None), "version", None)
            yield session


# ----------------------------- FIXED (фикстуры) -----------------------------
SERVER_VERSION = "fake-bench-1.0"

_DIRECT_TOOLS = [
    Tool(name="get_account_info", description="Информация о рекламном аккаунте.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="list_campaigns", description="Список кампаний (Id, Name, Type, State, Status).",
         inputSchema={"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}}),
    Tool(name="list_ad_groups", description="Группы объявлений кампаний.",
         inputSchema={"type": "object", "properties": {"campaignIds": {"type": "array", "items": {"type": "integer"}}}}),
    Tool(name="list_keywords", description="Ключевые слова кампаний/групп.",
         inputSchema={"type": "object", "properties": {"campaignIds": {"type": "array", "items": {"type": "integer"}}}}),
    Tool(name="get_regions", description="Справочник гео-регионов.",
         inputSchema={"type": "object", "properties": {"query": {"type": "string"}}}),
    Tool(name="get_statistics", description="Статистика кампаний за период: показы, клики, расход, CTR, конверсии.",
         inputSchema={"type": "object", "properties": {
             "reportType": {"type": "string", "enum": ["ACCOUNT_PERFORMANCE_REPORT", "CAMPAIGN_PERFORMANCE_REPORT", "SEARCH_QUERY_PERFORMANCE_REPORT"]},
             "dateRangeType": {"type": "string", "enum": ["TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_30_DAYS", "THIS_MONTH", "LAST_MONTH", "ALL_TIME", "CUSTOM_DATE"]},
             "campaignIds": {"type": "array", "items": {"type": "integer"}},
             "fieldNames": {"type": "array", "items": {"type": "string"}}}}),
]
_METRIKA_TOOLS = [
    Tool(name="list_counters", description="Счётчики Яндекс Метрики.",
         inputSchema={"type": "object", "properties": {"search": {"type": "string"}}}),
    Tool(name="list_goals", description="Цели/конверсии счётчика.",
         inputSchema={"type": "object", "properties": {"counterId": {"type": "integer"}}}),
    Tool(name="get_statistics", description="Веб-аналитика: визиты, пользователи, конверсии по целям.",
         inputSchema={"type": "object", "properties": {"counterId": {"type": "integer"},
                      "metrics": {"type": "array", "items": {"type": "string"}}}}),
]


def _ints(val):
    if val is None:
        return None
    out = []
    for x in (val if isinstance(val, list) else [val]):
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out or None


def _resolve_direct(name, args) -> str:
    if name == "get_account_info":
        return '{"Login": "bench-demo", "Currency": "RUB", "Type": "GENERAL"}'
    if name == "list_campaigns":
        return acc.list_campaigns_result(_ints(args.get("ids")))
    if name == "list_ad_groups":
        return '{"adGroups": [{"Id": 60001, "Name": "Группа 1", "CampaignId": 12345}]}'
    if name == "list_keywords":
        cids = _ints(args.get("campaignIds")) or []
        if 12349 in cids:
            return acc.keywords_big_result(12349)
        return '{"keywords": [{"Id": 70001, "Keyword": "пример", "CampaignId": 12345, "Bid": 1500}]}'
    if name == "get_regions":
        return '{"GeoRegions": [{"GeoRegionId": 213, "GeoRegionName": "Москва"}]}'
    if name == "get_statistics":
        if args.get("reportType") == "ACCOUNT_PERFORMANCE_REPORT":
            return acc.campaign_report_tsv(None)
        return acc.campaign_report_tsv(_ints(args.get("campaignIds")))
    return f"ERROR: unknown direct tool '{name}'"


def _resolve_metrika(name, args) -> str:
    if name == "list_counters":
        return acc.metrika_counters_result()
    if name == "list_goals":
        return acc.metrika_goals_result()
    if name == "get_statistics":
        return acc.metrika_stats_result()
    return f"ERROR: unknown metrika tool '{name}'"


class FakeMCPSession:
    def __init__(self, platform):
        self._metrika = platform == PLATFORM_YANDEX_METRIKA
        self.server_version = SERVER_VERSION

    async def list_tools(self):
        return ListToolsResult(tools=_METRIKA_TOOLS if self._metrika else _DIRECT_TOOLS)

    async def call_tool(self, name, arguments=None):
        args = arguments or {}
        text = _resolve_metrika(name, args) if self._metrika else _resolve_direct(name, args)
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=text.startswith("ERROR:"))


@asynccontextmanager
async def fake_session(platform):
    yield FakeMCPSession(platform)


def open_session(mode: str, platform: str):
    """Фабрика сессии: 'live' → реальный сервер, 'fixed' → фикстуры. Возвращает async-ctxmgr."""
    return live_session(platform) if mode == "live" else fake_session(platform)
