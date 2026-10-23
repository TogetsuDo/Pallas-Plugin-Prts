from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from nonebot import logger
from pallas.api.logging import register_plugin_startup_ready
from pallas.api.paths import plugin_data_dir

from .config import get_config

PLUGIN_ID = "prts_corpus"
JOB_ID = "prts_corpus.auto_update"


def _is_sharded_worker() -> bool:
    try:
        from pallas.api.platform import is_sharded_worker

        return bool(is_sharded_worker())
    except Exception:  # noqa: BLE001
        return False


async def _auto_update_job() -> None:
    cfg = get_config()
    if not cfg.enabled or not cfg.auto_update:
        return
    from .corpus import CorpusError, auto_update_if_needed, parse_pack_ids

    packs = parse_pack_ids(cfg.packs, include_endfield=cfg.include_endfield)
    try:
        result = await asyncio.to_thread(
            auto_update_if_needed,
            Path(plugin_data_dir(PLUGIN_ID)),
            packs,
            timeout=cfg.download_timeout_sec,
        )
    except CorpusError as exc:
        logger.warning("PRTS 语料自动更新失败，保留现有语料：[{}]", exc)
        return
    except Exception:  # noqa: BLE001
        logger.exception("PRTS 语料自动更新任务异常")
        return
    if result.get("updated"):
        logger.info(
            "PRTS 语料已自动更新至 release [{}]，共 [{}] 个 pack",
            result.get("release_id"),
            len(result.get("packs") or []),
        )
    else:
        logger.info("PRTS 语料检查完成，release [{}] 已是最新，跳过下载", result.get("release_id"))


def reschedule_auto_update_job(*, run_soon: bool = False) -> None:
    if _is_sharded_worker():
        return
    try:
        from nonebot_plugin_apscheduler import scheduler
    except ImportError:
        logger.warning("PRTS 语料自动更新未调度：缺少 nonebot_plugin_apscheduler")
        return
    if scheduler.get_job(JOB_ID):
        scheduler.remove_job(JOB_ID)
    cfg = get_config()
    if not cfg.enabled or not cfg.auto_update:
        logger.info("PRTS 语料自动更新已关闭，未注册定时任务")
        return
    hours = max(1, min(720, int(cfg.update_interval_hours or 24)))
    add_kwargs: dict[str, object] = {}
    if run_soon:
        add_kwargs["next_run_time"] = datetime.now(UTC)
    scheduler.add_job(
        _auto_update_job,
        trigger="interval",
        hours=hours,
        id=JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
        **add_kwargs,
    )
    register_plugin_startup_ready(
        PLUGIN_ID,
        detail=f"PRTS 语料自动更新已调度：每 [{hours}] 小时检查一次",
    )


def setup_prts_runtime() -> None:
    reschedule_auto_update_job(run_soon=True)


def _register_startup() -> None:
    try:
        from nonebot import get_driver
    except Exception:  # noqa: BLE001
        return
    try:
        get_driver().on_startup(setup_prts_runtime)
    except ValueError:
        return


_register_startup()
