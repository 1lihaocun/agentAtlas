"""Gemini CLI 平台。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="gemini",
    label="Gemini CLI",
    user_roots=(".gemini",),
    project_roots=(".gemini",),
    scan_roots=(".gemini/GEMINI.md",),
    instruction_scan=True,
    instruction_names=("gemini.md",),
    first_level_allowed=("tmp",),  # Gemini 的会话存储，不按通用 tmp/cache 裁剪
)
