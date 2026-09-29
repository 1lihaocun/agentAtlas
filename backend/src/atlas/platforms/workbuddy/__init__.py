# 文件库收录 ~/.workbuddy 与 ~/.workbuddy-ai，读取规则未核实。
# ~/WorkBuddy AI 与 ~/Workbuddy 仅由指令扫描器检查约定指令文件。
from ..registry import Platform

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
