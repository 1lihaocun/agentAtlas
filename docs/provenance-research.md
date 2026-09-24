# 调研：文件"产生来源与生成机制"展示（provenance 功能）

日期：2026-09-23。结论先行：**可行，且价值真实**。Codex 的记忆生态在本地留有完整的生成证据链（后台 job 记录 + 会话内工具调用记录 + 驱动规则文本），三者都能被程序化取证。限制与成本见文末。

## 一、要回答的三个问题

1. 这个文件是谁生成的？（后台任务 / 模型在聊天中主动调用工具 / 后台进程 / 人工）
2. 生成时遵循的"规则/提示词"是什么？
3. 若是工具调用：工具名、入参、出参能否还原？

## 二、本机取证结果（全部为真实读取，非推测）

### 2.1 Codex 记忆文件：四条生成路径，各有独立证据

| 文件/目录 | 生成机制 | 证据位置 | 已验证 |
|---|---|---|---|
| `memories/MEMORY.md`、`memories/memory_summary.md` | **后台 consolidate 任务**（会话结束后异步跑，非聊天中生成） | `~/.codex/memories_1.sqlite` 的 `jobs` 表：`memory_consolidate_global`，本机 2026-09-23 07:57:30–08:02:27(UTC)；两文件 mtime 16:01:02 / 16:02:12(+08) 与任务完成时间吻合 | ✅ |
| `memories/rollout_summaries/*.md` | **后台 stage1 任务**，每个 rollout 会话一份摘要 | 同库 `jobs` 表 `memory_stage1`（216 done / 24 error），`job_key` = thread_id；`stage1_outputs.rollout_slug` 与文件名 slug 一一对应（如 `rustfs-upload-502-k8s-public-endpoint-proxy-trace`） | ✅ |
| `memories/extensions/ad_hoc/notes/*.md` | **模型聊天中主动调用 apply_patch 写入** | 真实命中：`sessions/2026/09/03/rollout-…-01a065fa….jsonl` 行 52837，`tools.apply_patch("*** Begin Patch\n*** Add File: …/ad_hoc/notes/20260906T122400Z-digital-human-pending-entry.md\n+…")`，入参含完整文件内容 | ✅ |
| `memories/extensions/skysight/`、`extensions/chronicle/` 下的 resources | **后台常驻进程**（事件流 / 被动录屏）按 10 分钟/6 小时窗口切片生成 | 各自 `instructions.md` 明文写明机制（"rolling local event stream process that runs in the background" / "passive screen recording process"）；文件名模式 `YYYY-MM-DDTHH-MM-SS-xxxx-10min-slug.md` 已有规范 | ✅（机制声明+文件形态） |

补充：`stage1_outputs` 还有 `usage_count`/`last_usage`（如 rustfs 摘要 usage_count=5），对应注入提示词里的 `<oai-mem-citation>` 机制——**"这份记忆被后续会话引用过几次"是现成数据**，可直接展示。

### 2.2 驱动生成的"规则/提示词"文本：可取证

- **注入给模型的完整记忆指令**：每个 rollout 的 `developer` message（rollout jsonl 第 3 行左右）含 `## Memory` 段：记忆布局、quick memory pass 流程、`<oai-mem-citation>` 格式规范，以及关键的 **Updating memories 规则**："只能在被用户明确要求时更新；写入 `extensions/ad_hoc/notes/`；文件名必须 `<timestamp>-<short slug>.md`；不得自己改记忆文件"。—— 这就是 ad_hoc note 的"生成提示词"，逐字可得。
- **扩展的生成规则**：`extensions/*/instructions.md` 落盘在记忆目录里（skysight、chronicle、ad_hoc 各一份），含"必须打 `[skysight memory]` 标签"等约束。
- **系统提示词**：`session_meta.payload.base_instructions`（本机为 Codex Desktop 0.155.0-alpha.9.2 的完整 system prompt）。
- **环境参数**：`turn_context` 每轮记录 model、cwd、sandbox_policy、approval_policy、timezone。
- **拿不到的**：stage1/consolidate 后台任务调用 LLM 时的确切 prompt 不在磁盘上（应在 Codex 二进制内）。能展示的是：job 记录（起止时间、watermark、重试次数）+ 上述约束文本。UI 上要如实区分"任务记录"与"驱动规则"，不能冒充"当时的 prompt"。

