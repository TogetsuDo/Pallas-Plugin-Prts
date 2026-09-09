from pallas.api.config import install_hot_reload_config
from pydantic import BaseModel, Field


class Config(BaseModel, extra="ignore"):
    enabled: bool = Field(default=True, description="是否启用 PRTS 语料工具。")
    packs: str = Field(
        default="official_game,reviewed_wiki,terra_journey,entities,references",
        description="默认下载与检索的 pack，使用英文逗号分隔。",
    )
    include_endfield: bool = Field(default=False, description="是否额外启用终末地语料。")
    search_limit: int = Field(default=5, ge=1, le=10, description="单次检索最多返回的资料数。")
    read_max_lines: int = Field(default=80, ge=1, le=200, description="单次原文读取最多返回的行数。")
    read_max_chars: int = Field(default=12000, ge=1000, le=50000, description="单次原文读取最多返回的字符数。")
    download_timeout_sec: int = Field(default=120, ge=10, le=600, description="单个语料文件下载超时时间。")


plugin_webui = install_hot_reload_config(Config, config_module=__name__)
get_config = plugin_webui.get
