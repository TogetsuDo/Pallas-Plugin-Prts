from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

from pallas.api.paths import plugin_data_dir

from .config import get_config
from .corpus import CorpusError, CorpusStore, parse_pack_ids, sync_packs

if TYPE_CHECKING:
    from pallas.api.commands import PluginHandlerContext

PLUGIN_ID = "prts_corpus"
ARKNIGHTS_PACKS = "official_game,reviewed_wiki,terra_journey,entities,references"
ENDFIELD_PACKS = "endfield_official_game,endfield_reviewed_knowledge"
ALL_PACKS = f"{ARKNIGHTS_PACKS},{ENDFIELD_PACKS}"


def requested_packs(command_text: str, cfg) -> list[str]:
    text = str(command_text or "").strip()
    scope = ""
    for prefix in ("PRTS更新", "PRTS 更新", "PRTS同步"):
        if text.casefold().startswith(prefix.casefold()):
            scope = text[len(prefix) :].strip().casefold()
            break
    if not scope:
        return parse_pack_ids(cfg.packs, include_endfield=cfg.include_endfield)
    scopes = [item for item in re.split(r"[,，+\s]+", scope) if item]
    ak_scopes = {"ak", "arknights", "方舟", "明日方舟"}
    ef_scopes = {"ef", "endfield", "终末地"}
    if scopes and set(scopes) <= ak_scopes:
        return parse_pack_ids(ARKNIGHTS_PACKS)
    if scopes and set(scopes) <= ef_scopes:
        return parse_pack_ids(ENDFIELD_PACKS)
    if scope in {"all", "全部", "所有"} or (ak_scopes.intersection(scopes) and ef_scopes.intersection(scopes)):
        return parse_pack_ids(ALL_PACKS)
    raise CorpusError("用法：PRTS更新 [ak|ef|all]，也可写「方舟 / 终末地 / 全部」。")


async def handle_status(ctx: PluginHandlerContext) -> None:
    status = CorpusStore(Path(plugin_data_dir(PLUGIN_ID))).status()
    if not status["installed"]:
        await ctx.finish("PRTS 语料尚未安装。请管理员发送「PRTS更新」。")
    packs = "、".join(item["pack_id"] for item in status["packs"])
    await ctx.finish(
        f"PRTS 语料已就绪：{status['release_id']}\n"
        f"版本：{status['data_version'][:12]}…\n"
        f"pack：{packs}"
    )


async def handle_sync(ctx: PluginHandlerContext) -> None:
    cfg = get_config()
    try:
        packs = requested_packs(ctx.plain_text, cfg)
    except CorpusError as exc:
        await ctx.finish(str(exc))
    await ctx.bot.send(
        ctx.event,
        f"PRTS 语料开始更新\n范围：{len(packs)} 个 pack\n完成后会再回复结果。",
    )
    try:
        pointer = await asyncio.to_thread(
            sync_packs,
            Path(plugin_data_dir(PLUGIN_ID)),
            packs,
            timeout=cfg.download_timeout_sec,
        )
    except CorpusError as exc:
        await ctx.finish(f"PRTS 语料更新失败：{exc}")
    else:
        await ctx.finish(
            f"PRTS 语料更新完成：{pointer['release_id']}\n"
            f"版本：{pointer['data_version'][:12]}…\n"
            f"pack：{'、'.join(pointer['packs'])}"
        )
