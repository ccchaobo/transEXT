"""Run every verification entry point in this repository and report one table.

    python scripts/verify_all.py                # everything
    python scripts/verify_all.py --group tests  # one group
    python scripts/verify_all.py --list         # what would run, and why

**The entry points are listed explicitly, never globbed.** This repository contains about fifty
``diagnose_*``, ``probe_*``, ``derive_*`` and ``debug_*`` scripts. Those are investigation tools
written while chasing a specific defect: they print what they found and are not assertions about
the product. A glob over ``scripts/*.py`` would run them, take their exit code, and present the
result as verification - which is the same failure mode as a metric that cannot fail on the bug
it was written for. Anything added here has to be a script that *decides* something.

``scripts/verify_order_by_eye.py`` is deliberately **not** in this list. It prints the reference
reading order beside the pipeline's so a human can compare them, and returns 0 whatever it finds;
its own docstring says it is not a metric. The first run of this harness included it, and its exit
code was reported as a verification verdict it never made.

Each entry point runs in its own process, with its own data directory, so one script cannot leave
state that makes the next one pass or fail. The harness records the exit code, the wall time and
the last line that looks like a verdict, because a script that prints PASS and exits non-zero is
a finding rather than a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

#: Matches a line that states a result.  Ordered by how much it says.
VERDICT_RE = re.compile(
    r"\b(PASS|FAIL|OK|VERDICT|SUPPORTING|WARN|INCOMPLETE|SKIP|SKIPPED)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Entry:
    name: str
    argv: tuple[str, ...]
    group: str
    timeout: int = 600
    #: Why this is verification and not an investigation tool.
    why: str = ""

    @property
    def command(self) -> list[str]:
        if self.argv[0] == "node":
            return list(self.argv)
        return [PYTHON, *self.argv]


@dataclass
class Outcome:
    entry: Entry
    code: int
    seconds: float
    verdict: str
    tail: str
    timed_out: bool = False
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


ENTRIES: tuple[Entry, ...] = (
    # ---- the test suite ------------------------------------------------------------------
    Entry("pytest tests", ("-m", "pytest", "tests", "-q"), "tests", 1800,
          "the unit and integration suites, as one process"),
    Entry("pytest tests (verbose collection)", ("-m", "pytest", "tests", "--collect-only", "-q"),
          "tests", 600, "collection alone, so an import error cannot hide behind a filter"),

    # ---- the coordinate contract ---------------------------------------------------------
    Entry("check_rotation", ("scripts/check_rotation.py",), "contract", 300,
          "all 16 corner mappings against a table measured from rendered pixels, plus round trip"),
    Entry("verify_rotation", ("scripts/verify_rotation.py",), "contract", 300,
          "rotation handling against the rendered image"),
    Entry("verify_coordinate_fix", ("scripts/verify_coordinate_fix.py",), "contract", 300,
          "the normalised-coordinate fix, re-measured"),

    # ---- layout and reading order --------------------------------------------------------
    Entry("measure_layout", ("scripts/measure_layout.py",), "layout", 900,
          "page+region and segmentation accuracy over the corpus"),
    Entry("measure_line_order", ("scripts/measure_line_order.py",), "layout", 600,
          "line-level reading order, inversions counted"),
    Entry("measure_reading_order", ("scripts/measure_reading_order.py",), "layout", 600,
          "word-level reading order by LCS"),
    Entry("measure_segmentation", ("scripts/measure_segmentation.py",), "layout", 600,
          "paragraph segmentation against ground truth"),
    Entry("measure_block_types", ("scripts/measure_block_types.py",), "layout", 600,
          "block classification against ground truth"),
    Entry("verify_layout_metrics", ("scripts/verify_layout_metrics.py",), "layout", 600,
          "the layout metrics themselves, recomputed independently"),
    Entry("verify_column_order", ("scripts/verify_column_order.py",), "layout", 600,
          "column-order behaviour on the two-column samples"),

    # ---- extraction, OCR, storage ---------------------------------------------------------
    Entry("spike_extract", ("scripts/spike_extract.py",), "extract", 900,
          "extraction and conversion over the corpus and the anomaly suite"),
    Entry("spike_ocr", ("scripts/spike_ocr.py",), "ocr", 1800,
          "OCR recognition and reading order against per-page ground truth"),
    Entry("check_confidence_signal", ("scripts/check_confidence_signal.py",), "ocr", 600,
          "whether OCR confidence separates correct from incorrect lines"),
    Entry("check_dpi_reproducible", ("scripts/check_dpi_reproducible.py",), "ocr", 600,
          "that the calibrated render resolution is reproducible"),
    Entry("smoke_storage", ("scripts/smoke_storage.py",), "storage", 600,
          "leasing, retry, fencing and partial completion"),
    Entry("env_inventory", ("scripts/env_inventory.py", "--quiet"), "environment", 600,
          "every installed dependency and its licence"),

    # ---- the wire contract -----------------------------------------------------------------
    Entry("gen_openapi", ("scripts/gen_openapi.py",), "contract", 600,
          "regenerate the OpenAPI artifact from the application"),
    Entry("check_openapi", ("scripts/check_openapi.py",), "contract", 600,
          "the artifact on disk resolves and matches the app"),

    # ---- evidence for the milestones --------------------------------------------------------
    Entry("evidence_parse", ("scripts/evidence_parse.py",), "evidence", 900,
          "a real import and parse, printed as evidence"),
    Entry("evidence_worker", ("scripts/evidence_worker.py",), "evidence", 900,
          "a real job lifecycle, printed as evidence"),
    Entry("evidence_translation", ("scripts/evidence_translation.py",), "evidence", 900,
          "what the translation path can and cannot demonstrate without a credential"),

    # ---- the running product ----------------------------------------------------------------
    Entry("verify_frontend_contract", ("scripts/verify_frontend_contract.py",), "product", 900,
          "every field the frontend reads, against a real payload"),
    Entry("verify_overlay_alignment", ("scripts/verify_overlay_alignment.py",), "product", 1800,
          "overlay boxes against rendered pixels, with a mirrored control"),
    Entry("verify_live_acceptance", ("scripts/verify_live_acceptance.py",), "product", 1800,
          "both processes as real processes, everything over HTTP"),
    Entry("verify_checks_can_fail", ("scripts/verify_checks_can_fail.py",), "product", 1800,
          "each new frontend check, reverted, and confirmed to fail"),
    Entry("frontend runtime tests", ("node", "--test", "tests/frontend/reader.test.cjs"), "frontend", 600,
          "real frontend functions with controlled API and DOM boundaries"),
    Entry("browser acceptance", ("scripts/verify_browser_acceptance.py",), "frontend", 600,
          "real browser rendering and interaction; AI UI fixtures explicitly labelled"),
    # NOTE: that one edits `apps/web/js/app.js` and `styles.css` for the length of its run, so it
    # must never be run in parallel with `verify_frontend_contract`.  This tuple is executed in
    # order and it is listed after that entry; running the product group with more than one worker
    # would make both of them lie.

    # ---- measurement ------------------------------------------------------------------------
    Entry("perf_harness", ("-m", "benchmarks.rr_m0.perf_harness",), "perf", 2400,
          "P50/P95 per stage and peak memory"),
)


def verdict_of(output: str) -> str:
    """The last line that states a result, or the last line that says anything."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if VERDICT_RE.search(line):
            return line[:160]
    return lines[-1][:160] if lines else ""


