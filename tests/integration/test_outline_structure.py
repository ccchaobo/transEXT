import uuid
import pymupdf
from rr_storage import Block,Document,SourceSpanRow
from rr_worker import Worker
from test_api import client,import_and_parse


def test_title_only_native_bookmark_does_not_hide_real_sections(client):
    pdf=pymupdf.open()
    for i,title in enumerate(['1. Introduction','2. Methods']):
        page=pdf.new_page();page.insert_text((50,60),'Research paper title',fontsize=18)
        page.insert_text((50,110),title,fontsize=14,fontname='hebo')
        page.insert_text((50,145),'The paper describes a reproducible method using longitudinal medical data.',fontsize=10)
    pdf.set_toc([[1,'Research paper title',1]])
    response=client.post('/api/v1/documents',files={'file':('bookmark.pdf',pdf.tobytes(),'application/pdf')});pdf.close()
    doc=response.json()['document']['id'];app=client.app
    Worker(app.state.settings,app.state.workspace,app.state.queue,database=app.state.database).serve(max_jobs=1,poll_seconds=0)
    reply=client.get(f'/api/v1/documents/{doc}/outline')
    assert reply.status_code==200
    titles=[item['title'] for item in reply.json()['outline']]
    assert '1. Introduction' in titles and '2. Methods' in titles and 'Research paper title' not in titles


def test_number_and_title_fragments_join_only_on_the_same_baseline(client):
    doc=import_and_parse(client)
    with client.app.state.database.session() as session:
        revision=session.get(Document,doc).active_revision_id
        def add(text,order,box,bold=True):
            key='blk_'+uuid.uuid4().hex[:20]
            row=Block(id=key,block_key=str(order),document_id=doc,revision_id=revision,page_index=0,order_index=order,
                type='heading',clean_text=text,raw_text=text,text_hash=key,extra={'font_size':12,'bold':bold})
            session.add(row);session.flush();session.add(SourceSpanRow(block_id=key,revision_id=revision,page_index=0,ordinal=0,
                bbox=box,text_start=0,text_end=len(text)));return key
        add('3',500,[.1,.5,.115,.52]);title=add('CodecLM',501,[.13,.5,.4,.52])
        add('A.1',502,[.1,.6,.135,.62]);add('Benchmark Details',503,[.15,.6,.5,.62])
        add('E',504,[.1,.7,.12,.72]);add('Method',505,[.1,.8,.3,.82])
    entries=client.get(f'/api/v1/documents/{doc}/outline').json()['outline'];labels=[e['title'] for e in entries]
    assert '3. CodecLM' in labels and 'A.1. Benchmark Details' in labels
    assert 'E' not in labels and 'Method' not in labels
    joined=next(e for e in entries if e['title']=='3. CodecLM');assert joined['block_id']==title and len(joined['block_ids'])==2
