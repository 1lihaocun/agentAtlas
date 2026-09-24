"""Cline / Roo Code 平台（项目级状态目录）。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="cline",
    label="Cline",
    project_roots=(".cline",),
    instruction_names=(".clinerules",),
)
