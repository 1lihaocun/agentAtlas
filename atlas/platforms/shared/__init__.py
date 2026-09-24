"""共享约定平台：AGENTS.md 及跨工具通用文件。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="shared",
    label="共享约定",
    user_roots=(".agents", ".config/superpowers"),
    project_roots=(".agents",),
    instruction_scan=True,
    instruction_names=("agents.md",),
    first_level_allowed=(".config/superpowers",),
)
