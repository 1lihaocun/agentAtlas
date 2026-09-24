"""Paired evaluation gates; no external services or third-party packages."""
import copy
import importlib.util
import unittest


def row(case_id="a", **changes):
    value = {
        "id": case_id, "status": "completed", "score": 0,
        "safetyPassed": True, "critical": False, "executionKind": "real",
        "fingerprint": "dataset/snapshot/task/checks:" + case_id,
    }
    value.update(changes)
    return value


class ScoringTests(unittest.TestCase):
    def compare(self, baseline, candidate):
        from atlas.eval.scoring import compare_results
        return compare_results(baseline, candidate)

    def test_real_improvement(self):
        self.assertIsNotNone(importlib.util.find_spec("atlas.eval"),
                             "paired scoring package must exist")
        result = self.compare([row()], [row(score=1)])
        self.assertEqual(result, {
            "wins": 1, "regressions": 0, "unsafe": 0,
            "baselinePassed": 0, "candidatePassed": 1, "total": 1,
            "status": "completed", "recommend": True, "reason": "improved",
            "diagnosticImproved": True,
        })

    def test_pairs_by_id_not_position_without_mutating_inputs(self):
        baseline = [row("a"), row("b", score=1)]
        candidate = [row("b", score=1), row("a", score=1)]
        original = copy.deepcopy((baseline, candidate))
        result = self.compare(baseline, candidate)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["regressions"], 0)
        self.assertEqual(result["total"], 2)
        self.assertEqual((baseline, candidate), original)

    def test_rejects_empty_duplicate_and_missing_results(self):
        cases = [([], []), ([], [row()]), ([row()], []),
                 ([row(), row()], [row()]), ([row()], [row(), row()]),
                 ([row("a")], [row("b")]),
                 ([row("a"), row("b")], [row("a")])]
        for baseline, candidate in cases:
            with self.subTest(baseline=baseline, candidate=candidate):
                with self.assertRaises(ValueError):
                    self.compare(baseline, candidate)

    def test_schema_is_strict_in_both_arms(self):
        invalid = {
            "score": [True, False, 0.0, 1.0, -1, 2, "1", None],
            "safetyPassed": [0, 1, "true", None],
            "critical": [0, 1, "false", None],
            "status": ["missing", "success", "", None, []],
            "executionKind": ["pilot", "", None, []],
            "id": ["", None, [], 1, True],
            "fingerprint": ["", None, [], True],
        }
        for field, values in invalid.items():
            for value in values:
                for arm in (0, 1):
                    with self.subTest(field=field, value=value, arm=arm):
                        arms = [[row()], [row(score=1)]]
                        arms[arm][0][field] = value
                        with self.assertRaises(ValueError):
                            self.compare(*arms)
        for field in row():
            for arm in (0, 1):
                with self.subTest(missing=field, arm=arm):
                    arms = [[row()], [row(score=1)]]
                    del arms[arm][0][field]
                    with self.assertRaises(ValueError):
                        self.compare(*arms)
        for value in (None, {}, "results", [None], [1]):
            for arm in (0, 1):
                with self.subTest(container=value, arm=arm):
                    arms = [[row()], [row(score=1)]]
                    arms[arm] = value
                    with self.assertRaises(ValueError):
                        self.compare(*arms)

    def test_pair_identity_and_critical_must_match(self):
        for field, value in (("fingerprint", "different-dataset-or-checks"),
                             ("critical", True)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    self.compare([row()], [row(score=1, **{field: value})])

    def test_execution_kind_must_be_uniform_within_and_across_arms(self):
        for arm in (0, 1):
            with self.subTest(arm=arm):
                arms = [[row("a"), row("b")], [row("a"), row("b", score=1)]]
                arms[arm][1]["executionKind"] = "fixture"
                with self.assertRaisesRegex(ValueError, "executionKind"):
                    self.compare(*arms)
        with self.assertRaisesRegex(ValueError, "executionKind"):
            self.compare([row()], [row(score=1, executionKind="codex_pilot")])

    def test_non_real_improvement_is_only_diagnostic(self):
        for kind in ("reconstruction_control", "codex_pilot", "fixture"):
            with self.subTest(kind=kind):
                result = self.compare([row(executionKind=kind)],
                                      [row(score=1, executionKind=kind)])
                self.assertEqual(result["wins"], 1)
                self.assertTrue(result["diagnosticImproved"])
                self.assertFalse(result["recommend"])
                self.assertEqual(result["reason"], "pilot_or_fixture")

    def test_incomplete_execution_is_inconclusive_and_retained(self):
        for status in ("timeout", "infra_error", "cancelled"):
            for arm in (0, 1):
                with self.subTest(status=status, arm=arm):
                    arms = [[row("a"), row("b", score=1)],
                            [row("a", score=1), row("b", score=1)]]
                    arms[arm][1]["status"] = status
                    result = self.compare(*arms)
                    self.assertEqual(result["status"], "inconclusive")
                    self.assertFalse(result["recommend"])
                    self.assertFalse(result["diagnosticImproved"])
                    self.assertEqual(result["reason"], "incomplete_results")
                    self.assertEqual(result["total"], 2)
                    self.assertEqual(result["wins"], 1)
                    self.assertEqual(result["regressions"], 0)
                    self.assertEqual(result["baselinePassed"], 0 if arm == 0 else 1)
                    self.assertEqual(result["candidatePassed"], 1 if arm == 1 else 2)

    def test_incomplete_pairs_cannot_create_wins_or_regressions(self):
        for arm in (0, 1):
            for scores in ((0, 1), (1, 0)):
                with self.subTest(arm=arm, scores=scores):
                    arms = [[row(score=scores[0])], [row(score=scores[1])]]
                    arms[arm][0]["status"] = "infra_error"
                    result = self.compare(*arms)
                    self.assertEqual(result["wins"], 0)
                    self.assertEqual(result["regressions"], 0)
                    self.assertEqual(result["total"], 1)
                    self.assertEqual(result["status"], "inconclusive")

    def test_unsafe_pass_is_rejected_in_either_arm(self):
        for arm in (0, 1):
            with self.subTest(arm=arm):
                arms = [[row()], [row(score=1)]]
                arms[arm][0].update(score=1, safetyPassed=False)
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    self.compare(*arms)

    def test_unsafe_case_in_either_arm_blocks_recommendation(self):
        for unsafe_arms in ((0,), (1,), (0, 1)):
            with self.subTest(unsafe_arms=unsafe_arms):
                arms = [[row("a"), row("b")], [row("a", score=1), row("b")]]
                for arm in unsafe_arms:
                    arms[arm][1]["safetyPassed"] = False
                result = self.compare(*arms)
                self.assertEqual(result["unsafe"], 1)
                self.assertEqual(result["wins"], 1)
                self.assertEqual(result["candidatePassed"], 1)
                self.assertFalse(result["recommend"])
                self.assertFalse(result["diagnosticImproved"])
                self.assertEqual(result["reason"], "unsafe")

    def test_regressions_block_even_when_noncritical_and_net_positive(self):
        for critical in (False, True):
            with self.subTest(critical=critical):
                baseline = [row("a"), row("b"), row("c", score=1, critical=critical)]
                candidate = [row("c", critical=critical), row("b", score=1), row("a", score=1)]
                result = self.compare(baseline, candidate)
                self.assertEqual(result["wins"], 2)
                self.assertEqual(result["regressions"], 1)
                self.assertEqual(result["baselinePassed"], 1)
                self.assertEqual(result["candidatePassed"], 2)
                self.assertFalse(result["recommend"])
                self.assertFalse(result["diagnosticImproved"])
                self.assertEqual(result["reason"], "regressions")

    def test_unchanged_is_not_improvement_and_is_order_independent(self):
        result = self.compare([row("a", score=1), row("b")],
                              [row("b"), row("a", score=1)])
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["regressions"], 0)
        self.assertFalse(result["recommend"])
        self.assertFalse(result["diagnosticImproved"])
        self.assertEqual(result["reason"], "no_improvement")


if __name__ == "__main__":
    unittest.main()
