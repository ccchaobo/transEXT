"""存量分类修复保留锚点，并让正文重新进入翻译计划。"""
from sqlalchemy import select

from rr_services.reference_repair import repair_reference_types
from rr_services.translation import TranslationService
from rr_storage import Block
from test_api import client, import_and_parse


def test_existing_false_references_are_repaired_without_reparsing(client):
    document_id = import_and_parse(client)
    database = client.app.state.database
    with database.session() as session:
        rows = list(session.scalars(select(Block).where(Block.document_id == document_id).order_by(Block.order_index)))
        body = next(row for row in rows if row.type == "paragraph")
        genuine = next(row for row in rows if row.type == "reference")
        block_id, text, revision = body.id, body.clean_text, body.revision_id
        genuine_id = genuine.id
        body.type = "reference"
    assert repair_reference_types(database) == 1
    assert repair_reference_types(database) == 0
    with database.session() as session:
        restored = session.get(Block, block_id)
        assert restored.type == "paragraph"
        assert (restored.id, restored.clean_text, restored.revision_id) == (block_id, text, revision)
        assert session.get(Block, genuine_id).type == "reference"
        plan = TranslationService(client.app.state.settings, provider=None).plan(session, document_id)
        assert block_id in {unit.block_id for unit in plan.units}
        assert genuine_id not in {unit.block_id for unit in plan.units}
    payload = client.get(f"/api/v1/documents/{document_id}/pages/0").json()
    assert next(block for block in payload["blocks"] if block["id"] == block_id)["translatable"] is True
