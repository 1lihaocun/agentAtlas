"""Claude Code 平台：官方 auto memory 读取规则。"""
import re

from ..registry import Platform
from .. import semantics as _semantics

_CLAUDE = (("Claude Code auto memory", "https://code.claude.com/docs/en/memory"),)


def resolve_memory(ctx):
    parts, roots = ctx["parts"], ctx["roots"]
    if (ctx["profile"] != "default" or len(parts) < 5
            or parts[:2] != (".claude", "projects") or parts[3] != "memory"
            or ctx["path"].suffix != ".md"):
        return _semantics.semantics()
    candidates = [root for root in roots if re.sub(r"[^A-Za-z0-9]", "-", root) == parts[2]]
    mapped = len(candidates) == 1
    summary = parts[4:] == ("MEMORY.md",)
    return _semantics.semantics(
        level="project" if mapped else "unknown", mode="automatic" if summary else "on_demand",
        reader="Claude Code 对应项目的主会话（普通子代理不自动继承）",
        trigger="auto memory 启用且使用此默认存储路径时，" + ("会话开始加载索引" if summary else "代理按任务需要用文件工具打开主题文件"),
        portion="MEMORY.md 前 200 行或 25KB，先到的限制为准" if summary else "按需打开的主题内容，不在启动时全量注入",
        inheritance="同一仓库子目录 / worktree 按官方规则共享；不解析 git 元数据，不推测未提供的 worktree 关联",
        kind="path_inference", detail="官方自动记忆规则 + 候选项目路径正向编码唯一匹配" if mapped else "识别自动记忆目录，但项目编码无匹配或存在歧义",
        sources=_CLAUDE, applies=candidates if mapped else (),
        role=("memory_index", "自动记忆入口索引") if summary else ("topic_memory", "按需主题记忆"),
        stage="foreground",
        notes=("不把连字符反解为斜杠；候选映射是路径推断而非运行时验证。",
               "autoMemoryDirectory、CLAUDE_CONFIG_DIR 和项目目录名覆盖未检查。",
               "主题类型 user / feedback / project / reference 不改变其加载目录范围。"))


PLATFORM = Platform(
    id="claude",
    label="Claude Code",
    user_roots=(".claude",),
    project_roots=(".claude",),
    instruction_scan=True,
    instruction_names=("claude.md",),
    session_database=True,
    resolve_memory=resolve_memory,
)
