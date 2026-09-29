"""平台声明载体：各平台子包从这里导入 Platform。"""


class Platform:
    """平台声明。字段缺省表示「无此约定」，聚合时按需跳过。

    id                    平台标识（catalog platform 字段与前端筛选值）。
    label                 中文展示名（文档与维护用，不进入接口）。
    user_roots            HOME 下的平台目录（相对路径，含首层 "."）。
    home_glob             HOME 下的 glob 模式（如 openclaw 的 ".openclaw*"）。
    project_roots         项目根下的平台目录。
    scan_roots            指令地图扫描根；缺省不参与指令地图。
    instruction_scan      scan_roots 是否进入指令地图扫描。
    instruction_names     归属本平台的全局指令文件名（AGENTS.md 等约定）。
    bootstrap_instructions 本平台视为指令的引导文件名（openclaw 系）。
    pruned_dirs           任意层级额外裁剪的目录名（平台限定）。
    first_level_pruned    仅平台根第一层裁剪的目录名。
    first_level_allowed   第一层豁免通用裁剪的目录名（如 gemini tmp）。
    category_dirs         目录名 -> 类别 的平台提示（如 zcode projects->session）。
    session_database      SQLite 等数据库是否按「会话」类别收录。
    resolve_memory        记忆作用域解析器 resolve_memory(ctx) -> semantics。
    extension_declaration 记忆扩展声明匹配 extension_declaration(profile, tail)。
    """

    def __init__(self, id, label="", user_roots=(), home_glob=(), project_roots=(),
                 scan_roots=None, instruction_scan=False, instruction_names=(),
                 bootstrap_instructions=(), pruned_dirs=(), first_level_pruned=(),
                 first_level_allowed=(), category_dirs=None, session_database=False,
                 resolve_memory=None, extension_declaration=None):
        self.id = id
        self.label = label
        self.user_roots = tuple(user_roots)
        self.home_glob = tuple(home_glob)
        self.project_roots = tuple(project_roots)
        self.scan_roots = None if scan_roots is None else tuple(scan_roots)
        self.instruction_scan = bool(instruction_scan)
        self.instruction_names = tuple(instruction_names)
        self.bootstrap_instructions = tuple(bootstrap_instructions)
        self.pruned_dirs = tuple(pruned_dirs)
        self.first_level_pruned = tuple(first_level_pruned)
        self.first_level_allowed = tuple(first_level_allowed)
        self.category_dirs = dict(category_dirs or {})
        self.session_database = bool(session_database)
        self.resolve_memory = resolve_memory
        self.extension_declaration = extension_declaration
