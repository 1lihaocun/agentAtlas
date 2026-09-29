
from ..registry import Platform

PLATFORM = Platform(
    id="continue",
    label="Continue",
    user_roots=(".continue",),
    # 任意 Markdown 规则尚未接入指令地图，文件库仍扫描平台目录。
)
