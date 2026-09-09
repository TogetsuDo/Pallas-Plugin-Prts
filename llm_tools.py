from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from pallas.api.paths import plugin_data_dir
from pallas.api.platform import register_llm_tool

from .config import get_config
from .corpus import CorpusError, CorpusStore, parse_pack_ids

PLUGIN_ID = "prts_corpus"
_STORE: CorpusStore | None = None
_QUERY_SCOPE_HINTS = (
    "剧情",
    "世界观",
    "台词",
    "密录",
    "时间线",
    "年表",
    "什么时候",
    "何时",
    "是什么",
    "是谁",
    "什么意思",
    "是啥意思",
    "哪里",
    "哪儿",
    "去哪",
    "最后",
    "结局",
    "发生",
    "为什么",
    "怎么",
    "介绍",
    "查一下",
    "查询",
)


def _store() -> CorpusStore:
    global _STORE
    if _STORE is None:
        _STORE = CorpusStore(Path(plugin_data_dir(PLUGIN_ID)))
    return _STORE


def prts_scope_matcher(user_text: str) -> bool:
    text = str(user_text or "").strip()
    return bool(
        text
        and get_config().enabled
        and any(hint in text for hint in _QUERY_SCOPE_HINTS)
        and _store().contains_entity(text)
    )


def _not_ready(error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(error),
        "result": {"hint": "请先使用 PRTS更新 下载语料。"},
    }


async def _search_with_query_fallback(
    query: str,
    *,
    packs: list[str],
    limit: int,
    filters: dict[str, list[str]],
) -> dict[str, Any]:
    store = _store()
    result = await asyncio.to_thread(store.search, query, pack_ids=packs, limit=limit, **filters)
    if result.get("count"):
        return result
    terms = list(dict.fromkeys(re.findall(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9_-]{2,}", query)))
    relaxed = {key: filters[key] for key in ("games", "resource_types", "activity_names")}
    for term in terms:
        if term == query:
            continue
        fallback = await asyncio.to_thread(store.search, term, pack_ids=packs, limit=limit, **relaxed)
        if fallback.get("count"):
            fallback["query"] = query
            fallback["matched_query"] = term
            return fallback
    return result


async def handle_search(args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.enabled:
        return {"ok": False, "error": "prts_disabled"}
    try:
        packs = parse_pack_ids(cfg.packs, include_endfield=cfg.include_endfield)
        filters = {
            key: [str(item).strip() for item in args.get(key, []) if str(item).strip()]
            for key in ("games", "resource_types", "character_names", "activity_names", "entity_names", "speakers")
        }
        result = await _search_with_query_fallback(
            str(args.get("query") or "").strip(),
            packs=packs,
            limit=min(int(args.get("limit") or cfg.search_limit), cfg.search_limit),
            filters=filters,
        )
        if result.get("count"):
            result["activate"] = ["prts.read"]
        return result
    except CorpusError as exc:
        return _not_ready(exc)


async def handle_read(args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.enabled:
        return {"ok": False, "error": "prts_disabled"}
    try:
        return await asyncio.to_thread(
            _store().read,
            document_uid=str(args.get("document_uid") or ""),
            document_id=str(args.get("document_id") or ""),
            display_title=str(args.get("display_title") or ""),
            source_ref=str(args.get("source_ref") or ""),
            start_line=int(args.get("start_line") or 1),
            end_line=int(args["end_line"]) if args.get("end_line") is not None else None,
            max_lines=min(int(args.get("max_lines") or cfg.read_max_lines), cfg.read_max_lines),
            max_chars=min(int(args.get("max_chars") or cfg.read_max_chars), cfg.read_max_chars),
        )
    except CorpusError as exc:
        return _not_ready(exc)


async def handle_timeline(args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.enabled:
        return {"ok": False, "error": "prts_disabled"}
    try:
        query = str(args.get("query") or "").strip()
        entity_names = [str(item).strip() for item in args.get("entity_names", []) if str(item).strip()]
        if not query and entity_names:
            query = entity_names[0]
        return await asyncio.to_thread(
            _store().search,
            query,
            pack_ids=["references"],
            limit=min(int(args.get("limit") or cfg.search_limit), cfg.search_limit),
        )
    except CorpusError as exc:
        return _not_ready(exc)


register_llm_tool(
    name="prts.search",
    plugin_name=PLUGIN_ID,
    description="检索本地 PRTS 方舟/终末地语料，返回资料标题、命中行和引用定位。语料未安装时不要编造结果。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "短实体名、台词片段、活动名或问题关键词"},
            "games": {"type": "array", "items": {"type": "string", "enum": ["arknights", "endfield"]}},
            "resource_types": {"type": "array", "items": {"type": "string"}},
            "character_names": {"type": "array", "items": {"type": "string"}},
            "activity_names": {"type": "array", "items": {"type": "string"}},
            "entity_names": {"type": "array", "items": {"type": "string"}},
            "speakers": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
    domains={"prts", "arknights", "endfield"},
    hints={"方舟剧情", "明日方舟世界观", "终末地剧情", "PRTS台词", "泰拉年表", "PRTS"},
    capabilities={"read_only"},
    handler=handle_search,
    scope_matcher=prts_scope_matcher,
    max_execution_ms=60_000,
)

register_llm_tool(
    name="prts.read",
    plugin_name=PLUGIN_ID,
    description="读取 PRTS 搜索结果对应的原文行，必须优先使用 prts.search 返回的 document_uid 或 source_ref。",
    parameters={
        "type": "object",
        "properties": {
            "document_uid": {"type": "string"},
            "document_id": {"type": "string"},
            "display_title": {"type": "string"},
            "source_ref": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 50000},
        },
    },
    domains=set(),
    hints={"方舟原文", "PRTS引用", "PRTS台词", "方舟密录"},
    capabilities={"read_only"},
    handler=handle_read,
    visibility="deferred",
    max_execution_ms=30_000,
)

register_llm_tool(
    name="prts.timeline",
    plugin_name=PLUGIN_ID,
    description="检索 PRTS 语料中的活动时间线与泰拉年表。时间线属于整理性资料，回答时应标注其来源性质。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "entity_names": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        },
    },
    domains={"prts", "arknights"},
    hints={"泰拉年表", "方舟时间线", "PRTS时间线", "活动顺序"},
    capabilities={"read_only"},
    handler=handle_timeline,
    max_execution_ms=60_000,
)
