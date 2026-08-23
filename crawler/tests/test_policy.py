"""The policy module (ADR-0005, ticket 04): one pure, table-driven
verdict per university per tick, plus the SLA age constants.

Three proofs:

1. VERDICT TABLE — each rule source triggers its documented decision and
   names the attention kind a human is needed for; a clean tick
   proceeds with nothing.
2. PRECEDENCE — block outranks warn outranks proceed; multiple triggered
   rules aggregate their kinds rather than masking each other.
3. SLA — 7 days open warns, 30 escalates; the constants live HERE (the
   attention module stores items, policy judges their age).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crawler import policy  # noqa: E402


def signals(**kw):
    base = dict(refresh_error=False, publish_blocked=False,
                gate_failures=0, pending_checks=0,
                pending_proposals=0, drift=0)
    base.update(kw)
    return base


class VerdictTableTest(unittest.TestCase):
    def test_a_clean_tick_proceeds(self):
        v = policy.verdict(signals())
        self.assertEqual(v.decision, "proceed")
        self.assertEqual(v.needs_human, ())

    def test_each_rule_row(self):
        cases = [
            (signals(refresh_error=True), "block", "refresh-error"),
            (signals(publish_blocked=True), "block", "blocked-publish"),
            (signals(gate_failures=2), "warn", "gate-failure"),
            (signals(pending_checks=1), "warn", "check-verdict"),
            (signals(pending_proposals=3), "warn", "proposal"),
            (signals(drift=1), "warn", "drift"),
        ]
        for sig, decision, kind in cases:
            with self.subTest(kind=kind):
                v = policy.verdict(sig)
                self.assertEqual(v.decision, decision)
                self.assertEqual(v.needs_human, (kind,))

    def test_an_unknown_signal_is_rejected(self):
        """Table-driven means the table is closed: a typo'd signal must
        fail loudly, not read as False."""
        with self.assertRaises(ValueError):
            policy.verdict(signals(gate_falures=1))


class PrecedenceTest(unittest.TestCase):
    def test_block_outranks_warn(self):
        v = policy.verdict(signals(publish_blocked=True, drift=2))
        self.assertEqual(v.decision, "block")

    def test_triggered_kinds_aggregate_in_table_order(self):
        v = policy.verdict(signals(publish_blocked=True, drift=2,
                                   gate_failures=1))
        self.assertEqual(v.needs_human,
                         ("blocked-publish", "gate-failure", "drift"))


class SlaTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(policy.WARN_AGE_DAYS, 7)
        self.assertEqual(policy.ESCALATE_AGE_DAYS, 30)

    def test_states(self):
        self.assertEqual(policy.sla_state(0), "ok")
        self.assertEqual(policy.sla_state(6), "ok")
        self.assertEqual(policy.sla_state(7), "warn")
        self.assertEqual(policy.sla_state(29), "warn")
        self.assertEqual(policy.sla_state(30), "escalate")
        self.assertEqual(policy.sla_state(300), "escalate")


if __name__ == "__main__":
    unittest.main()
