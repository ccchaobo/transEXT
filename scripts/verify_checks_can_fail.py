"""Break each new frontend-contract check, one at a time, and confirm it fails.

A check that has never failed is a check nobody should believe.  The four invariants added for
this round each correspond to a defect that actually happened - three of them mine - so each is
reverted in turn and the script is expected to report that specific failure.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"D:\transEXT\research-reader")
APP = REPO / "apps/web/js/app.js"
CSS = REPO / "apps/web/styles.css"
SCRIPT = REPO / "scripts/verify_frontend_contract.py"

# (name, file, find, replace, the substring the failure must contain)
CASES = [
    (
        "a control put back inside a pane",
        APP,
        '  const sourceHead = el("div", "pane-head");\n',
        '  const sourceHead = el("div", "pane-head");\n'
        '  const stray = el("span", null, "x");\n'
        '  stray.id = "view-mode";\n',
        "built inside a pane",
    ),
    (
        "the pane body unable to shrink",
        CSS,
        ".pane-body { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; padding: 14px; }",
        ".pane-body { flex: 1; overflow-y: auto; overflow-x: hidden; padding: 14px; }",
        "cannot be scrolled to",
    ),
    (
        "the overlay panel turned back into a box",
        CSS,
        "  border: 0;\n",
        "  border: 1px solid rgba(37, 99, 235, .55);\n",
        "still a box",
    ),
    (
        "the per-block re-translation call removed",
        APP,
        'scope: "blocks", block_ids: [blockId], target_language: state.targetLanguage, force: true,',
        'scope: "document", target_language: state.targetLanguage,',
        "re-translates one block",
    ),
    (
        "the toolbar put back inside .panes",
        APP,
        "  main.appendChild(buildToolbar(doc, reader.dataset.mode));\n"
        "  main.appendChild(buildPanes(doc, reader.dataset.mode));\n",
        "  main.appendChild(buildPanes(doc, reader.dataset.mode));\n",
        "not built outside .panes",
    ),
]

results = []
# Every file this touches is recorded first and compared at the end.  A verifier that mutates the
# tree it verifies is one crash away from leaving a defect behind that looks like source, so the
# restoration is checked rather than trusted to the `finally`.
untouched = {path: path.read_text(encoding="utf-8") for path in {case[1] for case in CASES}}

for name, path, find, replace, expecting in CASES:
    original = path.read_text(encoding="utf-8")
    if original.count(find) != 1:
        print(f"SKIP {name}: anchor appears {original.count(find)} times")
        results.append((name, "SKIP"))
        continue
    path.write_text(original.replace(find, replace), encoding="utf-8")
    try:
        done = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        output = (done.stdout or "") + (done.stderr or "")
        failed = done.returncode != 0 and expecting in output
        print(f"{'FAILS AS INTENDED' if failed else 'DID NOT FAIL  '}  {name}")
        if not failed:
            for line in output.splitlines():
                if "FAIL" in line:
                    print(f"    (reported: {line.strip()})")
        results.append((name, "ok" if failed else "MISSED"))
    finally:
        path.write_text(original, encoding="utf-8")

print()
for path, before in untouched.items():
    if path.read_text(encoding="utf-8") != before:
        print(f"THE TREE WAS LEFT MODIFIED: {path}")
        raise SystemExit(2)
print(f"every file was restored byte for byte ({len(untouched)} files)")

bad = [n for n, r in results if r != "ok"]
if bad:
    print(f"NEGATIVE CONTROL FAILED for: {bad}")
    raise SystemExit(1)
print(f"all {len(results)} checks fail when their defect is reintroduced")
