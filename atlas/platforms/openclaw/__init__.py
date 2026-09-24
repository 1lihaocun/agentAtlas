"""OpenClaw / OpenClaw 衍生平台：标准工作区与记忆检索规则。

覆盖 ``~/.openclaw*``（openclaw、openclaw-autoclaw 等衍生状态目录）。
"""
import re
from pathlib import Path

try:
    from ..registry import Platform
    from .. import semantics as _semantics
except ImportError:
    from registry import Platform
    import semantics as _semantics

_OPENCLAW = (("OpenClaw agent workspace", "https://docs.openclaw.ai/concepts/agent-workspace"),
             ("OpenClaw memory", "https://docs.openclaw.ai/concepts/memory"))


def resolve_memory(ctx):
    parts, roots, home = ctx["parts"], ctx["roots"], ctx["home"]
    profile = ctx["profile"]
    state = parts[0]
    expected_profile = "default" if state == ".openclaw" else state[len(".openclaw-"):]
    if expected_profile != profile:
        return _semantics.semantics()
    if len(parts) == 3 and parts[1] == "memory" and parts[2].endswith((".sqlite", ".sqlite3", ".db")):
        return _semantics.semantics(
            reader="OpenClaw 记忆检索后端候选（活动后端未验证）", kind="path_inference",
            detail="仅匹配 memory 目录下的数据库命名；当前官方内置存储路径已经不同",
            sources=_OPENCLAW + (("OpenClaw builtin memory engine", "https://docs.openclaw.ai/concepts/memory-builtin"),),
            role=("retrieval_database", "记忆检索数据库候选"), stage="unknown",
            notes=("数据库仅显示清单元数据，不读取表、索引、嵌入或内容。",
                   "不从文件名推测活动代理、工作区、项目映射或实际召回；可能是旧存储。"))
    if len(parts) < 3 or not (parts[1] == "workspace" or re.fullmatch(r"workspace-[A-Za-z0-9_-]+", parts[1])):
        return _semantics.semantics()
    tail = parts[2:]
    bootstrap = tail in (("USER.md",), ("MEMORY.md",))
    recall = len(tail) >= 2 and tail[0] == "memory" and tail[-1].endswith(".md")
    if not bootstrap and not recall:
        return _semantics.semantics()
    workspace = home / state / parts[1]
    applies = [root for root in roots if Path(root) == workspace or workspace in Path(root).parents]
    if tail == ("USER.md",):
        trigger, portion = "此目录被选为代理工作区时，每次会话加载", "用户模型文件，独立 4000 字符预算"
    elif tail == ("MEMORY.md",):
        trigger, portion = "此目录被选为工作区，且为主私密会话；不应在共享/群组会话加载", "受 bootstrap 文件及总预算限制的长期摘要"
    else:
        trigger = "此工作区启用记忆插件时，按任务调用 memory_search / memory_get"
        if len(tail) == 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-[^/]+)?\.md", tail[1]):
            trigger += "；日期为今天或昨天的笔记，在 bare /new 或 /reset 时也可自动加入"
        portion = "相关检索片段或指定文件/行；不承诺历史笔记全量注入"
    return _semantics.semantics(
        level="workspace", mode="automatic" if bootstrap else "retrieval",
        reader="OpenClaw 使用工作区 " + str(workspace) + " 的代理", trigger=trigger, portion=portion,
        inheritance="按代理工作区共享，不按任意代码仓库继承；其他工作区不自动合并",
        kind="path_inference", detail="标准工作区路径 + 官方文件规则；实际代理路由、配置和会话类型未验证",
        sources=_OPENCLAW, applies=applies,
        role=(("user_profile", "用户模型") if tail == ("USER.md",) else
              ("memory_summary", "长期记忆摘要") if bootstrap else ("topic_memory", "工作区检索笔记")),
        stage="foreground",
        notes=("只关联位于该工作区内的已知项目；工具可访问外部仓库不代表其自动获得这份记忆。",
               "memory 下的导入副本与源平台的原始记忆是不同资产。"))


PLATFORM = Platform(
    id="openclaw",
    label="OpenClaw",
    home_glob=(".openclaw*",),
    user_roots=(".openclaw-autoclaw/workspace",),
    scan_roots=(".openclaw-autoclaw/workspace",),
    instruction_scan=True,
    bootstrap_instructions=("soul.md", "identity.md", "tools.md", "heartbeat.md", "bootstrap.md"),
    session_database=True,
    resolve_memory=resolve_memory,
)
