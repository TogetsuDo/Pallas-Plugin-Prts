from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from pallas.api.paths import plugin_data_dir

from .config import get_config
from .corpus import CorpusError, CorpusStore, parse_pack_ids, sync_packs

if TYPE_CHECKING:
    from pallas.api.commands import PluginHandlerContext

PLUGIN_ID = "prts_corpus"


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
        pointer = await asyncio.to_thread(
            sync_packs,
            Path(plugin_data_dir(PLUGIN_ID)),
            parse_pack_ids(cfg.packs, include_endfield=cfg.include_endfield),
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
