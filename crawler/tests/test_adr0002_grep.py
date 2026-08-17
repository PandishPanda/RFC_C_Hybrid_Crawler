"""ADR-0002 grep test — Artifact construction is store-only.

Scans every .py file under crawler/ for Artifact-constructor call sites.
Allowed locations:
  - render/store modules (module basename containing 'store' or 'render'),
    where rendering knowledge lives and Artifacts are legitimately built;
  - crawler/tests/ (provenance's own tests build Artifacts from vendored
    fixture text).
Any other call site reproduces the wrong-artifact failure class the ADR
kills in the type system, and fails this test.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CRAWLER_DIR = Path(__file__).resolve().parents[1]
CALL_SITE = re.compile(r"\bArtifact\s*\(")


def _allowed(path):
    rel = path.relative_to(CRAWLER_DIR)
    if rel.parts[0] == "tests":
        return True
    stem = path.stem.lower()
    return "store" in stem or "render" in stem


class TestArtifactStoreOnlyConstruction(unittest.TestCase):
    def test_no_artifact_construction_outside_store_render_and_tests(self):
        violations = []
        for py in sorted(CRAWLER_DIR.rglob("*.py")):
            if _allowed(py):
                continue
            for lineno, line in enumerate(
                    py.read_text(errors="ignore").splitlines(), 1):
                if CALL_SITE.search(line):
                    violations.append(
                        f"{py.relative_to(CRAWLER_DIR)}:{lineno}: "
                        f"{line.strip()}")
        self.assertEqual(
            violations, [],
            "Artifact constructed outside render/store modules and tests "
            "(ADR-0002 store-only construction invariant):\n"
            + "\n".join(violations))

    def test_grep_actually_sees_the_tree(self):
        """Guard against the scan silently scanning nothing."""
        files = list(CRAWLER_DIR.rglob("*.py"))
        self.assertIn("provenance.py", {f.name for f in files})
        self.assertGreaterEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main()
