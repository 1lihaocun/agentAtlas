"""Hermes 平台：内置记忆与待审提案规则。"""
try:
    from ..registry import Platform
    from .. import semantics as _semantics
except ImportError:
    from registry import Platform
    import semantics as _semantics

_HERMES = (("Hermes Persistent Memory", "https://hermes-agent.nousresearch.com/docs/user-guide/features/memory"),
           ("Hermes Profiles", "https://hermes-agent.nousresearch.com/docs/user-guide/profiles"))


def resolve_memory(ctx):
    parts = ctx["parts"]
    if not parts or parts[0] != ".hermes":
        return _semantics.semantics()
    tail = parts[1:]
    profile = ctx["profile"]
    expected_profile = "default"
    if len(tail) >= 3 and tail[0] == "profiles":
        expected_profile, tail = tail[1], tail[2:]
    if profile != expected_profile:
        return _semantics.semantics()
    roots = ctx["roots"]
    if len(tail) == 3 and tail[:2] == ("pending", "memory") and tail[2].endswith(".json"):
        return _semantics.semantics(
            level="profile", mode="none", reader="Hermes 写入审批流程（非会话记忆上下文）",
            trigger="批准后才写入正式记忆；该待审 JSON 本身不作为记忆快照加载",
            portion="待审批写入提案，不读取或解析其内容", kind="documented_rule",
            detail="匹配配置档 pending/memory/<id>.json 待审存储",
            sources=_HERMES + (("Hermes write approval source", "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/tools/write_approval.py"),),
            role=("pending_memory_write", "待审批记忆写入"), stage="unknown",
            notes=("批准状态、目标存储及实际影响未验证。",))
    if len(tail) == 2 and tail[0] == "memories" and tail[1] in {"MEMORY.md", "USER.md"}:
        return _semantics.semantics(
            level="profile", mode="automatic", reader="Hermes 配置档 " + profile + " 的会话",
            trigger="该配置档启用对应内置记忆存储时，在会话开始加载；未检查本机开关",
            portion="受配置容量约束的存储内容，作为会话开始时的冻结快照",
            inheritance="仅同一 Hermes home / 配置档跨项目共享；不回退或继承其他配置档记忆",
            kind="documented_rule", detail="匹配内置 MEMORY.md / USER.md 的标准位置",
            sources=_HERMES, applies=roots, all_projects=True,
            role=("user_profile", "用户模型快照") if tail[1] == "USER.md" else ("memory_summary", "记忆快照"),
            stage="foreground",
            notes=("allProjects 仅指同一平台与配置档内，不代表所有代理或配置档。",
                   "会话中保存的更改不代表已进入既有会话快照；未检查外部 memory provider。"))
    return _semantics.semantics()


PLATFORM = Platform(
    id="hermes",
    label="Hermes",
    user_roots=(".hermes",),
    project_roots=(".hermes",),
    scan_roots=(".hermes/skills", ".hermes/hermes-agent"),
    instruction_scan=True,
    session_database=True,
    resolve_memory=resolve_memory,
)
