# PRTS 语料社区插件

为 Pallas-Bot 提供 PRTS.chat 明日方舟 / 终末地资料检索能力，供 LLM 使用：

- `prts.search`：检索资料、命中行和引用定位；
- `prts.read`：读取搜索结果对应的原文；
- `prts.timeline`：检索活动时间线与泰拉年表。

插件代码不包含游戏语料。安装后由管理员发送 `PRTS更新`，插件从 PRTS.chat 当前 release
选择的公开镜像下载用户配置的 pack，并保存到 `data/prts_corpus/`。每个文件都会按 PRTS
manifest 的大小和 SHA-256 校验。

## 安装

将仓库目录放入：

```text
local/plugins/prts_corpus/
```

然后重启 Bot。在控制台插件页调整 pack 或是否包含终末地，管理员发送：

```text
PRTS更新
```

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
| `search_limit` | LLM 单次最多返回的资料数 |
| `read_max_lines` / `read_max_chars` | 单次原文读取预算 |

## 权利边界

本仓库代码按 MIT License 发布。PRTS 语料不适用本仓库代码许可证，仍按各 pack 的来源声明、
游戏权利方条款和 PRTS.chat / ModelScope 数据集说明使用。插件只保存用户主动下载的本地副本，
不重新发布语料。

相关项目：

- [PRTS Terrarchive](https://github.com/HTian-qwq/prts-terrarchive)
- [明日方舟语料集](https://modelscope.cn/datasets/HTiantian/prts-agent-corpus-arknights)
- [终末地语料集](https://modelscope.cn/datasets/HTiantian/prts-agent-corpus-endfield)
