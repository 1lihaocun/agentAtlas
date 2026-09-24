"""Conservative paired evaluation summaries (not significance estimates)."""


_STATUSES = ("completed", "timeout", "infra_error", "cancelled")
_KINDS = ("reconstruction_control", "codex_pilot", "real", "fixture")
_FIELDS = ("id", "status", "score", "safetyPassed", "critical",
           "executionKind", "fingerprint")


def _validate_rows(rows, arm):
    if not isinstance(rows, list) or not rows:
        raise ValueError("{} results must be a nonempty list".format(arm))
    for index, row in enumerate(rows):
        label = "{} row {}".format(arm, index)
        if not isinstance(row, dict) or any(field not in row for field in _FIELDS):
            raise ValueError("{} must contain all result fields".format(label))
        for field in ("id", "fingerprint"):
            if not isinstance(row[field], str) or not row[field]:
                raise ValueError("{} {} must be a nonempty string".format(label, field))
        if type(row["score"]) is not int or row["score"] not in (0, 1):
            raise ValueError("{} score must be an integer 0 or 1".format(label))
        for field in ("safetyPassed", "critical"):
            if type(row[field]) is not bool:
                raise ValueError("{} {} must be a boolean".format(label, field))
        if row["status"] not in _STATUSES:
            raise ValueError("{} has an unsupported status".format(label))
        if row["executionKind"] not in _KINDS:
            raise ValueError("{} has an unsupported executionKind".format(label))
        if row["status"] == "completed" and row["score"] == 1 and not row["safetyPassed"]:
            raise ValueError("{} reports an unsafe completed pass".format(label))


def compare_results(baseline, candidate):
    """Return a deterministic, fail-closed summary of two nonempty row lists.

    Rows are paired by nonempty string ``id``, not position. ``fingerprint``
    is a nonempty opaque string identifying the dataset/snapshot/task/checks;
    callers must generate it from those inputs. It and ``critical`` must match
    in each pair. All rows must have the same accepted ``executionKind``.
    Invalid schemas, pairing, or completed unsafe score-1 rows raise ValueError.

    ``total`` includes every pair, including incomplete executions. Pass counts
    count only completed score-1 rows in the respective arm; wins/regressions
    compare only pairs completed in both arms. ``unsafe`` counts case IDs with
    safetyPassed=False in either arm, once per pair regardless of status.

    Any non-completed row makes status ``inconclusive``, reason
    ``incomplete_results``, and both improvement flags False. Otherwise status
    is ``completed``. ``diagnosticImproved`` requires a win, no regression
    (including noncritical cases), and no unsafe cases. Only ``real`` execution
    can also set ``recommend`` True. Completed non-real results always use
    reason ``pilot_or_fixture``; real results use ``unsafe``, ``regressions``,
    ``improved``, or ``no_improvement`` in that precedence order.

    Inputs are not changed. These counts make no statistical significance claim.
    """
    _validate_rows(baseline, "baseline")
    _validate_rows(candidate, "candidate")
    before_by_id = {row["id"]: row for row in baseline}
    after_by_id = {row["id"]: row for row in candidate}
    if len(before_by_id) != len(baseline) or len(after_by_id) != len(candidate):
        raise ValueError("duplicate case IDs")
    if before_by_id.keys() != after_by_id.keys():
        raise ValueError("baseline and candidate case ID sets must match")
    candidate = [after_by_id[row["id"]] for row in baseline]
    kind = baseline[0]["executionKind"]
    for before, after in zip(baseline, candidate):
        if before["executionKind"] != kind or after["executionKind"] != kind:
            raise ValueError("executionKind must be uniform in both arms")
        for field in ("fingerprint", "critical"):
            if before[field] != after[field]:
                raise ValueError("{} mismatch for case {!r}".format(field, before["id"]))
    baseline_passed = sum(row["score"] for row in baseline if row["status"] == "completed")
    candidate_passed = sum(row["score"] for row in candidate if row["status"] == "completed")
    wins = regressions = unsafe = 0
    complete = True
    for before, after in zip(baseline, candidate):
        unsafe += not before["safetyPassed"] or not after["safetyPassed"]
        if before["status"] != "completed" or after["status"] != "completed":
            complete = False
        else:
            wins += after["score"] > before["score"]
            regressions += after["score"] < before["score"]
    improved = complete and wins > 0 and regressions == 0 and unsafe == 0
    reason = "improved" if improved else "no_improvement"
    if regressions:
        reason = "regressions"
    if unsafe:
        reason = "unsafe"
    if kind != "real":
        reason = "pilot_or_fixture"
    if not complete:
        reason = "incomplete_results"
    return {
        "wins": wins, "regressions": regressions, "unsafe": unsafe,
        "baselinePassed": baseline_passed, "candidatePassed": candidate_passed,
        "total": len(baseline), "status": "completed" if complete else "inconclusive",
        "recommend": improved and kind == "real", "reason": reason,
        "diagnosticImproved": improved,
    }
