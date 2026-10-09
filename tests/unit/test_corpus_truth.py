"""Tests for the generated corpus ground truth.

Ground truth is the reference every acceptance metric is measured against, so a
defect in it produces confident wrong numbers.  Both metric defects found in this
project had a ground-truth defect behind them, so the structure of the truth is
tested rather than assumed.

These tests read the committed corpus.  If it has not been generated they skip
rather than fail, and say so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
CORPUS = REPO / "benchmarks" / "corpus"


@pytest.fixture(scope="module")
def manifest() -> dict:
    path = CORPUS / "manifest.json"
    if not path.exists():
        pytest.skip("the corpus has not been generated; run the corpus generator")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def truth_files() -> dict[str, dict]:
    truth_dir = CORPUS / "truth"
    if not truth_dir.exists():
        pytest.skip("the corpus has not been generated")
    loaded: dict[str, dict] = {}
    for path in sorted(truth_dir.glob("*.json")):
        loaded[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


class TestCorpusShape:
    def test_the_corpus_meets_the_minimum_sample_count(self, manifest: dict) -> None:
        # Spec 12.1 requires at least 24 normal samples.
        counts = manifest.get("counts", {})
        normal = sum(
            value for key, value in counts.items() if key != "anomaly"
        )
        assert normal >= 24, f"only {normal} normal samples: {counts}"

    def test_the_anomaly_suite_is_present(self, manifest: dict) -> None:
        assert manifest.get("counts", {}).get("anomaly", 0) >= 10

    def test_the_manifest_states_where_the_corpus_came_from(self, manifest: dict) -> None:
        # Provenance has to be recorded, because every measurement taken here is
        # bounded by the fact that the corpus is synthetic.
        assert manifest.get("generator")
        assert manifest.get("generator_version")
        assert manifest.get("licence_note")

    def test_the_development_holdout_split_is_recorded(self) -> None:
        # Spec 12.1: a holdout exists so that tuning is not measured on the data it
        # was tuned against.
        path = CORPUS / "holdout.json"
        if not path.exists():
            pytest.skip("the corpus has not been generated")
        split = json.loads(path.read_text(encoding="utf-8"))
        assert split, "the split must name the held-out samples"


class TestGroundTruthStructure:
    def test_every_page_records_visible_text(self, truth_files: dict) -> None:
        empty: list[str] = []
        for name, truth in truth_files.items():
            for page in truth.get("pages", []):
                if not page.get("visible_text", "").strip():
                    empty.append(f"{name}#{page['index']}")
        assert empty == [], f"pages with no recorded visible text: {empty}"

    def test_every_page_records_visible_lines(self, truth_files: dict) -> None:
        # Line-level truth is what makes reading order measurable.  Without it a
        # page's reference is one string per paragraph and no order score can be
        # computed, which is exactly the defect that produced a vacuous "1.0".
        missing: list[str] = []
        for name, truth in truth_files.items():
            for page in truth.get("pages", []):
                if not page.get("visible_lines"):
                    missing.append(f"{name}#{page['index']}")
        assert missing == [], f"pages with no visible_lines: {missing}"

    def test_a_text_page_has_many_lines_not_one_per_paragraph(
        self, truth_files: dict
    ) -> None:
        # The specific defect: a page of prose was recorded as a handful of lines
        # because each paragraph was flattened.  A page that renders to dozens of
        # visual lines must record roughly that many.
        offenders: list[str] = []
        for name, truth in truth_files.items():
            if not name.startswith(("scan_", "paper_", "text_")):
                continue
            for page in truth.get("pages", []):
                lines = page.get("visible_lines", [])
                text = page.get("visible_text", "")
                if len(text) > 1500 and len(lines) < 10:
                    offenders.append(
                        f"{name}#{page['index']}: {len(text)} chars in {len(lines)} line(s)"
                    )
        assert offenders == [], (
            "a page of prose was flattened into too few lines, so line order "
            f"cannot be measured: {offenders}"
        )

    def test_visible_text_is_the_visible_lines_joined(self, truth_files: dict) -> None:
        # The two must describe the same content: if they diverged, the
        # character-level metric and the order metric would be measuring different
        # pages.
        offenders: list[str] = []
        for name, truth in truth_files.items():
            for page in truth.get("pages", []):
                lines = " ".join(page.get("visible_lines", []))
                flat = page.get("visible_text", "")
                if " ".join(flat.split()) != " ".join(lines.split()):
                    offenders.append(f"{name}#{page['index']}")
        assert offenders == [], (
            f"visible_text and visible_lines disagree on these pages: {offenders}"
        )

    def test_every_page_records_enough_content_to_compare(
        self, truth_files: dict
    ) -> None:
        # A page with almost no text would let a metric pass by having nothing to
        # measure.
        short: list[str] = []
        for name, truth in truth_files.items():
            for page in truth.get("pages", []):
                if 0 < len(page.get("visible_text", "")) < 40:
                    short.append(f"{name}#{page['index']}")
        assert short == [], f"pages with suspiciously little text: {short}"

    def test_blocks_record_their_drawn_line_texts(self, truth_files: dict) -> None:
        # A block's `text` is its semantic text, which for a cross-page paragraph
        # is the whole paragraph.  The per-line record is what a per-page
        # comparison needs.
        offenders: list[str] = []
        for name, truth in truth_files.items():
            for page in truth.get("pages", []):
                for block in page.get("blocks", []):
                    if block.get("n_lines", 0) > 1 and not block.get("line_texts"):
                        offenders.append(f"{name}#{page['index']} block {block.get('index')}")
        assert offenders == [], f"multi-line blocks with no line record: {offenders}"


class TestGroundTruthSelfConsistency:
    def test_every_declared_page_has_a_geometry_record(self, truth_files: dict) -> None:
        for name, truth in truth_files.items():
            for page in truth.get("pages", []):
                assert "rotation" in page, f"{name}#{page['index']} has no rotation"
                assert page["rotation"] in (0, 90, 180, 270)

    def test_the_files_named_in_the_manifest_exist(self, manifest: dict) -> None:
        # Manifest entries carry a bare name; the file on disk carries an
        # extension.  Comparing names to filenames directly reported every entry as
        # missing, so the comparison is on the stem.
        stems: set[str] = set()
        for folder in ("files", "images", "anomalies"):
            directory = CORPUS / folder
            if directory.exists():
                stems.update(path.stem for path in directory.iterdir() if path.is_file())
        missing = [
            entry.get("name")
            for entry in manifest.get("entries", [])
            if entry.get("name") and entry["name"] not in stems
        ]
        assert missing == [], f"manifest entries with no file: {missing}"

    def test_every_file_on_disk_is_named_in_the_manifest(self, manifest: dict) -> None:
        # The other direction matters too: a file with no manifest entry has no
        # recorded provenance, so a measurement on it cannot be attributed.
        named = {entry.get("name") for entry in manifest.get("entries", [])}
        unlisted: list[str] = []
        for folder in ("files", "images", "anomalies"):
            directory = CORPUS / folder
            if not directory.exists():
                continue
            for path in directory.iterdir():
                if path.is_file() and path.stem not in named:
                    unlisted.append(f"{folder}/{path.name}")
        assert unlisted == [], f"files with no manifest entry: {unlisted}"
