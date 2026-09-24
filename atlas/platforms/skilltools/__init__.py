"""技能目录型工具平台（iFlow / Trae / Qoder / Kode 等十余个）。

本机存在一批 ``~/.<tool>/skills`` 目录，内容全部是指向
``~/.agents/skills/<name>`` 的符号链接；catalog 沿途不跟随目录软链，
因此它们当前不产生独立文件资产，真实技能内容已由 ``shared`` 平台的
``~/.agents`` 根覆盖。这里把它们登记为一个平台，一旦某个工具开始
落盘真实内容（自有技能 / 规则 / 记忆），扫描即可发现；届时若需要
单独的作用域规则，再拆分为独立子包。
"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

_MOUNT_TOOLS = (
    ".iflow", ".qoder", ".qoder-cn", ".kode", ".trae", ".trae-cn", ".neovate",
    ".reasonix", ".vibe", ".lingma", ".augment", ".pochi", ".factory",
    ".junie", ".kilocode", ".ona", ".openhands", ".terramind", ".moxby",
    ".rovodev", ".codebuddy", ".codestudio", ".codemaker", ".commandcode",
)

PLATFORM = Platform(
    id="skilltools",
    label="技能目录工具",
    user_roots=_MOUNT_TOOLS,
    scan_roots=(),
    instruction_scan=False,
    pruned_dirs=("logs", "log", "cache", "sessions", "session", "task-storage",
                 "observability", "auggie-path", "code-ratio", "diagnostics",
                 "binaries", "plugins"),
)
