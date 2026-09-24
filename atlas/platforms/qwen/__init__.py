"""Qwen Code 平台。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="qwen",
    label="Qwen Code",
    user_roots=(".qwen",),
    scan_roots=(".qwen/QWEN.md",),
    instruction_scan=True,
    instruction_names=("qwen.md",),
)
