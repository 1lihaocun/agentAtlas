"""WorkBuddy / WorkBuddy AI 平台（目录已收录；读取规则未核实）。

本机存在 ``~/.workbuddy``、``~/.workbuddy-ai``、``~/WorkBuddy AI``、
``~/Workbuddy``。目录形态（SOUL/IDENTITY/BOOTSTRAP/USER.md、memory/、
projects/<编码路径>/<uuid>.jsonl）与 openclaw 系相似，但官方加载规则
尚未核对，因此不标注任何作用范围；见 docs/memory-rules.md。
"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="workbuddy",
    label="WorkBuddy",
    user_roots=(".workbuddy", ".workbuddy-ai"),
    scan_roots=(),
    instruction_scan=False,
    pruned_dirs=("binaries", "logs", "traces", "plugins", "app", "shell-snapshots",
                 "security", "connectors-marketplace", "blobs", "file-tree-manifests",
                 "file-history", "browser-profile", ".workbuddy-sqlite-migrations",
                 "marketplaces", "cache"),
    first_level_pruned=("binaries", "logs", "traces", "plugins", "app"),
    category_dirs={"projects": "session"},
    session_database=True,
)
