"""Cursor 平台。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="cursor",
    label="Cursor",
    user_roots=(".cursor",),
    project_roots=(".cursor",),
    scan_roots=(".cursor/rules",),
    instruction_scan=True,
    instruction_names=(".cursorrules",),
    pruned_dirs=("extensions",),
)
