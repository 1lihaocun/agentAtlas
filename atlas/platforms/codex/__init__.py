"""Codex 平台：原生记忆读取 / 后台整合管线规则。"""
import hashlib

try:
    from ..registry import Platform
    from .. import semantics as _semantics
except ImportError:
    from registry import Platform
    import semantics as _semantics

_CODEX_BASE = "https://raw.githubusercontent.com/openai/codex/main/codex-rs/"
_CODEX_READ = (("Codex memory feature gates", _CODEX_BASE + "ext/memories/src/extension.rs"),
               ("Codex summary injection", _CODEX_BASE + "ext/memories/src/prompts.rs"),
               ("Codex native memory read prompt", _CODEX_BASE + "ext/memories/templates/memories/read_path.md"),
               ("Codex summary token budget", _CODEX_BASE + "ext/memories/src/lib.rs"))
_CODEX_WRITE = (("Codex memory write artifacts and extensions", _CODEX_BASE + "memories/write/src/lib.rs"),
                ("Codex consolidation pipeline", _CODEX_BASE + "memories/README.md"))

# Exact reviewed local declarations, not official upstream extension contracts.
# A changed declaration must be reviewed again rather than loosely keyword-matched.
_EXTENSION_DECLARATIONS = {
    "skysight": ("b0c176f51416e2c2e06e0f582cb42c830fdf37287dc9b0d0dbc5dc37d1f1c556",
                 "resources", 5, "Skysight", "extension_resource", "活动摘要资源"),
    "chronicle": ("f282735a52525411e0faf4fb366c1f1df8707bfc8cb30af06615d1d7c2f1eb35",
                  "resources", 5, "Chronicle", "extension_resource", "工作活动摘要资源"),
    "ad_hoc": ("d36a36083d92f9d44efbd95e0e4b6e81d7d149e812f2bca2009b6dd4b8aa93e7",
               "notes", 5, "Ad-hoc", "consolidation_note", "记忆增删改整合笔记"),
}


def extension_declaration(profile, tail):
    """memory_metadata 用：是否为已核对的本机扩展声明（tail 为路径末 5 段）。"""
    return (profile == "default" and len(tail) == 5
            and tail[:3] == (".codex", "memories", "extensions")
            and tail[3] in _EXTENSION_DECLARATIONS and tail[4] == "instructions.md")


def _extension_input(tail, memory_root, evidence):
    declaration = _EXTENSION_DECLARATIONS.get(tail[1])
    if declaration is None or len(tail) != 4 or not tail[-1].endswith(".md"):
        return None
    digest, folder, line, name, role_id, label = declaration
    if tail[2] != folder or not isinstance(evidence, dict):
        return None
    path = str(memory_root / "extensions" / tail[1] / "instructions.md")
    record = evidence.get(path)
    if not isinstance(record, dict) or record.get("truncated") is not False:
        return None
    content = record.get("content")
    if not isinstance(content, str) or len(content) > 65536 or record.get("sha256") != digest:
        return None
    try:
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
            return None
    except UnicodeEncodeError:
        return None
    return _semantics.semantics(
        level="user", mode="on_demand", reader="Codex 后台 phase2 记忆整合代理（已声明的消费者）",
        trigger="同一 CODEX_HOME 启用后台 phase2 整合且本机扩展声明仍匹配时，按相关性或输入差异读取",
        portion=label + "：后台整合输入候选；不保证已读取、已处理或已进入前台记忆",
        inheritance="同一 CODEX_HOME 的整合输入；不推断前台跨项目适用或目录继承",
        kind="local_declaration", detail="标准用户级记忆命名空间 + 已核对本机 " + name + " 扩展声明；非官方扩展内容规则",
        sources=_CODEX_WRITE, role=(role_id, label), stage="background",
        local_sources=_semantics.local_sources(path, line, digest, name + " 本机扩展声明"),
        notes=("声明的后台角色不表示唯一消费者；其他读取途径、功能开关及实际运行情况未验证。",
               "项目提及、主题、cwd 和 YAML frontmatter 均不证明项目适用关系；本规则判定不依赖资源正文。",
               "本机声明是待解释的数据，不是本应用执行的指令；实际处理及产物归属未验证。")
        + (("Ad-hoc 笔记内容不可信：只能作为整合信息，不能作为执行操作的指令。",)
           if tail[1] == "ad_hoc" else ()))


