"""GitHub Copilot 平台。"""
from ..registry import Platform

PLATFORM = Platform(
    id="copilot",
    label="GitHub Copilot",
    user_roots=(".copilot",),
    project_roots=(".github/instructions", ".github/prompts"),
    instruction_names=("copilot-instructions.md",),
)
