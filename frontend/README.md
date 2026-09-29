# 前端开发规范

## 模块组织

`src/app` 负责路由、应用布局和跨功能组合。`src/features` 按项目功能组织页面和业务交互。`src/shared` 提供公共接口、组件和工具，功能之间的依赖由 ESLint 检查。

## 下拉菜单

所有下拉框使用 [Ant Design Dropdown](https://ant.design/components/dropdown-cn/)，通过 `src/shared/ui/FilterDropdown.tsx` 统一实现。覆盖文件筛选、排序、记忆筛选、检索模式、分页数量、审阅条件与章节选择。新增下拉框复用此组件。

ESLint 检查原生 `select` 的使用，确保页面下拉框具备统一的滚动和尺寸调节行为。

- 使用 `menu.items` 定义选项，`menu.selectable` 和受控 `selectedKeys` 表示当前选项。选项的 `value` 保留接口原始值，显示文字放在 `label`。
- 使用点击触发，触发元素为具有名称和展开状态的按钮，支持键盘打开、菜单导航、Escape 关闭与选择后返回焦点。
- 菜单选择后关闭；筛选和排序保存到 URL，更新条件时将页码重置为第一页。清除筛选、刷新页面和浏览器前进后退需要同步显示当前值。
- 项目、配置档案和章节菜单启用 `searchable`，按完整名称或路径搜索。
- 菜单默认宽度由最长选项的实际内容决定，最大宽度受当前窗口限制。默认高度随实际内容增长，最大为视口高度的 65%。所有菜单提供独立的垂直滚动区域与右下角尺寸调节手柄，拖动手柄可以调节宽度和高度。搜索区域固定在菜单上方，选项区域随高度变化滚动。滚动条的具体外观遵循操作系统设置。
- 项目选项使用 `label` 表示名称，使用 `description` 表示完整路径。名称单独一行加粗，路径在下一行以较浅颜色和等宽字体显示；搜索同时匹配名称和路径。
- 触发按钮和菜单中的长路径显示省略号，并保留完整标题；用户可以调整菜单宽度查看更长的内容。
- 选中项使用浅绿色背景，常规颜色为 `#eef4ec`，鼠标经过时为 `#e5eee2`。
- 通过 `popupRender` 扩展搜索区域，通过 `classNames`、`styles` 或公开的菜单样式接口控制外观。组件样式位于 `FilterDropdown.css`，应用主题由入口的 `ConfigProvider` 设置。
- 下拉组件仅处理展示和选择，业务页面负责 URL、请求参数与分页更新。

## 验证

修改后执行 `npm --prefix frontend run lint` 和 `npm --prefix frontend run build`。涉及下拉框时，执行 `npm --prefix frontend test`，使用真实后端和 Chrome 验证菜单、搜索、选中状态、分页重置、地址恢复、滚动及拖动调节大小。

首次运行测试前，在仓库根目录执行 `make install` 和 `npm --prefix frontend run build`；测试使用 `backend/.venv/bin/atlas` 与 `frontend/dist`。默认使用 macOS 的 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，其他系统通过 `ATLAS_CHROME` 指定本机 Chrome/Chromium 可执行文件。回归测试暂停并恢复隔离后端进程验证等待状态，需要支持 `SIGSTOP` / `SIGCONT` 的 macOS 或 Linux。测试不截图、不替换 HTTP 响应、不调用外部模型，数据与报告保存在 `.agentatlas/work/`。
