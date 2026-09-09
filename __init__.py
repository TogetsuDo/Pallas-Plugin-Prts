from nonebot.plugin import PluginMetadata
from pallas.api.commands import (
    command_limit_list,
    command_limit_row,
    command_perm_list,
    command_perm_row,
)
from pallas.api.metadata import (
    PLUGIN_MENU_TEMPLATE,
    SCENE_BOTH,
    join_usage,
    knowledge_source_row,
    usage_line,
)

from . import commands as _commands  # noqa: F401
from . import llm_tools as _llm_tools  # noqa: F401
from .config import Config

PLUGIN_ID = "prts_corpus"

__plugin_meta__ = PluginMetadata(
    name="PRTS 语料",
    description="按需下载并检索 PRTS.chat 的明日方舟与终末地资料，为 LLM 提供带行号引用的原文检索。",
    usage=join_usage(
        usage_line("PRTS状态", "查看本地语料版本与已安装 pack"),
        usage_line("PRTS更新 [ak|ef|all]", "选择范围下载 PRTS 语料（管理员）"),
    ),
    type="application",
    homepage="https://github.com/HTian-qwq/prts-terrarchive",
    config=Config,
    supported_adapters={"~onebot.v11"},
    extra={
        "help_tag": "tool",
        "version": "0.1.1",
        "menu_template": PLUGIN_MENU_TEMPLATE,
        "reload_policy": "metadata",
        "command_permissions": command_perm_list(
            command_perm_row("prts_corpus.status", "PRTS状态", "everyone"),
            command_perm_row("prts_corpus.sync", "PRTS更新", "superuser"),
        ),
        "command_limits": command_limit_list(
            command_limit_row("prts_corpus.status", 3),
            command_limit_row("prts_corpus.sync", 10),
        ),
        "knowledge_sources": [
            knowledge_source_row(
                source_id="prts_corpus.guide",
                title="PRTS 语料工具说明",
                description="PRTS 语料的安装状态、资料来源和检索工具使用约定",
                chunks=[
                    {
                        "title": "语料安装",
                        "content": "PRTS 语料需要管理员发送「PRTS更新」后才会下载；未安装时不要根据 PRTS 语料编造事实。",
                        "keywords": "PRTS,语料,更新,安装,下载",
                    },
                    {
                        "title": "资料来源",
                        "content": "PRTS 检索结果区分官方游戏文本与整理性 Wiki、实体、时间线资料；回答时应保留这种来源区别。",
                        "keywords": "官方,Wiki,整理,时间线,来源,引用",
                    },
                ],
            ),
        ],
        "menu_data": [
            {
                "func": "PRTS状态",
                "group": "资料工具",
                "trigger_method": "on_cmd",
                "trigger_scene": SCENE_BOTH,
                "trigger_condition": "PRTS状态 / PRTS 状态",
                "command_permission": "prts_corpus.status",
                "brief_des": "查看 PRTS 语料状态。",
                "detail_des": "显示本地 release、data version 和已安装资料分区。",
            },
            {
                "func": "PRTS更新",
                "group": "资料工具",
                "trigger_method": "on_cmd",
                "trigger_scene": SCENE_BOTH,
                "trigger_condition": "PRTS更新 / PRTS同步",
                "command_permission": "prts_corpus.sync",
                "brief_des": "下载当前 PRTS 语料。",
                "detail_des": "可追加 ak、ef 或 all 选择明日方舟、终末地或全部资料；无参数时按插件配置下载。",
            },
        ],
    },
)
