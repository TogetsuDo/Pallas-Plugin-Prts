# PRTS 语料社区插件

为 Pallas-Bot 提供 PRTS.chat 明日方舟 / 终末地资料检索能力，供 LLM 使用：

- `prts.search`：检索资料、命中行和引用定位；
- `prts.read`：读取搜索结果对应的原文；
- `prts.timeline`：检索活动时间线与泰拉年表。

插件代码不包含游戏语料。安装后由管理员发送 `PRTS更新`，插件从 PRTS.chat 当前 release
选择的公开镜像下载用户配置的 pack，并保存到 `data/prts_corpus/`。每个文件都会按 PRTS
manifest 的大小和 SHA-256 校验。

插件默认开启自动更新：启动后不久检查一次，之后按配置间隔检查 PRTS 当前 release；只有
release 或 data_version 变化、或本地缺少已配置 pack 时才会下载，同版本直接跳过。自动更新
与手动 `PRTS更新` 共用同一把锁，同一时间只会有一个同步在跑。

## 安装

将仓库目录放入：

```text
local/plugins/prts_corpus/
```

然后重启 Bot。在控制台插件页调整 pack 或是否包含终末地，管理员发送：

```text
PRTS更新 ak       # 明日方舟
PRTS更新 ef       # 终末地
PRTS更新 all      # 全部
```

也支持 `PRTS更新 方舟`、`PRTS更新 终末地`、`PRTS更新 全部`，以及用逗号或空格组合范围；不带参数时按插件配置下载。

## 默认资料

默认下载明日方舟：

- `official_game`
- `reviewed_wiki`
- `terra_journey`
- `entities`
- `references`

可在配置中开启 `include_endfield`。当前版本不把 PRTS 语料打进插件、Docker 或 Bot release。

常用配置：

| 配置项 | 作用 |
| --- | --- |
| `packs` | 逗号分隔的默认 pack 列表 |
| `include_endfield` | 是否追加终末地两个 pack |
| `auto_update` | 是否定时检查并自动下载新 release（默认开启） |
| `update_interval_hours` | 自动检查间隔，1–720 小时（默认 24） |
| `search_limit` | LLM 单次最多返回的资料数 |
| `read_max_lines` / `read_max_chars` | 单次原文读取预算 |

自动更新范围始终包含配置的 pack，并保留本地此前已安装的合法 pack；自动更新只在完整校验
下载成功后切换 `current.json`，失败时保留旧语料继续可读。若远端出现相同 release、不同
data_version，自动更新会拒绝原位覆盖并保留现有语料，避免破坏已下载数据。

## 权利边界

本仓库代码按 MIT License 发布。PRTS 语料不适用本仓库代码许可证，仍按各 pack 的来源声明、
游戏权利方条款和 PRTS.chat / ModelScope 数据集说明使用。插件只保存用户主动下载的本地副本，
不重新发布语料。

相关项目：

- [PRTS Terrarchive](https://github.com/HTian-qwq/prts-terrarchive)
- [明日方舟语料集](https://modelscope.cn/datasets/HTiantian/prts-agent-corpus-arknights)
- [终末地语料集](https://modelscope.cn/datasets/HTiantian/prts-agent-corpus-endfield)
