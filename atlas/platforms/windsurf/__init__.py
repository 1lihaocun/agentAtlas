"""Windsurf 平台（.windsurf 与 .codeium/windsurf 两个状态目录）。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="windsurf",
    label="Windsurf",
    user_roots=(".windsurf", ".codeium/windsurf"),
    project_roots=(".windsurf",),
    scan_roots=(".codeium/windsurf/memories",),
    instruction_scan=True,
    instruction_names=(".windsurfrules",),
)
