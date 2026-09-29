"""Conservative, file-level review decisions from retained-log evidence.

Nothing in this module writes instruction files. A review recommendation is not
an assertion that a file is unused or harmful, including when a counter is zero.
"""
import hashlib
import math
import os
from pathlib import Path
import stat

DAY = 86400
BUCKETS = ("excluded", "unknown", "active", "recent", "kept", "snoozed", "review")
REASONS = {
    "protected_scope": "全局指令或工作树副本，不参与自动审阅筛选",
    "file_unavailable": "文件不存在、不可读取、已改变指向或超过安全读取上限",
    "invalid_edit_time": "修改时间缺失或在未来，无法判断修改年龄",
    "insufficient_observation": "日志来源不完整或当前工具尚不支持此文件的观测",
    "observed_or_estimated_signal": "在已保留日志中观察到读取证据或推算曝光",
    "mention_without_read_confirmation": "有路径提及，但不能确认已读取；不自动建议淘汰",
    "recently_modified": "文件系统记录的最近修改尚未达到审阅门槛",
    "user_kept_this_version": "你已选择保留这个内容版本",
    "user_snoozed_this_version": "这个内容版本尚未到再次审阅的时间",
    "no_signal_in_retained_logs": "已保留的 Codex 日志中未观察到信号，仅供人工审阅",
}


def _threshold(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("%s 必须为 1–%s 的整数" % (name, maximum))
    return value


def classify_record(record, now, stale_days=90):
    _threshold(stale_days, "staleDays", 3650)
    if record.get("worktree") or record.get("scope") == "user":
        return "excluded", "protected_scope"
    if not record.get("readable", False):
        return "unknown", "file_unavailable"
    mtime = record.get("lastModified")
    if not isinstance(mtime, (int, float)) or not math.isfinite(mtime) or mtime > now:
        return "unknown", "invalid_edit_time"
    if record.get("sourceStatus") != "available" or not record.get("supported", False):
        return "unknown", "insufficient_observation"
    if (record.get("estimatedSessions") or 0) > 0 or (record.get("confirmedReads") or 0) > 0:
        return "active", "observed_or_estimated_signal"
    if (record.get("mentions") or 0) > 0:
        return "unknown", "mention_without_read_confirmation"
    if now - mtime < stale_days * DAY:
        return "recent", "recently_modified"
    decision = record.get("decision")
    if decision and decision.get("version") == record.get("version"):
        if decision.get("action") == "keep":
            return "kept", "user_kept_this_version"
        if decision.get("action") == "snooze" and (decision.get("until") or 0) > now:
            return "snoozed", "user_snoozed_this_version"
    return "review", "no_signal_in_retained_logs"


def partition(records, now, stale_days=90):
    """Partition an already normalized inventory without hiding any row."""
    _threshold(stale_days, "staleDays", 3650)
    rows, seen = [], set()
    counts = {name: 0 for name in BUCKETS}
    for record in records:
        if record["path"] in seen:
            raise ValueError("duplicate realpath")
        seen.add(record["path"])
        bucket, reason = classify_record(record, now, stale_days)
        rows.append(dict(record, bucket=bucket, reason=reason, reasonText=REASONS[reason]))
        counts[bucket] += 1
    rows.sort(key=lambda row: (BUCKETS.index(row["bucket"]), row.get("lastModified") or 0, row["path"]))
    return {"rows": rows, "counts": counts, "total": len(rows)}


def _file_snapshot(path, limit):
    """Bounded/no-follow read: never hash a preview or follow a retargeted path."""
    absolute = os.path.abspath(path)
    if absolute != os.path.realpath(absolute):
        raise OSError("symbolic path")
    fd = os.open(absolute, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise OSError("not a bounded regular file")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise OSError("file changed while reading")
        current = os.stat(absolute, follow_symlinks=False)
        if (current.st_ino, current.st_dev, current.st_size, current.st_mtime_ns) != (
                before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns):
            raise OSError("file replaced while reading")
    raw.decode("utf-8")
    return hashlib.sha256(raw).hexdigest(), before.st_mtime, before.st_size


def build_retirement(index, usage, store, now, days=30, stale_days=90,
                     max_file_bytes=2 * 1024 * 1024):
    """Join the instruction index, live file versions, evidence and decisions.

    Store.review(path, version) returns a version-scoped decision or None;
    Store.edits() supplies known committed edit times. Source failures stay visible.
    Legacy counters and missing evidence rows cannot produce recommendations.
    """
    _threshold(days, "days", 365)
    _threshold(stale_days, "staleDays", 3650)
    if not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError("now must be a finite timestamp")
    if type(max_file_bytes) is not int or max_file_bytes < 1:
        raise ValueError("max_file_bytes must be a positive integer")
    if not isinstance(index, dict) or not isinstance(index.get("files"), list):
        raise ValueError("尚未加载指令索引")
    usage = usage or {}
    usable = (usage.get("schemaVersion") == 2 and usage.get("tool") == "Codex"
              and usage.get("days") == days)
    evidence = {}
    if usable:
        for row in usage.get("files", []):
            evidence[os.path.abspath(row["path"])] = row
    last_edits = {}
    for edit in store.edits():
        when = edit.get("created_at")
        if edit.get("status") == "committed" and isinstance(when, (int, float)) and math.isfinite(when):
            p = os.path.abspath(edit["path"])
            last_edits[p] = max(last_edits.get(p, when), when)
    records = []
    seen = set()
    for entry in index["files"]:
        # Never substitute realpath after a symlink change: the indexed path is
        # the target of authorization, not whatever it points at today.
        path = os.path.abspath(entry["path"])
        if path in seen:
            continue
        seen.add(path)
        item = evidence.get(path)
        supported = bool(item and item.get("supported")) and Path(path).name in ("AGENTS.md", "AGENTS.override.md")
        source_status = (item.get("sourceStatus", usage.get("sourceStatus", "unsupported"))
                         if item else "unsupported")
        record = {
            "path": path, "displayPath": entry.get("displayPath") or path,
            "kind": entry.get("kind", "other"), "scope": entry.get("scope", "project"),
            "worktree": bool(entry.get("worktree")), "supported": supported,
            "sourceStatus": source_status, "readable": False, "version": None,
            "lastModified": None, "bytes": entry.get("bytes", 0),
            "estimatedSessions": item.get("estimatedSessions") if item else None,
            "confirmedReads": item.get("confirmedReads") if item else None,
            "mentions": item.get("mentions") if item else None,
            "lastEffective": item.get("lastEffective") if item else None,
            "decision": None, "canDecide": False,
        }
        try:
            version, mtime, size = _file_snapshot(path, max_file_bytes)
        except (OSError, UnicodeError, ValueError):
            pass
        else:
            record.update(version=version, lastModified=max(mtime, last_edits.get(path, mtime)),
                          fileMtime=mtime, lastAtlasEdit=last_edits.get(path), bytes=size, readable=True)
            record["decision"] = store.review(path, version)
            record["canDecide"] = not record["worktree"] and record["scope"] != "user"
        records.append(record)
    result = partition(records, now, stale_days)
    result.update({
        "schemaVersion": 1, "tool": "Codex", "days": days, "staleDays": stale_days,
        "generatedAt": now, "observationScope": "retained_logs_only",
        "sourceStatus": usage.get("sourceStatus", "unsupported") if usable else "unsupported",
        "warnings": list(usage.get("warnings", [])) if usable else ["legacy_or_mismatched_usage"],
        "sessionsScanned": usage.get("sessionsScanned", 0) if usable else None,
    })
    return result
