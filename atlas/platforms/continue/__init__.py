"""Continue 平台。"""
try:
    from ..registry import Platform
except ImportError:
    from registry import Platform

PLATFORM = Platform(
    id="continue",
    label="Continue",
    user_roots=(".continue",),
    scan_roots=(".continue/rules",),
    instruction_scan=True,
)
