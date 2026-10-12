"""Reparse a document, keeping historical revisions and exact-source translations.

Run with the background worker paused. This never sends text to a translator;
changed blocks are explicitly reported for a subsequent translation job.
"""
import argparse
import json
import uuid
from pathlib import Path
from sqlalchemy import select
from rr_adapters.translation import load_env_file
from rr_services import open_runtime, ParseService
from rr_services.parsing import PARSER_VERSION
from rr_services.translation import cache_key
from rr_storage import Block, Document, Translation, Annotation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document_id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if Path('.env').exists():
        load_env_file(Path('.env'))
    runtime = open_runtime()
    with runtime.database.session() as session:
        document = session.get(Document, args.document_id)
        if document is None:
            raise ValueError("document does not exist")
        old_revision = document.active_revision_id
        old_blocks = list(session.scalars(select(Block).where(Block.revision_id == old_revision)))
        old_text = {block.id: block.clean_text for block in old_blocks}
        old_translations = list(session.scalars(select(Translation).where(Translation.revision_id == old_revision)))
        job, _ = runtime.queue.enqueue(session, job_type='parse', document_id=document.id,
            request={}, idempotency_key=f'layout-repair:{document.id}:{uuid.uuid4().hex}', priority=100)
        lease = runtime.queue.claim_next(session, holder='layout-repair')
        if lease is None or lease.job_id != job.id:
            raise RuntimeError("another job is pending; run repair while the queue is idle")
        outcome = ParseService(runtime.settings,runtime.workspace,runtime.queue).run(session,lease,document.id)
        runtime.queue.finish(session,lease,status=outcome.status)
        new_blocks = list(session.scalars(select(Block).where(Block.revision_id == outcome.revision_id)))
        matches = {}
        for block in old_blocks:
            matches.setdefault(block.clean_text,[]).append(block)
        reused = 0
        for block in new_blocks:
            candidates = matches.get(block.clean_text,[])
            if len(candidates)>1:
                candidates = [old for old in candidates if old.page_index==block.page_index
                              and old.section_path==block.section_path]
            # Repeated identical labels can carry different manual translations;
            # they need review, never an arbitrary provenance assignment.
            if len(candidates)!=1:
                continue
            old = candidates[0]
            rows=[row for row in old_translations if row.block_id==old.id and (row.is_current or row.status=='unknown')]
            for row in rows:
                fields={column.name:getattr(row,column.name) for column in Translation.__table__.columns
                        if column.name not in {'id','block_id','revision_id','cache_key','created_at'}}
                session.add(Translation(id=f'tr_{uuid.uuid4().hex[:24]}',block_id=block.id,
                    revision_id=outcome.revision_id,cache_key=cache_key(text_hash=block.text_hash,
                    revision_id=outcome.revision_id,source_language=row.source_language,
                    target_language=row.target_language,provider=row.provider,model=row.model,
                    prompt_version=row.prompt_version,glossary_version=row.glossary_version,params=row.params),**fields))
                reused+=1
        # Historical annotation anchors remain historical; flag relocation explicitly.
        for note in session.scalars(select(Annotation).where(Annotation.document_id==document.id)):
            if note.block_id in old_text:
                note.needs_relocation=True
                note.relocation_note='版面解析已修正，请重新核对段落位置。'
        session.flush()
        document.translate_status='partially_succeeded'
        report={'document_id':document.id,'previous_revision':old_revision,
                'revision':outcome.revision_id,'parser_version':PARSER_VERSION,
                'blocks':outcome.blocks,'reused_translation_rows':reused,'status':outcome.status}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