### 2.3 工具调用的入参/出参：完整可得

rollout jsonl 中 `response_item/custom_tool_call`（name + 完整 arguments，exec 工具甚至是 JS 源码）与 `custom_tool_call_output`（call_id 配对）成对落盘。因此"点击来源标签 → 展开当时的工具名/入参/出参"技术上就是一次有界 grep + JSON 解析。

### 2.4 其他 agent（本机现状）

- **Claude Code**：`~/.claude/projects/<proj>/*.jsonl` 会话文件（本机仅 7 个、都很小），其中 assistant 消息含 `tool_use`（name+input）；`autoMemoryEnabled: true` 且 `projects/<proj>/memory/` 目录已被创建但为空——auto memory 机制存在、尚未产出内容。全局 `CLAUDE.md`（"常用语言为中文"）无生成记录，按"人工/不可溯源"处理。
- **Hermes（本工具）**：`~/.hermes/memories/MEMORY.md`、`USER.md` 由 memory 工具在聊天中写入（§ 分段）；批量历史在 `state.db`。可做二期。

## 三、真实价值论证

**决定性证据：用户已经手工做过这件事。** `ad_hoc/notes/20260914T064551Z-memory-targeted-corrections.md` 是一次"MEMORY.md 定点修订审计"的产物——用户当时人工核对记忆条目、定位来源、要求修订。这个功能就是把该手工流程产品化：点开任何一条记忆即知"哪次会话、哪个机制、按什么规则、用什么工具调用生成的"，直接回答"这条过时信息是哪来的/为什么被记住"。

次要价值：

- 与现有曝光统计（usage.py）互补：曝光回答"谁读到过我"，provenance 回答"谁写下我"，一读一写闭环。
- `usage_count` 让记忆条目多一个"被引用 N 次"的置信信号。
- 界面信息密度符合用户偏好：标签本体极小，证据按需展开。

## 四、落地设计（建议切法）

1. **分类器（纯本地规则，零扫描）**：按路径即判机制——`extensions/skysight|chronicle/` → 后台进程；`rollout_summaries/` → stage1 任务（可再查 sqlite 补 job 记录）；`MEMORY.md`/`memory_summary.md` → consolidate 任务；`extensions/ad_hoc/notes/` → 模型 apply_patch；`skills/` → 按内容再判。挂到 `docs/file-types.json` 的条目上（file_guide.py 已有按文件名模式的注册表，`_ACTIVITY` 正则已经认识 skysight 资源名，顺势扩展）。
2. **证据取数（按需、带缓存，不做全量预扫）**：点击"来源"时才查——
   - sqlite 只读连接拿 job 行（`mode=ro` URI，usage.py 已有同款安全先例）；
   - 会话溯源在文件 mtime ±N 天的 rollout 窗口内 grep note 文件名，命中后解析 call/output 对，提取工具名、入参（截断至 64KB，同 usage.py 的 MAX_ARGUMENT_BYTES 约定）、出参片段；
   - 规则文本：从任一近期 rollout 的 developer message 截取 `## Memory` 段 + 对应 `extensions/*/instructions.md` 原文。
3. **UI**：文件表/详情加一枚来源徽标（后台任务 / 模型写入 / 后台进程 / 人工·不可溯源），点击展开三层：机制说明 → 任务/调用记录 → 规则文本与工具出入参。
4. **口径声明**：后台任务只能给"任务记录+约束规则"，不给"当时 prompt"；溯源失败（会话已归档/未保留）如实显示"证据未保留"，不作假。

## 五、限制与风险

