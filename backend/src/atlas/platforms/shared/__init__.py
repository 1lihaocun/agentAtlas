
from ..registry import Platform

PLATFORM = Platform(
    id="shared",
    label="共享约定",
    user_roots=(".agents", ".config/superpowers"),
    project_roots=(".agents",),
    instruction_scan=True,
    instruction_names=("agents.md",),

)
