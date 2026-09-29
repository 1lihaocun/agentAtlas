"""共享的语义与上下文构造器：memory_scope 与各平台 resolver 共用。"""
from copy import deepcopy
from pathlib import Path

CHECKED_DATE = "2026-09-22"
_LEVELS = {"user": "用户级", "profile": "配置档级", "workspace": "工作区级",
           "project": "项目级", "directory": "目录级", "session": "会话级", "unknown": "范围未知"}
_MODES = {"automatic": "条件自动加载", "on_demand": "按需读取", "retrieval": "检索召回",
          "manual": "人工读取", "none": "不直接加载", "unknown": "加载方式未知"}


def semantics(level="unknown", mode="unknown", reader="读取者未知", trigger="未识别加载规则",
              portion="未知", inheritance="不推断目录继承或跨项目共享", kind="unknown",
              detail="仅有目录清单，不能确认运行时范围", sources=(), applies=(), all_projects=False,
              notes=(), role=None, stage=None, local_sources=()):
    result = {"level": level, "label": _LEVELS[level], "appliesTo": list(applies),
              "allProjects": all_projects, "reader": reader,
              "loading": {"mode": mode, "label": _MODES[mode], "trigger": trigger, "portion": portion},
              "inheritance": inheritance,
              "basis": {"kind": kind, "label": {"documented_rule": "官方规则（条件适用）",
                         "path_inference": "路径推断", "local_declaration": "本机声明（条件适用）",
                         "unknown": "依据不足"}[kind],
                        "detail": detail + "；规则核对：" + CHECKED_DATE,
                        "sources": [{"title": title, "url": url} for title, url in sources]},
              "observation": {"state": "unverified", "label": "未验证实际读取"},
              "notes": list(notes)}
    if role is not None:
        result["role"] = {"id": role[0], "label": role[1]}
    if stage is not None:
        result["pipeline"] = {"stage": stage, "label": {
            "foreground": "前台记忆读取", "background": "后台记忆整合", "unknown": "消费阶段未知"}[stage]}
    if local_sources:
        result["basis"]["localSources"] = deepcopy(list(local_sources))
    return result


def context(asset, roots, home):
    """Lexical context for a platform resolver; no I/O of any kind."""
    if home is None:
        return None  # 无效 HOME 时不回落到当前用户目录。
    path = asset.get("path")
    if not isinstance(path, str) or not path or "\x00" in path:
        return None
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        return None
    try:
        parts = path.relative_to(home).parts
    except ValueError:
        parts = ()
    return {"asset": asset, "path": path, "parts": parts, "roots": list(roots),
            "home": home, "profile": asset.get("profile", "default")}


def local_sources(path, line, digest, label):
    return ({"path": path, "line": line, "sha256": digest, "label": label},)