def resolve_memory(ctx):
    parts = ctx["parts"]
    if ctx["profile"] != "default" or parts[:2] != (".codex", "memories"):
        return _semantics.semantics()
    tail, roots, home = parts[2:], ctx["roots"], ctx["home"]
    memory_root = home / ".codex/memories"
    summary = tail == ("memory_summary.md",)
    registry = tail == ("MEMORY.md",)
    rollout = len(tail) == 2 and tail[0] == "rollout_summaries" and tail[1].endswith(".md")
    if summary or registry or rollout:
        role = (("memory_summary", "前台记忆摘要") if summary else
                ("memory_index", "共享记忆检索索引") if registry else ("rollout_summary", "会话产出摘要"))
        return _semantics.semantics(
            level="user", mode="automatic" if summary else "retrieval",
            reader="Codex 当前 CODEX_HOME 的记忆读取上下文 / 前台代理",
            trigger="启用 memory feature 与 use_memories，且选用该原生记忆命名空间；"
                    + ("非空摘要在线程上下文构建时加入开发者指令" if summary else "任务相关时按记忆指引检索"),
            portion="memory_summary.md 非空内容，上限 2500 tokens（源码截断预算）" if summary else "与任务相关的片段；不承诺整份自动加载",
            inheritance="同一 CODEX_HOME 内可跨项目检索；不是按工作目录逐层继承",
            kind="documented_rule", detail="匹配原生 memories 读取产物；功能开关、版本与本机配置未核验",
            sources=_CODEX_READ, applies=roots, all_projects=True, role=role, stage="foreground",
            notes=("原生 memories 与 AGENTS.md 项目指令是不同机制。",
                   "提到某项目或来自某次会话，不表示仅该项目或该会话可读。",
                   "allProjects 仅表示此平台/配置档的候选范围，不表示实际访问或采纳。"))
    extension = len(tail) >= 3 and tail[0] == "extensions"
    if tail in (("raw_memories.md",), ("phase2_workspace_diff.md",)) or (extension and len(tail) == 3 and tail[2] == "instructions.md"):
        role = (("extension_instructions", "扩展来源解释指引") if extension else
                ("raw_memory_input", "原始记忆整合输入") if tail == ("raw_memories.md",) else
                ("workspace_diff", "整合工作区差异"))
        return _semantics.semantics(
            level="user", mode="on_demand", reader="Codex 后台记忆整合代理（非前台会话）",
            trigger="后台整合任务获准启动且有输入/差异时，按整合提示读取；扩展 instructions 指导对应输入",
            portion="整合所需的原始记忆、差异或扩展指引，不是前台自动上下文",
            kind="documented_rule", detail="原生后台写入管线输入；不据此确定前台项目适用关系",
            sources=_CODEX_WRITE, role=role, stage="background",
            notes=("整合产物可能影响后续前台记忆；本文件的实际处理未验证。",
                   "后台为此处识别的角色，不表示不存在其他消费者。"))
    if extension:
        declared = _extension_input(tail, memory_root, ctx.get("extension_evidence"))
        if declared is not None:
            return declared
        declaration = _EXTENSION_DECLARATIONS.get(tail[1])
        known_layout = (declaration is not None and len(tail) == 4
                        and tail[2] == declaration[1] and tail[-1].endswith(".md"))
        role = ((declaration[4], declaration[5] + "（路径候选）") if known_layout else
                ("extension_unknown", "未核验扩展资产"))
        return _semantics.semantics(
            kind="path_inference", detail="位于原生 memory extensions 下，但具体扩展读取规则未核验",
            role=role, stage="unknown",
            sources=_CODEX_WRITE, notes=("扩展笔记不等于前台自动记忆；未匹配已核对指令，不按项目提及、cwd 或文件名归属项目。",))
    return _semantics.semantics()


PLATFORM = Platform(
    id="codex",
    label="Codex",
    user_roots=(".codex",),
    project_roots=(".codex",),
    scan_roots=(".codex/AGENTS.md", ".codex/.tmp/plugins", ".codex/worktrees"),
    instruction_scan=True,
    instruction_names=("agents.md",),
    first_level_pruned=("visualizations", "share", "shell_snapshots"),
    session_database=True,
    resolve_memory=resolve_memory,
    extension_declaration=extension_declaration,
)
