"""Survey ensemble (fill-rate follow-up; architecture-review candidate 4).

The survey is the only non-deterministic module in onboarding, and it
got exactly one call — its variance wasted whole runs (the same MUSofia
seed gave 9 proposals, then 0; SWU lost two paid runs to it). The
deepening: k survey rounds, union of the URL sets, dedup by URL — the
deterministic verify step (already the expensive, trustworthy half)
stays the ranker via its gate-verified-field counts. ADR-0003
untouched: still propose-only.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler.onboarding import propose_onboarding  # noqa: E402
from crawler.tests.test_onboarding import FakeVerifyStore  # noqa: E402

LINKS = [("https://x/a", "Спец А"), ("https://x/b", "Спец Б"),
         ("https://x/c", "Спец В")]
STORE = FakeVerifyStore({"https://x/a": "text", "https://x/b": "text",
                         "https://x/c": "text"})


class _VaryingAdapter:
    """A different survey answer each call — the measured failure mode."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = 0

    def call(self, prompt, schema, model, tag):
        answer = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        if isinstance(answer, Exception):
            raise answer
        return {"programs": answer}, {"cost_usd": 0.01}


def _item(url, name):
    return {"url": url, "name": name, "reasoning": "n/a"}


class SurveyEnsembleTest(unittest.TestCase):
    def test_rounds_union_their_selections(self):
        adapter = _VaryingAdapter([
            [_item("https://x/a", "А")],
            [_item("https://x/b", "Б")],
            [],
        ])
        proposals, _ = propose_onboarding(
            "X", LINKS, adapter, STORE, survey_rounds=3)
        self.assertEqual(adapter.calls, 3)
        self.assertEqual({p.proposed_url for p in proposals},
                         {"https://x/a", "https://x/b"})

    def test_repeated_url_across_rounds_dedups(self):
        adapter = _VaryingAdapter([
            [_item("https://x/a", "А")],
            [_item("https://x/a", "А пак"), _item("https://x/c", "В")],
        ])
        proposals, _ = propose_onboarding(
            "X", LINKS, adapter, STORE, survey_rounds=2)
        urls = [p.proposed_url for p in proposals]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(set(urls), {"https://x/a", "https://x/c"})

    def test_one_failed_round_does_not_kill_the_survey(self):
        adapter = _VaryingAdapter([
            RuntimeError("transport"),
            [_item("https://x/b", "Б")],
        ])
        proposals, _ = propose_onboarding(
            "X", LINKS, adapter, STORE, survey_rounds=2)
        self.assertEqual([p.proposed_url for p in proposals],
                         ["https://x/b"])
        self.assertTrue(all(p.adapter_error is None for p in proposals))

    def test_all_rounds_failing_reports_the_adapter_error(self):
        adapter = _VaryingAdapter([RuntimeError("a"), RuntimeError("b")])
        proposals, _ = propose_onboarding(
            "X", LINKS, adapter, STORE, survey_rounds=2)
        self.assertEqual(len(proposals), 1)
        self.assertIsNotNone(proposals[0].adapter_error)

    def test_cost_sums_across_rounds(self):
        adapter = _VaryingAdapter([[_item("https://x/a", "А")], [], []])
        _, cost = propose_onboarding(
            "X", LINKS, adapter, STORE, survey_rounds=3)
        self.assertAlmostEqual(cost, 0.03)

    def test_single_round_is_the_old_behavior(self):
        adapter = _VaryingAdapter([[_item("https://x/a", "А")]])
        proposals, cost = propose_onboarding(
            "X", LINKS, adapter, STORE, survey_rounds=1)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(len(proposals), 1)


if __name__ == "__main__":
    unittest.main()
