# AgentAtlas 目录、路由与维护设计

设计日期：2026-09-24。

## 开源资料与设计依据

| 已核查的资料 | 采用的组织方式 |
| --- | --- |
| [Bulletproof React](https://raw.githubusercontent.com/alan2207/bulletproof-react/master/docs/project-structure.md) | `app` 组合应用，`features` 聚合业务，`shared` 提供公共能力；检查模块导入边界 |
| [FastAPI 完整项目模板](https://github.com/fastapi/full-stack-fastapi-template) | 前后端分别管理代码、依赖与检查命令 |
| [模板前端说明](https://raw.githubusercontent.com/fastapi/full-stack-fastapi-template/master/frontend/README.md) | React、TypeScript、Vite 与真实浏览器验收 |
| [FastAPI 多文件应用](https://fastapi.tiangolo.com/tutorial/bigger-applications/) | 每个业务模块提供 `APIRouter`，入口创建并组合业务实例 |
| [React Router Data Routing](https://reactrouter.com/start/data/routing) | 使用显式嵌套路由、持续挂载的应用布局与按页面加载代码 |

目录划分依据 AgentAtlas 的实际功能：文件发现、指令规则、记忆作用域、文件编辑、检索、审阅、重复同步、说明、设置和后台任务分别维护。

## 根目录与运行边界

| 目录 | 职责 |
| --- | --- |
| `frontend/` | React 应用、TypeScript 类型、npm 依赖、构建配置 |
| `backend/` | Python 包、uv 依赖、模块与 HTTP 测试 |
| `tests/e2e/` | 启动独立后端并操作真实 Chrome 的验收脚本 |
| `tests/fixtures/` | 纳入版本管理的样例文件 |
| `scripts/` | 开发进程管理与安装包检查 |
| `docs/` | 领域说明、设计与维护资料 |
| `.env` | 翻译和 embedding 配置，仅手工编辑 |
| `.agentatlas/` | 本地清单、索引、缓存、编辑记录与备份 |
| `.agentatlas/work/` | 独立测试环境、中间文件和浏览器报告 |

Python 包入口为 `atlas serve`、`atlas scan` 和 `atlas eval`。`--workspace` 显式选择数据目录，源代码安装位置不影响运行数据位置。生产运行由后端提供 `frontend/dist/`；开发运行由 Vite 代理 `/api` 请求。

## 前端模块

| 目录 | 负责内容 |
| --- | --- |
| `src/app/` | 路由、布局、导航、查询容器、文件补充信息组合与统一样式 |
| `src/features/assets/` | 文件列表、平台、用途、项目、排序与分页 |
| `src/features/instructions/` | 指令地图、用户级文件、项目和目录浏览、读取规则 |
| `src/features/memories/` | 作用域、存放目录、处理阶段、标题、内容关联与统计 |
| `src/features/files/` | 文件内容、章节、草稿、版本冲突、翻译与外部编辑器 |
| `src/features/search/` | 本地关键词、语义与混合查询、结果快照、向量索引执行与发送确认 |
| `src/features/reviews/` | 审阅列表、读取证据、保留和延后决定 |
| `src/features/duplicates/` | 重复组、同步源与逐个文件结果 |
| `src/features/file-guide/` | 类型说明、命名字段与生成来源 |
| `src/features/jobs/` | 扫描入口、任务进度与失败状态 |
| `src/shared/api/` | HTTP 客户端、错误结构与公共接口类型 |
| `src/shared/ui/` | 分页、文件表格、确认对话框与加载状态 |
| `src/shared/lib/` | 地址参数、动作接口与格式化 |

功能目录只导入本功能和公共模块。跨功能页面由 `app` 组合，ESLint 检查依赖方向。文件查看器接收应用提供的补充信息，草稿和正文编辑由 `files` 管理。

## 页面路由与状态

| 路由 | 页面与地址参数 |
| --- | --- |
| `/` | 使用 replace 导航至 `/assets` |
| `/assets` | 全部文件；`platform`、`category`、`project`、`q`、`mode`、`sort`、`page`、`pageSize` |
| `/instructions` | 指令地图；`hideWorktrees`、`page`、`pageSize` |
| `/instructions/user` | 用户级指令；`page`、`pageSize` |
| `/instructions/directory` | 指令目录；`path`、`hideWorktrees`、`effective`、`page`、`pageSize` |
| `/memories/scope` | 记忆作用域；平台、档案、项目、作用层级、处理阶段、关键词和分页 |
| `/memories/directory` | 记忆存放目录；`path`、`recursive`、筛选条件和分页 |
| `/reviews` | 审阅；`bucket`、`days`、`staleDays`、`evidence` 和分页 |
| `/duplicates` | 重复组；`group` 和分页 |
| `/file-types`、`/file-types/:typeId` | 文件说明；`q`、文件上下文 `path` |
| `/settings` | 旧入口，使用 replace 导航至 `/assets` |
| `/files` | 独立文件页；`path`、`panel`、`section` |

普通列表页通过 `file` 打开文件侧栏，`panel=raw|sections|translation` 选择面板。目录和文件使用标准 URL 编码后的完整路径，显示名称独立于路径身份。

筛选和页码保存在地址中，浏览器前进后退恢复相应位置。TanStack Query 保存查询结果；查询标识包含当前条件和分页，清单版本变化触发相关查询失效。

草稿由持续挂载的 `DraftProvider` 按文件路径维护，包含内容、原始版本、光标与滚动位置。关闭侧栏后可从草稿列表重新打开。刷新和关闭页面提示未保存内容。模型配置仅通过 `.env` 修改，不提供前端设置表单。

保存发生冲突时，保留草稿并读取最新源文件。用户可以查看最新内容，明确更新版本依据后继续编辑；后续保存仍然检查文件版本。

## 后端模块

所有模块位于 `backend/src/atlas/`。

| 目录 | 职责 |
| --- | --- |
| `app/` | FastAPI 工厂、服务实例、应用生命周期、跨功能工作流 |
| `http/` | 总路由、响应结构、Host/Origin 与请求大小检查 |
| `core/` | 配置、分页、原子写入、编辑事务与审阅存储 |
| `assets/` | 文件发现、清单、分类、项目身份、路径策略和分页 |
| `instructions/` | 指令扫描、目录数据与 Codex 读取规则 |
| `files/` | 授权访问、常规文件检查、读写、章节定位与保存 |
| `memories/` | 作用域、目录拓扑、元数据与记忆查询 |
| `search/` | SQLite 内容索引、关键词和向量查询、结果快照 |
| `reviews/` | 使用记录、证据、审阅分类和决定 |
| `duplicates/` | 重复组与同步 |
| `guides/` | 类型说明、命名字段、生成来源与说明库资源 |
| `settings/` | 只读加载 `.env` 服务配置、公开配置查询与授权版本，不提供写入接口 |
| `translation/` | 模型配置、翻译分段与缓存 |
| `jobs/` | 单任务执行、排队刷新、进度与退出等待 |
| `platforms/` | 平台注册与各平台读取规则 |
| `evaluation/` | 数据集、隔离执行、评分与命令行 |

HTTP 路由负责请求解析，业务服务负责操作与约束，`app/workflows.py` 组织保存后的重新扫描及索引刷新。文件读取与写入统一经过 `files` 授权。

`core/storage.py` 维护现有 SQLite 结构、编辑事务和备份的一致性。文件业务与审阅业务各自调用需要的公开操作。数据目录中的现有记录继续沿用，模块移动无需数据库迁移。

## 分页、任务与云端执行

文件、记忆、审阅、重复组和检索结果由后端分页，返回 `items`、`total`、`page`、`pageSize`、`catalogVersion`。默认每页 50 条，支持 25、50、100 条；超出末页时返回有效末页并更新地址。指令地图基于完整目录快照排列目录与本层文件，并对展示结果分页。

记忆存放目录保留真实祖先关系。统计分别给出本层文件、直接子目录和包含子目录的文件数量。`recursive=true` 控制文件集合，目录关系保持独立。

扫描与索引使用单个后台执行器。重复启动返回冲突；保存产生的刷新请求合并到待执行任务。进程锁防止同一个数据目录被两个服务同时打开。应用退出等待任务结束。

关键词查询只读取本地 SQLite。向量和混合查询需要 POST 提交并确认，返回 `searchId`；后续分页通过 GET 读取有期限的结果快照。清单或设置变化使结果过期。

翻译和向量索引由明确操作触发。查询自动重试与窗口重新聚焦刷新关闭，页面导航不会重新发送云端操作。模型调用失败返回错误，完整成功的翻译才写入缓存。

## 接口与资源

Pydantic 定义请求校验与公共分页响应，OpenAPI 位于 `/api/openapi.json`。前端接口类型集中在 `shared/api/types.ts`，接口变更同时更新调用端与真实 HTTP 检查。

文件说明库唯一维护位置为 `backend/src/atlas/guides/data/file-types.json`，随 Python 包一起构建。`ATLAS_GUIDE_PATH` 支持显式选择外部说明库。

接口返回 JSON；缺失构建资源返回 404；页面地址返回 React 入口。包含句点的文件类型标识支持直接刷新。密钥仅保存在本地私有文件，公开接口只返回是否配置。

## 验证与维护入口

- `make install`：按锁文件安装前后端依赖。
- `make check`：后端静态检查、前端类型与导入边界检查。
- `make test`：模块及真实 HTTP 回归检查。
- `make browser-test`：构建后启动独立服务，检查真实浏览器交互。
- `make build`：生成前端产物与 Python 安装包。
- `make dev`、`make serve`：开发运行与构建产物运行。

测试使用 `.agentatlas/work/` 内的独立文件、SQLite 和服务进程。浏览器记录页面错误及网络请求，验收分页与刷新、搜索、跨页面草稿、文件和章节保存、记忆目录、说明详情、全部路由以及云端确认前的请求行为。

外部模型调用不属于自动回归检查。云端实际返回结果需要使用明确授权的服务验证。
