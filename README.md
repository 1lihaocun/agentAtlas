# AgentAtlas

在本机扫描、查看、检索和编辑智能体文件，分别展示文件存放位置、用途、作用范围与读取证据。

## 运行

需要 Python 3.12 以上、Node.js 22.12 以上和 uv。前后端依赖分别由 `backend/uv.lock` 与 `frontend/package-lock.json` 管理。

在仓库根目录执行：

```bash
make install
make build
make serve
```

打开 `http://127.0.0.1:7788`。后端提供 React 构建产物与 `/api` 接口；页面地址支持直接访问、刷新、浏览器前进和后退。

开发时执行 `make dev`，打开 `http://127.0.0.1:5173`。该命令同时启动 FastAPI 与 Vite，前端通过 `/api` 代理访问后端，退出命令时关闭这两个进程。Python 代码修改后需要重新启动开发命令；React 代码通过 Vite 更新。

## 功能与目录

| 目录 | 职责 |
| --- | --- |
| `frontend/src/app/` | 路由、应用布局、查询容器、跨功能组件组合与样式 |
| `frontend/src/features/assets/` | 全部文件、平台和用途筛选、分页 |
| `frontend/src/features/instructions/` | 指令地图、用户级指令、项目与目录导航、Codex 读取规则 |
| `frontend/src/features/memories/` | 记忆作用域、真实存放目录、元数据与数量统计 |
| `frontend/src/features/files/` | 文件查看、编辑草稿、章节保存、翻译、外部编辑器 |
| `frontend/src/features/search/` | 关键词、语义与混合检索、向量索引执行与发送确认 |
| `frontend/src/features/reviews/` | 使用证据、保留与延后审阅 |
| `frontend/src/features/duplicates/` | 重复文件分组与同步 |
| `frontend/src/features/file-guide/` | 类型、命名字段与生成来源说明 |
| `frontend/src/features/jobs/` | 扫描入口、扫描与索引任务状态 |
| `frontend/src/shared/` | HTTP 客户端、公共类型、格式化、表格、分页和对话框 |
| `backend/src/atlas/app/`、`http/`、`core/` | 应用组装、HTTP 约束、配置、原子写入与事务存储 |
| `backend/src/atlas/assets/`、`instructions/`、`memories/` | 文件发现、指令扫描、记忆规则与目录统计 |
| `backend/src/atlas/files/`、`search/`、`reviews/` | 源文件读写、检索、读取证据和审阅 |
| `backend/src/atlas/duplicates/`、`guides/`、`settings/`、`translation/`、`jobs/` | 对应功能的路由与业务服务 |
| `backend/src/atlas/platforms/` | 17 个平台的独立目录规则与注册表 |
| `backend/src/atlas/evaluation/` | 数据集、评分、隔离执行与评测命令行 |
| `backend/tests/` | 真实文件、SQLite、模块与 HTTP 回归检查 |
| `tests/e2e/` | 真实浏览器交互验收 |
| `tests/fixtures/` | 纳入版本管理的样例文件 |
| `scripts/` | 开发启动与工程维护命令 |

前端使用 React、TypeScript、React Router、TanStack Query、Ant Design 和 Radix UI。下拉菜单组件及使用约定见 [前端开发规范](frontend/README.md)。ESLint 检查功能目录之间的导入边界：功能依赖本功能与公共模块，跨功能组合由 `app` 负责。

后端使用 FastAPI、Pydantic 和 Uvicorn。每项业务拥有自己的路由，应用层创建服务并组织扫描、保存和索引流程。JSON、YAML、dotenv 和 Markdown 使用对应解析库。

## 页面地址

| 页面 | 地址 |
| --- | --- |
| 全部文件 | `/assets` |
| 指令地图、目录、用户级指令 | `/instructions`、`/instructions/directory?path=…`、`/instructions/user` |
| 记忆作用域、存放目录 | `/memories/scope`、`/memories/directory?path=…` |
| 审阅、重复文件 | `/reviews`、`/duplicates` |
| 文件说明 | `/file-types`、`/file-types/:typeId` |
| 独立文件详情 | `/files?path=…` |

筛选、页码和目录保存在 URL。列表页面通过 `file` 参数打开文件侧栏，`panel` 参数选择文件内容、章节或翻译。分页默认 50 条，支持 25、50、100 条。记忆目录分别统计本层文件和包含子目录的文件。

未保存的文件草稿保存在应用布局中。切换页面和关闭文件侧栏后，可以从“未保存修改”重新打开；刷新或关闭浏览器会触发未保存提示。文件保存使用 SHA-256 检查源文件版本，并在写入前保存备份。

## 本地配置与数据

`atlas --workspace /绝对路径 serve` 显式选择数据所在目录。默认使用执行命令时的目录；`make serve` 使用仓库根目录，`make dev` 默认使用仓库根目录，也支持通过 `ATLAS_WORKSPACE` 指定独立目录。

| 配置 | 含义 |
| --- | --- |
| `ATLAS_WORKSPACE` | 清单与运行数据所在目录，可用 `--workspace` 覆盖 |
| `ATLAS_HOME` | 扫描使用的用户目录，默认当前用户目录 |
| `ATLAS_SCAN_ROOTS` | JSON 数组，指定指令与项目扫描范围 |
| `ATLAS_MAX_DEPTH` | 扫描深度，默认 9 |
| `ATLAS_PORT` | 后端端口，默认 7788，可用 `serve --port` 覆盖 |
| `ATLAS_FRONTEND_PORT` | `make dev` 的前端端口，默认 5173 |
| `ATLAS_FRONTEND_DIR` | 前端构建目录，可用 `serve --frontend-dir` 覆盖 |
| `ATLAS_GUIDE_PATH` | 外部文件说明库路径 |
| `ATLAS_CODEX_HOME` | 使用证据与生成来源的 Codex 数据目录 |
| `ATLAS_ALLOWED_ORIGINS` | 开发代理允许的本机来源，JSON 数组 |

