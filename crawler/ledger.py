"""The append-only value ledger + run pointer (ticket 03).

Every PASSing value from a run is appended to a JSONL ledger, keyed
Program x field x academic_year x run — years append, nothing overwrites
(RFC v2 Q6/Q9). ``current`` is a pointer file naming the promoted run_id;
Postgres (not built here — out of scope for this ticket) would be a
projection of whatever run the pointer names. The pointer only moves when
``expectations.check`` passes, so rollback is "write the previous run_id
back into the pointer file" — no data is ever deleted or rewritten.

academic_year ("valid_for") is inferred per value: a "YYYY/YYYY" token in
the value's own segments wins (the value states its own year, same
evidence discipline as RFC v2 Q9's `valid_for`); if no value carries one,
the run's declared ``academic_year`` parameter is the fallback. This is a
deliberately scoped-down stand-in for the full onboarding/discovery
`valid_for` design (ticket 05/06, v0.2) — good enough to key the ledger
and drive the year-lag expectation check now, not a claim that per-value
year extraction is solved.
"""
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

__all__ = [
    "LEDGER_NAME", "POINTER_NAME", "SUMMARIES_DIR", "append_run",
    "read_current", "write_current", "load_run_values", "diff_runs",
    "infer_academic_year", "run_id_for", "write_run_summary",
    "read_run_summary",
]

LEDGER_NAME = "ledger.jsonl"
POINTER_NAME = "current-run.json"
SUMMARIES_DIR = "run-summaries"

_YEAR_RANGE_RX = re.compile(r"\b(20\d{2})\s*/\s*(20\d{2})\b")
# a year introduced by «до уч.» / «до учебната» is a historical
# reference ("until academic year X it was called Y"), never the
# validity year of the value that happens to sit near it
_HISTORICAL_YEAR_RX = re.compile(r"до\s+уч(?:\.|ебната)\s*$")


def run_id_for(uni_id, now=None):
    """A fresh, collision-safe run_id. Timestamp for readability in the
    ledger/pointer files, uuid4 suffix so two runs started within the
    same second (routine in tests, and possible for fast replay runs)
    never collide and silently merge into one ledger entry."""
    ts = now or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return "{0}-{1}-{2}".format(uni_id, ts, uuid.uuid4().hex[:8])


# Fields whose VALUE genuinely varies by admission cycle (fee orders,
# admission rules are republished yearly). degree/duration/language are
# structurally stable across years for a given program — searching their
# segments for a "YYYY/YYYY" token finds unrelated noise instead (measured:
# a curriculum-revision cohort label, "Випуск 2021/2022", inside a duration
# field's segment, misread as the value's own year and blocking a first-
# ever publish on a fact that was never year-scoped to begin with).
YEAR_VARYING_FIELDS = frozenset({"tuition", "admission"})


def infer_academic_year(segments, value, fallback_academic_year, field=None):
    # type: (List[str], str, str, Optional[str]) -> str
    """A 'YYYY/YYYY' token in the value or its own segments wins, but ONLY
    for fields that actually vary by year (see YEAR_VARYING_FIELDS) — for
    everything else this returns the run's declared academic_year
    directly, since those fields have no year of their own to state.
    field=None searches regardless (back-compat for callers that don't
    know their field, e.g. ad hoc scripts)."""
    if field is not None and field not in YEAR_VARYING_FIELDS:
        return fallback_academic_year
    for text in (value, *(segments or ())):
        for m in _YEAR_RANGE_RX.finditer(text or ""):
            if _HISTORICAL_YEAR_RX.search(text, max(0, m.start() - 24),
                                          m.start()):
                # «до уч. 2020/2021 г.» dates a subject's FORMER NAME,
                # not this value. SU's current 2026/2027 ordinance says
                # «ДЗИ по философия (до уч. 2020/2021 г. „Философски
                # цикъл“)», and reading that as the value's cycle
                # blocked a publish on an up-to-date document
                # (measured 2026-08-23).
                continue
            return "{0}/{1}".format(m.group(1), m.group(2))
    return fallback_academic_year


