"""`crawler onboard` end-to-end through the CLI (regression).

Found live 2026-08-24 running onboard against a real university: the
onboard branch in __main__.py hands `attention.detect_proposals` the
OnboardingReport DATACLASS run_onboarding returns, but
detect_proposals (like every other caller — refresh.py, its own tests)
expects the WRITTEN PROPOSAL DICT (uni_id + proposals as plain data).
`report.get("proposals")` doesn't exist on a dataclass, so `onboard`
crashed with an AttributeError immediately after writing a perfectly
good proposal file — the exact write-then-crash a human onboarding a
fresh site would hit on the very first run.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import crawler.llm_tail as llm_tail  # noqa: E402
import crawler.onboarding as onboarding_mod  # noqa: E402
from crawler.__main__ import main as cli_main  # noqa: E402
from crawler.tests.test_onboarding import (  # noqa: E402
    FakeLinkFetcher,
    FakeSurveyAdapter,
    FakeVerifyStore,
)


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class OnboardCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.configs_dir = Path(self.tmp.name) / "configs"
        self.configs_dir.mkdir()
        self.out_dir = Path(self.tmp.name) / "out"

        fetcher = FakeLinkFetcher({
            "https://uni.example/seed":
                b'<a href="/p1">Specialty One</a>'})
        store = FakeVerifyStore({"https://uni.example/p1": "plain text"})
        self._real_fetcher = onboarding_mod.build_fetcher_and_store
        onboarding_mod.build_fetcher_and_store = (
            lambda *a, **k: (fetcher, store))
        self._real_adapter = llm_tail.CLIAdapter
        llm_tail.CLIAdapter = lambda **kw: FakeSurveyAdapter()
        self.addCleanup(self._restore)

    def _restore(self):
        onboarding_mod.build_fetcher_and_store = self._real_fetcher
        llm_tail.CLIAdapter = self._real_adapter

    def test_onboard_does_not_crash_after_writing_the_proposal(self):
        code, out, err = run_cli([
            "onboard", "FRESH", "--seed", "https://uni.example/seed",
            "--configs", str(self.configs_dir), "--out",
            str(self.out_dir)])
        self.assertEqual(code, 0, err)
        self.assertIn("proposal:", out)
        proposal_path = self.out_dir / "FRESH" / "onboarding-proposal.json"
        self.assertTrue(proposal_path.exists())


if __name__ == "__main__":
    unittest.main()
