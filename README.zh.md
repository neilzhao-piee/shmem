# shmem

> **给 agent 的共享记忆。markdown 是唯一真相，agent 之间只需要共享一个目录。**
> 没有 daemon、没有向量库、没有云——一个文件夹、一份协议、一个检索脚本。

[English](README.md) · [中文](README.zh.md)

![ci](https://github.com/neilzhao-piee/shmem/actions/workflows/ci.yml/badge.svg)

你跑着好几个 AI agent，它们各自失忆。常规解法——一个带向量索引的记忆服务——会引入
daemon、API、数据库，以及一份会跟 agent 真实记忆漂移的"第二真相"。

shmem 反过来：**记忆就是一堆 markdown 文件**，agent 本来就会读写。在这个目录之上盖一层
薄而零依赖的检索（SQLite FTS5，支持中文），再加一个每天把"以后仍然为真"的事实从会话历史
里抽进 journal 的定时任务。就这些。

## 为什么是 shmem（三个差异点）

| | 典型 "agent memory" | shmem |
|---|---|---|
| 存储 | 向量库 / 托管服务 | 纯 markdown，git 友好，人能审计 |
| 服务对象 | 一个助手 | **N 个 agent 共享一个目录** |
| 核心思路 | 更好的检索 | **一套 agent 真能执行的写入纪律** |

1. **文件优先。** markdown 是唯一真相。FTS5 索引是派生缓存——删了毫秒级重建。你可以
   `git diff` agent 的记忆、`cat` 着读、直接丢给另一个 agent，不用任何 API。
2. **天生多 agent。** 协议围绕"投递"设计（按收件人的 inbox 文件、显式 ACK）、按 agent 的
   注入方式（引导文件块 / hook / MCP / 贴 digest），以及一个硬事实：**没有任何 agent 的
   上下文会被自动共享**。
3. **协议才是产品。** 真正的贡献是 [`PROTOCOL.md`](PROTOCOL.md)——三条写入通道
   （facts / journal / inbox）、一套读协议（先检索、别通读）、几条硬规则（绝不写密钥；
   事实带日期和来源；修订不追加"更正"）。脚本只是这套协议约 750 行纯 Python 标准库 +
   几个 shell 的参考实现，**零第三方依赖**。喜欢协议、用自己的工具也行。

## 目录结构

```
INDEX.md            入口页：结构、协议摘要、指针
DIGEST.md           自动生成的 ≤9KB 自包含摘要（给读不到本地文件的 agent）
facts/              当前事实，重写式，一个主题一个文件
journal/            追加式事件流，按月轮转
inbox/              按收件人的投递文件（TO-<对方>.md）
tools/              memory-search.py · summarize-sessions.py · make-digest.sh
integrations/       按 agent 的胶水：dsh/（AGENTS.md 块）· hermes/（hook + 私有记忆快照）
units/              每日 summarizer 的 systemd user timer
examples/           一棵合成记忆树，可直接 `make new`
PROTOCOL.md         协议本体——先读这个
```

## 快速上手

Linux 和 macOS 都能跑：只需 Python 3.9+（纯标准库）和带 FTS5 的 sqlite3（macOS 系统自带的已含 FTS5）。macOS 上 Makefile 需要 GNU make（`brew install make` 后用 `gmake`），定时任务用 launchd agent 代替 systemd timer（见 `units/launchd.example.plist`）。

```sh
git clone <本仓库> && cd shmem

# 1. 建一个记忆目录
make new DIR=~/my-memory          # 拷贝 examples/ + tools/

# 2. 检索（立即可用；首次查询时自动建索引）
SHMEM_ROOT=~/my-memory make search Q="dashboard 跑在哪台机器"
SHMEM_ROOT=~/my-memory make search Q="为什么改成周日凌晨跑备份"   # 中文能查

# 3. 重新生成给沙箱 agent 的 digest
make digest DIR=~/my-memory

# 4.（可选）每天从 agent 会话历史里抽事实
cp config.example.env /etc/shmem.env   # 填 SUMMARIZE_MODEL_URL 等
make install-units DIR=~/my-memory     # systemd user timer，本地 05:30 跑
```

在每个 agent 的指令里告诉它目录在哪（"干活前先读 `INDEX.md`，再读相关 `facts/`；查历史
先检索别通读 journal"），再给它接上注入的门：

| 你的 agent 有…… | 用这个接 |
|---|---|
| 引导文件（DSH `AGENTS.md`、Claude `CLAUDE.md` 等） | `integrations/dsh/update-agents-block.sh`——在文件里维护一段标记包裹的块 |
| 会话 hook（hermes `pre_llm_call`） | `integrations/hermes/inject-shared-memory.sh`——每会话注入一次 digest |
| MCP | `tools/memory-search.py --mcp`（stdio；读工具 `memory_search`、`memory_stats`；写工具 `memory_write`、`memory_append`、`inbox_write` 仅在 `--allow-write` 时开放） |
| 啥都没有（沙箱） | 贴/上传 `DIGEST.md`——集成到此为止 |

## 工具一览

| 工具 | 干什么 | 依赖 |
|---|---|---|
| `tools/memory-search.py` | 对 markdown 建 FTS5 索引；**中文按字符二元组切分**（不依赖 jieba）；按内容哈希增量重建索引；返回片段 + 文件 + 行号；CLI **和** MCP stdio 服务两种面；可选向量层（RRF 融合，默认关）；MCP 写工具（三通道 + flock + secret guard）仅在 `--allow-write` 时开放 | Python 3 标准库 |
| `tools/summarize-sessions.py` | 每日收割：增量扫描 agent 会话历史 → 模型抽取持久事实 → 在 `flock` 下追加进 `journal/`；质量闸门（≤6 行、不编日期、不含凭据模式）；模型挂了退回关键词抽取；会话源可插拔（内置 DSH + hermes 两个 adapter） | Python 3 标准库 |
| `tools/make-digest.sh` | 生成 `DIGEST.md`（≤9KB）：INDEX 头部 + facts 速览 + 最新自动摘要 + 最近私有记忆快照 | bash + awk（可移植反转，不依赖 `tac`） |
| `integrations/dsh/update-agents-block.sh` | 在 DSH 的 `AGENTS.md` 标记区间里刷新自动注入块 | bash + awk |
| `integrations/hermes/snapshot-memory.sh` | 对方 agent 的私有 `MEMORY.md`/`USER.md` 变化时追加进 journal | bash + sha256sum/shasum + flock（有 python3 回退） |
| `eval/run.sh` | 对提交进仓库的合成样例跑 recall 测试（23 对，含中文和"查不到不许编"的诚实用例）；任何检索改动必须保持 100%——这就是 CI 门槛 | bash + python3 |
| `tests/test_shmem.py` | 协议强制部分的单元测试：中文二元组切分、secret guard（模式 + 凭证值）、slug 规则、三条写通道、默认只读面 | Python 3 标准库 |
| `integrations/hermes/inject-shared-memory.sh` | hermes `pre_llm_call` hook：刷新 digest、快照记忆、每会话注入一次 | bash + python3 |

配置全部走环境变量（见 [`config.example.env`](config.example.env)）——没有任何硬编码。

## 安全模型

- **写入端**：协议禁止把凭据写进记忆（只写位置）——协议第 1 条，summarizer 的提示词也
  强制这一点。
- **索引端**：匹配明显凭据模式（`password = …`）的行永不入索引；包含 `SHMEM_SECRET_FILES`
  所列文件取值的行直接跳过。
- **摘要端**：summarizer 输出先过行级闸门（必须是 bullet、日期不得越出会话年份、不得含
  凭据模式）才允许落 journal。
- **边界**：所有写入都有上限（每次运行 ≤4KB、inbox ≤20 行、digest ≤9KB）；journal 追加
  用 `flock` 串行化。设计上单机——要跨机就把 git 当同步层（当初特意选 markdown 就是为此）。

## 设计取舍与诚实的局限

- **这个量级 BM25 够了。** 几 MB 的记忆，词法检索的精度通常压过大多数 embedding 方案，
  而且零成本。向量层存在但默认关；语料超过 ~1MB 且中文短语召回明显掉时再开。
- **journal 是可检索的，不是可读的。** 没人通读它，这正是目的。如果你发现自己在滚动
  journal，说明 `facts/` 里少了一条。
- **单机。** `flock` 只在一个主机上串行化写入。跨机共享靠 git（当初选 markdown 就是为此），
  不靠 shmem 的任何特性。
- **summarizer 故意有损。** 它只保留本地模型认证的"仍然为真"；漏掉的留在会话历史里。
  它**从不**写 `facts/`——curated 的真相刻意由 agent/人写。
- **不是数据库。** 并发模型 = 文件锁 + 纪律，不是事务。在"几个 agent、一台机器"的尺度上
  这是优点。

## 来源

shmem 诞生于让两个长跑 agent（DSH 编码 agent + hermes 助手 agent）在一台机器上共享一份
记忆的场景，2026-10 起持续运行至今。DSH / hermes 专属的胶水放在 `integrations/` 里并明确
标注，其余部分与具体 agent 无关。

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)

## 许可

MIT——见 [LICENSE](LICENSE)。