- **溯源范围有限**：本机验证中部分 note 的写入会话已不在 `sessions/`（仅 `archived_sessions/` 命中读操作），写入调用可能随会话归档/清理而不可达。全量 grep 2026 年会话实测约 40s（含 41 万文件的 worktrees 目录被波及时更慢），必须按需+限窗+缓存。
- **格式漂移**：Codex 闭源，rollout/sqlite 结构无兼容承诺（`~/.codex/rollout-migrations/` 的存在即信号）。沿用 usage.py 的 SCHEMA_VERSION/PARSER_VERSION 防御式解析与降级策略。
- **Claude Code / Hermes 证据薄**：先做 Codex（数据最全、用户主力），其余标注"机制已知、证据待采"。

## 六、结论

- 可实现性：**高**。四类机制本机全部取到一手证据，无需逆向、无需联网。
- 真实价值：**成立**。用户已有手工审计记忆的实录需求；读写两侧数据（曝光统计 + usage_count + 生成链）恰好拼成完整的记忆生命周期视图。
- 建议范围：MVP 只做 Codex 记忆目录（~495 文件）的四分类徽标 + 点击取证面板；溯源查询按需执行并缓存。

---

## 七、实现记录（2026-09-23 完成第一期：Codex + Hermes）

已实现并全部验证：

- `atlas/provenance.py`：`ProvenanceService.describe(entry)` 按类型分流取证——
  - consolidate/stage1 类：只读查询 `memories_1.sqlite`（`mode=ro` URI），返回 `jobs` 表最近 24 条任务记录；rollout 摘要再匹配 `stage1_outputs` 的 slug 与 `usage_count`；
  - ad-hoc 笔记：先按文件名时间戳查对应日期目录，再按 mtime ±21 天窗口限流扫描 `sessions/`+`archived_sessions/`（≤400 文件、单文件 ≤512MB、流式逐行、2MB/行上限），找到写入调用后配对 `custom_tool_call`/`_output`，给出工具名、入参（≤4000 字）、出参（≤2000 字）；找不到如实返回"证据未保留"；
  - skysight/chronicle：机制说明 + 本机 `instructions.md` 声明路径；
  - Hermes：机制说明（memory 工具 + write_approval 提案），注明 state.db 逐条溯源未实现。
- `docs/file-types.json`：11 个类型新增 `generationMechanism` 字段；新增 `codex-doc-memories`（developers.openai.com/codex/customization/memories）与 `hermes-doc-memory`（hermes-agent.nousresearch.com/docs/user-guide/features/memory）官方来源；`codex-memory` 源更新为官方文档 URL。
- `atlas/file_guide.py`：注册表允许 `generationMechanism`；`_description` 聚合"官方文档"来源为 `docs` URL 列表。
- `atlas/serve.py`：`GET /api/provenance?path=…`（清单内、非受限条目）。
- `web/index.html`：类型说明弹窗新增「生成机制」区块（带"查看生成证据"按钮，仅对当前文件路径出现）与「官方文档」超链接区；证据面板展示机制、官方文档、证据块（任务记录表 / 写入调用的出入参）、诚实声明（后台任务的确切模型提示词不落盘）。
- 测试：`tests/test_provenance.py`（11 项）+ `tests/test_frontend_file_guide.py` 新增 UI 契约（生成机制区块、官方文档链接、单一捕获委托复用、证据面板）。全量 404 项 OK（`/usr/bin/python3` + `TMPDIR=scratch` + `no_proxy=*`），node 9 项 OK。
- 真机验证：五类文件端到端全部命中——MEMORY.md（24 条任务记录）、rollout 摘要（slug+usage_count 对照）、补充笔记（在 549MB 跨天会话中 1.1s 定位 apply_patch 写入调用及出入参）、skysight 资源（声明文件路径）、Hermes MEMORY.md（机制+官方文档）。

已知环境注意：系统代理开启时（如 127.0.0.1:7890），unittest 的 urllib 自检请求会被代理拦截导致 http 类测试假失败，跑测试需 `no_proxy=*`；`test_eval_isolation`/`test_eval_runner` 需在 `/usr/bin/python3` 下运行（沙箱白名单不含 hermes venv 路径）。
