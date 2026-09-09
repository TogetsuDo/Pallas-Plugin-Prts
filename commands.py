from pallas.api.commands import bind_alias_handlers, message_command

from .handlers import handle_status, handle_sync

status_command = message_command(
    "prts_corpus.status",
    "PRTS状态",
    aliases=("PRTS 状态", "prts状态"),
    scene="both",
    cd_sec=3,
    priority=5,
    block=True,
)
sync_command = message_command(
    "prts_corpus.sync",
    "PRTS更新",
    aliases=("PRTS同步", "PRTS 更新"),
    scene="both",
    cd_sec=10,
    priority=5,
    block=True,
)

bind_alias_handlers(status_command, handle_status)
bind_alias_handlers(sync_command, handle_sync)
