"""Cline / Roo Code 平台（项目级状态目录）。"""
from ..registry import Platform

PLATFORM = Platform(
    id="cline",
    label="Cline",
    project_roots=(".cline",),
    instruction_names=(".clinerules",),
)