def run(entry: Entry, data_root: Path) -> Outcome:
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        # A private data directory per entry point.  The user's own var/ may be held open by a
        # running server, and a script that found a document left by another script would report
        # on a state nobody asked for.
        "RR_DATA_DIR": str(data_root / entry.name.replace(" ", "_").replace("/", "_")),
    }
    started = time.monotonic()
    try:
        finished = subprocess.run(
            entry.command,
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=entry.timeout,
        )
        seconds = time.monotonic() - started
        output = (finished.stdout or "") + (finished.stderr or "")
        return Outcome(entry, finished.returncode, seconds, verdict_of(output), output[-2500:], output=output)
    except subprocess.TimeoutExpired as expired:
        seconds = time.monotonic() - started
        output = ""
        for stream in (expired.stdout, expired.stderr):
            if stream:
                output += stream if isinstance(stream, str) else stream.decode("utf-8", "replace")
        return Outcome(entry, 124, seconds, f"TIMEOUT after {entry.timeout}s", output[-2500:],
                       timed_out=True, output=output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", default=None,
                        help="run only these groups (repeatable)")
    parser.add_argument("--list", action="store_true", help="list the entry points and exit")
    parser.add_argument("--keep", action="store_true",
                        help="keep each entry point's isolated data directory")
    parser.add_argument("--output", type=Path, help="保存逐入口完整日志、执行时间和机器可读报告")
    args = parser.parse_args()

    entries = [e for e in ENTRIES if not args.group or e.group in args.group]
    if args.list:
        for entry in entries:
            print(f"{entry.group:12} {entry.name:34} {' '.join(entry.argv)}")
            print(f"{'':12} {'':34} {'':0}{entry.why}")
        print(f"\n{len(entries)} entry points in {len({e.group for e in entries})} groups")
        return 0

    if not entries:
        print(f"no entry points in groups {args.group}", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp(prefix="rr-verify-all-"))
    outcomes: list[Outcome] = []
    started = time.monotonic()
    try:
        for index, entry in enumerate(entries, 1):
            print(f"[{index}/{len(entries)}] {entry.group}: {entry.name} ...", flush=True)
            outcome = run(entry, root)
            outcomes.append(outcome)
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                name = re.sub(r"[^a-zA-Z0-9_-]", "_", entry.name)
                (args.output / f"{name}.log").write_text(outcome.output, encoding="utf-8")
                report = {"note": "退出成功表示入口执行成功；度量结果及未接入能力需另行核对。",
                    "completed": len(outcomes), "total": len(entries), "outcomes": [
                        {"name": row.entry.name, "group": row.entry.group, "command": row.entry.command,
                         "exit_code": row.code, "seconds": row.seconds, "ok": row.ok,
                         "timed_out": row.timed_out, "verdict": row.verdict}
                        for row in outcomes]}
                (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            mark = "PASS" if outcome.ok else "FAIL"
            print(f"      [{mark}] {outcome.seconds:6.1f}s  exit {outcome.code}  "
                  f"{outcome.verdict}", flush=True)
            if not outcome.ok:
                # Print the tail immediately: a failure at minute two should not wait for the
                # report at minute twenty.
                print("      ---- output tail ----", flush=True)
                for line in outcome.tail.splitlines()[-25:]:
                    print(f"      | {line}", flush=True)
                print("      --------------------", flush=True)

        total = time.monotonic() - started
        print("\n" + "=" * 100)
        print("FULL VERIFICATION")
        print("=" * 100)
        group = None
        for outcome in outcomes:
            if outcome.entry.group != group:
                group = outcome.entry.group
                print(f"\n-- {group} " + "-" * (96 - len(group)))
            mark = "PASS" if outcome.ok else "FAIL"
            print(f"  [{mark}] {outcome.entry.name:34} exit {outcome.code:>3}  "
                  f"{outcome.seconds:7.1f}s  {outcome.verdict}")

        passed = sum(1 for o in outcomes if o.ok)
        failed = [o for o in outcomes if not o.ok]
        print("\n" + "=" * 100)
        print(f"{passed}/{len(outcomes)} entry points PASS in {total / 60:.1f} minutes")
        if failed:
            print("\nFAILED:")
            for outcome in failed:
                print(f"  - {outcome.entry.name}: exit {outcome.code}  {outcome.verdict}")
        print("=" * 100)
        return 1 if failed else 0
    finally:
        if args.keep:
            print(f"\nper-entry data kept under {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