例如：

```bash
ATLAS_SCAN_ROOTS='["/Users/yourname/Documents/Code"]' make serve
backend/.venv/bin/atlas --workspace "$PWD" scan
backend/.venv/bin/atlas eval --help
```

### 模型配置

翻译与 embedding 配置统一保存在工作目录根部的 `.env`（`make serve` 默认使用仓库根目录；指定 `--workspace` 时使用该目录的 `.env`）。首次配置可复制 `.env.example` 为 `.env`，然后填写模型名称、服务地址与密钥。不要覆盖已有的 `.env`。

| 配置 | 含义 |
| --- | --- |
| `ATLAS_TRANSLATION_PROVIDER` | 翻译服务类型：openai、glm、zai、openrouter 或 ollama，默认 openai |
| `ATLAS_TRANSLATION_BASE_URL` | 翻译服务的 API 基础地址，留空使用该服务类型的默认地址 |
| `ATLAS_TRANSLATION_MODEL` | 翻译模型名称 |
| `ATLAS_TRANSLATION_API_KEY` | 翻译密钥，Ollama 可留空 |
| `ATLAS_EMBEDDING_BASE_URL` | 兼容 OpenAI embeddings 接口的 API 基础地址 |
| `ATLAS_EMBEDDING_MODEL` | Embedding 模型名称 |
| `ATLAS_EMBEDDING_API_KEY` | Embedding 密钥 |
| `ATLAS_EMBEDDING_DIMENSIONS` | 向量维度，留空由服务决定 |
| `ATLAS_EMBEDDING_BATCH_SIZE` | 每批文本数量，默认 16，范围 1–128 |
| `ATLAS_EMBEDDING_CATEGORIES` | 允许发送的文件类别，以英文逗号分隔；空值表示不允许发送任何类别 |

模型配置只读取这一个 `.env`，不继承进程环境变量、不读取 `~/.hermes/config.yaml` 或 `~/.hermes/.env`，也不再使用 `.agentatlas/settings.json` 和 `.agentatlas/embedding-key`。旧版本的配置需要移入对应字段；未配置模型不影响本地扫描与关键词检索。上表之外的运行参数仍通过命令行或进程环境变量传入。

模型配置只通过手工编辑 `.env` 修改，前端不提供配置表单或配置写入接口。`.env` 已加入 Git 忽略规则，建议权限设为 `0600`；字段按字面值解析，不展开 `${变量}`。修改后下一次操作读取新配置，无需重启；运行中的向量任务和分块翻译检测到文件变化会停止后续请求，需要重新确认。已经发出的请求无法撤回。

“全部文件”的检索工具栏保留“建立向量索引”按钮，点击后只读展示当前服务、模型和允许发送的文件类别，确认后才启动任务。每次至多处理 256 个待索引片段，不受列表筛选条件限制；任务进度和错误显示在顶部。配置缺失或没有允许发送的类别时，禁止执行并提示修改 `.env`。旧的 `/settings` 地址跳转到 `/assets`。

文件位置保持在数据目录中：`atlas.json` 为指令清单；`.agentatlas/assets.json` 为文件清单；`.agentatlas/search.sqlite3` 为本地检索索引；`.agentatlas/lifecycle/` 保存编辑日志、备份与审阅记录。同一数据目录通过进程锁限制为一个服务实例。

服务限定本机地址，并校验 Host、Origin 和跨站请求。文件访问经过清单授权，敏感路径、符号链接替换、只读类型和截断内容分别处理。设置接口仅返回密钥是否已经配置。

语义检索、向量索引和翻译需要在页面确认后执行。检索结果保存为有期限的本地快照，翻页与浏览器刷新读取结果快照。

## 验证

```bash
make check
make test
make browser-test
```

浏览器检查使用本机 Chrome；默认路径为 macOS 的 Chrome 安装位置，其他环境通过 `ATLAS_CHROME` 指定可执行文件。测试启动独立服务并创建真实样例文件，所有运行数据与报告位于已忽略的 `.agentatlas/work/`。测试不调用外部模型。

`make build` 输出 `frontend/dist/` 和后端 Python 安装包。部署目录需要同时包含前端构建产物；后端可通过 `--frontend-dir` 指向该目录。Python 安装包内包含平台注册信息和文件类型说明库。接口文档位于 `/api/docs`，OpenAPI 位于 `/api/openapi.json`。

## 维护资料

- [目录与路由设计](docs/restructure-plan.md)
- [领域定义](CONTEXT.md)
- [文件说明维护](docs/agent-file-design.md)
- [记忆读取规则](docs/memory-rules.md)
- [文件类型说明库](backend/src/atlas/guides/data/file-types.json)

新增平台时，在 `backend/src/atlas/platforms/<name>/` 声明平台路径、裁剪规则与读取规则，并补充对应回归检查。修改文件说明内容时，维护 `guides/data/file-types.json`；调整解析行为时，同时修改 `guides/service.py` 与其测试。
