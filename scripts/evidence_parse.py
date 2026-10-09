"""Run a real parse end to end against a corpus PDF and print what was stored.

This is the acceptance evidence for the parse service: a real file, the real storage
schema, and the real queue lease.  It prints the pages, the block types and one source
span, so the result can be checked rather than assumed.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from rr_services import DocumentService, ParseService, Settings
from rr_storage import Database, JobQueue, Workspace, Block, Page, ParseRevision, SourceSpanRow

CORPUS = Path("benchmarks/corpus")
NAME = "pdf_b_two_column"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="rr_parse_"))
    try:
        settings = Settings(data_dir=root)
        database = Database(settings.database_url)
        database.create_all()
        workspace = Workspace(settings.workspace_root)
        queue = JobQueue(settings.lease_seconds)
        documents = DocumentService(settings, workspace, queue)
        parsing = ParseService(settings, workspace, queue)

        source = CORPUS / "files" / f"{NAME}.pdf"
        with source.open("rb") as stream:
            with database.session() as session:
                result = documents.import_file(session, filename=source.name, stream=stream)
                document_id = result.document.id
                print(f"imported {result.document.filename} ({result.document.size_bytes} bytes)")
                print(f"job queued: {result.parse_queued}")

        with database.session() as session:
            lease = queue.claim_next(session, holder="evidence")
            assert lease is not None, "no job to claim"
            print(f"claimed job {lease.job_id} attempt {lease.attempt_number}")
            queue.set_stage(session, lease, "extract")
            outcome = parsing.run(session, lease, document_id)
            queue.finish(session, lease, status=outcome.status)
            print(f"parse status: {outcome.status}")

        with database.session() as session:
            document = documents.get_document(session, document_id)
            print()
            print(f"document.parse_status   = {document.parse_status}")
            print(f"document.page_count     = {document.page_count}")
            print(f"document.active_revision= {document.active_revision_id}")

            revision = session.get(ParseRevision, outcome.revision_id)
            print(f"revision.status         = {revision.status}")
            print(f"revision.stats          = {revision.stats}")
            print(f"revision.warnings       = {revision.warnings}")

            pages = session.query(Page).filter_by(revision_id=revision.id).order_by(Page.page_index).all()
            print()
            print(f"PAGES ({len(pages)})")
            for page in pages:
                print(f"  {page.page_index}  {page.width_pt:.0f}x{page.height_pt:.0f}pt "
                      f"rot={page.rotation} kind={page.source_kind:8} chars={page.text_chars:5} "
                      f"warnings={page.warnings}")

            blocks = (
                session.query(Block)
                .filter_by(revision_id=revision.id)
                .order_by(Block.order_index)
                .all()
            )
            print()
            print(f"BLOCKS ({len(blocks)})")
            for block in blocks:
                marker = "T" if block.type not in ("reference", "page_number", "header", "footer") else " "
                print(f"  [{marker}] {block.order_index:3} p{block.page_index} "
                      f"{block.type:12} conf={block.confidence_score:.2f} "
                      f"{block.clean_text[:58]!r}")

            spans = session.query(SourceSpanRow).filter_by(revision_id=revision.id).all()
            print()
            print(f"SPANS: {len(spans)}")
            first = (
                session.query(SourceSpanRow)
                .filter_by(revision_id=revision.id)
                .order_by(SourceSpanRow.page_index, SourceSpanRow.ordinal)
                .first()
            )
            print(f"  first span: page={first.page_index} bbox={[round(v, 4) for v in first.bbox]}")
            print(f"              raw_bbox={[round(v, 2) for v in first.raw_bbox]} space={first.raw_space}")
            print(f"              text[{first.text_start}:{first.text_end}]")

            print()
            print("TYPE COUNTS")
            from collections import Counter

            for block_type, count in Counter(b.type for b in blocks).most_common():
                print(f"  {block_type:12} {count}")

        database.dispose()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
