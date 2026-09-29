
from ..registry import Platform

PLATFORM = Platform(
    id="opencode",
    label="OpenCode",
    user_roots=(".config/opencode", ".local/share/opencode", ".local/state/opencode"),
    project_roots=(".opencode",),
    scan_roots=(".config/opencode/AGENTS.md",),
    instruction_scan=True,

    category_dirs={"storage": "session"},
    session_database=True,
)
