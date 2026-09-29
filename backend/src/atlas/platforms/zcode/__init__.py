"""ZCode 平台（目录已收录；读取规则未核实）。

本机存在 ``~/.zcode``（cli/v2/computer-use/workspace）。会话产物、
数据库与插件缓存按平台裁剪规则收录其文本资产；官方读取规则未核对，
不做作用范围推断。
"""
from ..registry import Platform

PLATFORM = Platform(
    id="zcode",
    label="ZCode",
    user_roots=(".zcode",),
    scan_roots=(),
    instruction_scan=False,
    pruned_dirs=("artifacts", "exec", "image-cache", "log", "db", "rollout",
                 "shell-snapshots", "bash-startup", "certs", "checkpoints", "crash",
                 "logs", "telemetry", "browser-profile", "computer-use",
                 "plugin-workspace", "cache", "runtime"),
    first_level_pruned=("computer-use", "plugin-workspace", "workspace", "v2"),
    session_database=True,
)
