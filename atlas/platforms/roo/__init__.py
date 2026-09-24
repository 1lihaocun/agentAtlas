"""Roo Code 平台。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="roo",
    label="Roo Code",
    project_roots=(".roo",),
)
