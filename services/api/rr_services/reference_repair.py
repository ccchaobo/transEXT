"""修复旧解析中在参考文献标题之前误标的条目；保留块 ID、文字、译文和标注。"""
from sqlalchemy import select

from document_core.blocks import is_reference_like, is_reference_section_heading
from rr_storage import Block, Database, Document


def repair_reference_types(database: Database) -> int:
    repaired = 0
    with database.session() as session:
        # API 与 worker 可能同时启动，SQLite 写事务确保修复不会互相覆盖。
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        revisions = session.scalars(select(Block.revision_id).join(
            Document, Document.active_revision_id == Block.revision_id
        ).where(Block.type == "reference").distinct()).all()
        for revision_id in revisions:
            rows = session.scalars(select(Block).where(Block.revision_id == revision_id)
                .order_by(Block.order_index)).all()
            in_references = False
            for row in rows:
                if row.type in {"header", "footer", "page_number"}:
                    continue
                text = row.clean_text or row.raw_text or ""
                if is_reference_section_heading(text):
                    in_references = True
                    continue
                if row.type == "heading" and not is_reference_like(text):
                    in_references = False
                if row.type == "reference" and not in_references:
                    row.type = "paragraph"
                    row.extra = {**(row.extra or {}), "reference_section_guard": "restored_body_translation"}
                    repaired += 1
    return repaired
