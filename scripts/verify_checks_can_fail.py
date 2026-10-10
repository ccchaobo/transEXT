"""Break each new check, one at a time, and confirm it fails.

A check that has never failed is a check nobody should believe.  Every invariant added in this
round corresponds to a defect that actually happened - most of them mine - so each is reverted in
turn and the verification is expected to report that specific failure.

Two kinds of check are covered:

* ``script`` - an assertion inside ``scripts/verify_frontend_contract.py``, which is a static
  reading of the frontend source.  Reverting the frontend source must make the script exit
  non-zero.
* ``pytest`` - a test that pins behaviour the API and the worker both have to get right.  Reverting
  the production code must make that test fail.

Every file this touches is recorded before the first case and compared after the last, so a check
that mutates the tree it verifies cannot leave a defect behind that looks like source.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"D:\transEXT\research-reader")
APP = REPO / "apps/web/js/app.js"
CSS = REPO / "apps/web/styles.css"
CONTRACT = REPO / "scripts/verify_frontend_contract.py"
WORKER = REPO / "services/api/rr_worker/__init__.py"

FRONTEND_TEST = "tests/integration/test_translation_settings.py::TestTheJobDoesWhatThePlanSaid"
ONE_BLOCK = f"{FRONTEND_TEST}::test_a_one_block_job_sends_exactly_one_request"
FORCED = f"{FRONTEND_TEST}::test_force_sends_a_block_that_is_already_translated"

# (name, file, find, replace, runner, the substring the failure must contain)
CASES = [
    (
        "a control put back inside a pane",
        APP,
        '  const sourceHead = el("div", "pane-head");\n',
        '  const sourceHead = el("div", "pane-head");\n'
        '  const stray = el("span", null, "x");\n'
        '  stray.id = "view-mode";\n',
        ("script",),
        "built inside a pane",
    ),
    (
        "the pane body unable to shrink",
        CSS,
        ".pane-body { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 14px; }",
        ".pane-body { flex: 1; overflow-y: auto; overflow-x: hidden; padding: 14px; }",
        ("script",),
        "cannot be scrolled to",
    ),
    (
        "the overlay panel turned back into a box",
        CSS,
        "  border: 0;\n",
        "  border: 1px solid rgba(37, 99, 235, .55);\n",
        ("script",),
        "still a box",
    ),
    (
        "the per-block re-translation call removed",
        APP,
        'scope: "blocks", block_ids: [blockId], target_language: state.targetLanguage, force: true,',
        'scope: "document", target_language: state.targetLanguage,',
        ("script",),
        "re-translates one block",
    ),
    (
        "the toolbar put back inside .panes",
        APP,
        "  main.appendChild(buildToolbar(doc, reader.dataset.mode));\n"
        "  main.appendChild(buildPanes(doc, reader.dataset.mode));\n",
        "  main.appendChild(buildPanes(doc, reader.dataset.mode));\n",
        ("script",),
        "not built outside .panes",
    ),
    (
        "the worker ignoring the block list again",
        WORKER,
        "        only_block_ids=only_block_ids,\n        force=force,\n",
        "",
        ("pytest", ONE_BLOCK),
        # Measured, not guessed.  I first wrote "0 requests" here, reasoning that a job which
        # re-planned the whole document would find everything cached and send nothing.  It sends
        # **53** - the entire document - when nothing is translated yet, so asking for one block
        # costs the whole paper.  My prediction was wrong and the truth is worse, which is a good
        # argument for making the negative control state what it expects.
        "sent 53 requests for one block",
    ),
    (
        "the worker ignoring `force` again",
        WORKER,
        "        only_block_ids=only_block_ids,\n        force=force,\n",
        "        only_block_ids=only_block_ids,\n",
        ("pytest", FORCED),
        "forcing a cached block sent 0 requests",
    ),
]


def run(runner: tuple[str, ...]) -> tuple[int, str]:
    if runner[0] == "script":
        command = [sys.executable, str(CONTRACT)]
    else:
        command = [sys.executable, "-m", "pytest", runner[1], "-q", "-p", "no:warnings"]
    done = subprocess.run(
        command, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return done.returncode, (done.stdout or "") + (done.stderr or "")


results: list[tuple[str, str]] = []
untouched = {path: path.read_text(encoding="utf-8") for path in {case[1] for case in CASES}}

for name, path, find, replace, runner, expecting in CASES:
    original = path.read_text(encoding="utf-8")
    if original.count(find) != 1:
        print(f"SKIP {name}: anchor appears {original.count(find)} times")
        results.append((name, "SKIP"))
        continue
    path.write_text(original.replace(find, replace), encoding="utf-8")
    try:
        code, output = run(runner)
        failed = code != 0 and expecting in output
        print(f"{'FAILS AS INTENDED' if failed else 'DID NOT FAIL  '}  {name}")
        if not failed:
            for line in output.splitlines():
                if "FAIL" in line or line.startswith("E "):
                    print(f"    (reported: {line.strip()[:150]})")
        results.append((name, "ok" if failed else "MISSED"))
    finally:
        path.write_text(original, encoding="utf-8")

print()
for path, before in untouched.items():
    if path.read_text(encoding="utf-8") != before:
        print(f"THE TREE WAS LEFT MODIFIED: {path}")
        raise SystemExit(2)
print(f"every file was restored byte for byte ({len(untouched)} files)")

bad = [name for name, outcome in results if outcome != "ok"]
if bad:
    print(f"NEGATIVE CONTROL FAILED for: {bad}")
    raise SystemExit(1)
print(f"all {len(results)} checks fail when their defect is reintroduced")