# ------------------------------------------------------------------- ledger
def append_run(ledger_dir, uni_id, run_id, report, academic_year):
    # type: (str, str, str, dict, str) -> Path
    """Append one line per non-null PASS value in ``report`` to the
    university's ledger. Returns the ledger path. Idempotent to call
    twice with the same run_id is NOT guaranteed (append-only, by design
    — re-running a run_id would duplicate entries; callers mint a fresh
    run_id per run via run_id_for())."""
    ledger_path = Path(ledger_dir) / uni_id / LEDGER_NAME
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def entries_of(program_id, fields, scope):
        for field_name, rec in fields.items():
            if rec["status"] != "PASS":
                continue
            prov = rec.get("provenance", {})
            entry = {
                "run_id": run_id,
                "program_id": program_id,
                "field": field_name,
                "academic_year": infer_academic_year(
                    prov.get("source_snippets", []), rec["value"],
                    academic_year, field=field_name),
                "value": rec["value"],
                "method": rec.get("method"),
                "tier": rec.get("tier"),
                "source_url": prov.get("source_url"),
                "retrieved_at": prov.get("retrieved_at"),
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "scope": scope,
            }
            yield entry

    with open(ledger_path, "a", encoding="utf-8") as f:
        for program in report["programs"]:
            for entry in entries_of(program["program_id"],
                                    program["fields"], "program"):
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return ledger_path


def load_run_values(ledger_dir, uni_id, run_id):
    # type: (str, str, str) -> Dict[tuple, dict]
    """{(program_id, field, academic_year, None): entry}.

    The fourth key slot is historical (it held the Offering key before
    ADR-0006 dropped Offerings enumeration); kept as a literal None so
    ledgers written before the change still load and diff cleanly.
    """
    ledger_path = Path(ledger_dir) / uni_id / LEDGER_NAME
    out = {}
    if not ledger_path.exists():
        return out
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry["run_id"] == run_id:
                key = (entry["program_id"], entry["field"],
                       entry["academic_year"], entry.get("offering_key"))
                out[key] = entry
    return out


def diff_runs(ledger_dir, uni_id, run_id_a, run_id_b):
    # type: (str, str, Optional[str], str) -> List[dict]
    """Value-level diff of run_id_b against run_id_a (None = no prior
    run — every value in b is reported as 'new'). One entry per
    (program_id, field, academic_year, None) that changed, was
    added, or was removed."""
    a = load_run_values(ledger_dir, uni_id, run_id_a) if run_id_a else {}
    b = load_run_values(ledger_dir, uni_id, run_id_b)
    changes = []
    # the historical fourth key slot is None, and Python 3 refuses
    # to order None against str -- sort on a total-order projection rather
    # than on the raw key.
    for key in sorted(set(a) | set(b),
                      key=lambda k: tuple("" if p is None else p for p in k)):
        va = a.get(key)
        vb = b.get(key)
        if va is None:
            changes.append({"key": key, "change": "added", "new_value": vb["value"]})
        elif vb is None:
            changes.append({"key": key, "change": "removed", "old_value": va["value"]})
        elif va["value"] != vb["value"]:
            changes.append({"key": key, "change": "changed",
                            "old_value": va["value"], "new_value": vb["value"]})
    return changes


# ------------------------------------------------------------------ pointer
def read_current(ledger_dir, uni_id):
    # type: (str, str) -> Optional[str]
    """The promoted run_id, or None if nothing has ever been promoted."""
    pointer_path = Path(ledger_dir) / uni_id / POINTER_NAME
    if not pointer_path.exists():
        return None
    return json.loads(pointer_path.read_text(encoding="utf-8"))["run_id"]


def write_current(ledger_dir, uni_id, run_id):
    # type: (str, str, str) -> None
    """Move the pointer. This IS rollback: call it with a previous
    run_id to revert — nothing is deleted, the ledger already has every
    run's values, only the pointer's target changes."""
    pointer_path = Path(ledger_dir) / uni_id / POINTER_NAME
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pointer_path.with_name(POINTER_NAME + ".tmp")
    tmp.write_text(json.dumps({
        "run_id": run_id,
        "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, ensure_ascii=False), encoding="utf-8")
    tmp.replace(pointer_path)


# ------------------------------------------------------------ run summaries
def write_run_summary(ledger_dir, uni_id, run_id, summary):
    # type: (str, str, str, dict) -> None
    """Persist expectations.summarize()'s output for a run so a later
    run can compare against it without needing to keep the full report
    around. One small JSON file per run, never overwritten."""
    path = Path(ledger_dir) / uni_id / SUMMARIES_DIR / (run_id + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")


def read_run_summary(ledger_dir, uni_id, run_id):
    # type: (str, str, Optional[str]) -> Optional[dict]
    if run_id is None:
        return None
    path = Path(ledger_dir) / uni_id / SUMMARIES_DIR / (run_id + ".json")
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
