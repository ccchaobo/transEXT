"""Library operations against a real isolated database and real PDF originals."""
import hashlib
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from rr_api import create_app
from rr_services import Settings
from rr_storage import Annotation, Document, Job, Translation
from rr_worker import Worker


@pytest.fixture
def library(tmp_path):
    app=create_app(Settings(data_dir=tmp_path/'data'),serve_frontend=False)
    with TestClient(app) as client:
        client.app=app
        yield client
    app.state.database.dispose()


def add(client,title='Scientific progression',parse=False):
    pdf=pymupdf.open();page=pdf.new_page();page.insert_text((60,80),title)
    page.insert_text((60,120),'This paper describes individual progression and its evaluation.')
    data=pdf.tobytes();pdf.close()
    response=client.post('/api/v1/documents',files={'file':('paper.pdf',data,'application/pdf')})
    assert response.status_code==201,response.text
    identifier=response.json()['document']['id']
    if parse:
        app=client.app
        worker=Worker(app.state.settings,app.state.workspace,app.state.queue,database=app.state.database)
        assert worker.serve(poll_seconds=0,max_jobs=1)==1
    return identifier,data


def batch(client,ids,action,**extra):
    return client.post('/api/v1/library/batch',json={'ids':ids,'action':action,**extra})


def test_metadata_collections_and_search_persist_without_changing_original(library):
    identifier,original=add(library,parse=True)
    before=library.get(f'/api/v1/documents/{identifier}').json()
    a=library.post('/api/v1/library/collections',json={'name':'神经影像'}).json()['id']
    b=library.post('/api/v1/library/collections',json={'name':'生成模型'}).json()['id']
    payload={'bibliography':{'title':'脑影像进展研究','authors':['张三','Alice Doe'],'year':2025,
              'doi':'10.1234/test','publication':'Medical Imaging','notes':'<script>仅为笔记文本</script>',
              'read_status':'reading','starred':True},'tags':['MRI','MRI',' 扩散模型 '],'collection_ids':[a,b]}
    response=library.patch(f'/api/v1/library/items/{identifier}',json=payload)
    assert response.status_code==200,response.text
    row=response.json();assert set(row['labels'])=={'MRI','扩散模型'}
    assert set(row['collection_ids'])=={a,b}
    for query in ['张三','Alice','10.1234','扩散模型','脑影像']:
        assert library.get('/api/v1/library/items',params={'q':query}).json()['total']==1,query
    assert library.get('/api/v1/library/items',params={'view':'starred','collection_id':b,'tag':'MRI'}).json()['total']==1
    assert library.get('/api/v1/library/items',params={'view':'reading'}).json()['total']==1
    after=library.get(f'/api/v1/documents/{identifier}').json()
    assert before['active_revision_id']==after['active_revision_id']
    assert library.get(f'/api/v1/documents/{identifier}/original').content==original
    assert library.get(f'/api/v1/library/items/{identifier}').json()['bibliography']['notes']==payload['bibliography']['notes']


def test_removing_membership_or_collection_keeps_the_document(library):
    identifier,_=add(library)
    a=library.post('/api/v1/library/collections',json={'name':'研究 A'}).json()['id']
    b=library.post('/api/v1/library/collections',json={'name':'研究 B'}).json()['id']
    assert batch(library,[identifier],'add_collection',collection_id=a).status_code==200
    assert batch(library,[identifier],'add_collection',collection_id=b).status_code==200
    assert batch(library,[identifier],'remove_collection',collection_id=a).status_code==200
    assert library.get('/api/v1/library/items',params={'collection_id':a}).json()['total']==0
    assert library.get('/api/v1/library/items',params={'collection_id':b}).json()['total']==1
    assert library.delete(f'/api/v1/library/collections/{b}').json()['documents_kept']
    assert library.get('/api/v1/library/items',params={'view':'unfiled'}).json()['total']==1
    assert library.get(f'/api/v1/documents/{identifier}/original').status_code==200


def test_trash_restore_keeps_translation_annotations_membership_and_original(library):
    identifier,original=add(library,parse=True)
    app=library.app
    revision=library.get(f'/api/v1/documents/{identifier}').json()['active_revision_id']
    col=library.post('/api/v1/library/collections',json={'name':'课题'}).json()['id']
    batch(library,[identifier],'add_collection',collection_id=col)
    with app.state.database.session() as session:
        from rr_storage import Block
        block=session.scalar(select(Block).where(Block.document_id==identifier))
        session.add(Translation(id='tr_preserved',document_id=identifier,block_id=block.id,revision_id=revision,
            source_hash=block.text_hash,provider='test',cache_key='test',text='已核对的译文',status='ok'))
        session.add(Annotation(id='ann_preserved',document_id=identifier,block_id=block.id,note='阅读笔记'))
        source=app.state.workspace.resolve(session.get(Document,identifier).source_path)
    assert batch(library,[identifier],'trash').status_code==200
    assert library.get('/api/v1/documents').json()['total']==0
    assert library.get('/api/v1/library/items').json()['total']==0
    assert library.get('/api/v1/library/items',params={'view':'trash'}).json()['total']==1
    assert library.get(f'/api/v1/documents/{identifier}').status_code==404
    assert source.read_bytes()==original
    assert batch(library,[identifier],'restore').status_code==200
    row=library.get(f'/api/v1/library/items/{identifier}').json()
    assert row['active_revision_id']==revision and row['collection_ids']==[col]
    with app.state.database.session() as session:
        assert session.get(Translation,'tr_preserved').text=='已核对的译文'
        assert session.get(Annotation,'ann_preserved').note=='阅读笔记'


def test_reimport_restores_same_record_and_requeues_unparsed_file(library):
    identifier,data=add(library)
    batch(library,[identifier],'trash')
    with library.app.state.database.session() as session:
        assert all(j.status=='cancelled' for j in session.scalars(select(Job).where(Job.document_id==identifier)))
    response=library.post('/api/v1/documents',files={'file':('renamed.pdf',data,'application/pdf')})
    assert response.status_code==201,response.text
    assert response.json()['duplicate'] and response.json()['document']['id']==identifier
    assert response.json()['job_id']
    assert library.get('/api/v1/library/items').json()['total']==1


def test_batch_validation_is_atomic_and_permanent_delete_requires_trash(library):
    identifier,_=add(library)
    assert batch(library,[identifier,'missing'],'trash').status_code==404
    assert library.get('/api/v1/library/items').json()['total']==1
    assert batch(library,[identifier],'permanent_delete',confirm=True).status_code==409
    batch(library,[identifier],'trash')
    assert batch(library,[identifier],'permanent_delete').status_code==400
    assert batch(library,[identifier],'permanent_delete',confirm=True).status_code==200
    assert library.get('/api/v1/library/items',params={'view':'trash'}).json()['total']==0
    assert not (library.app.state.workspace.root/'originals'/identifier).exists()


def test_search_wildcards_are_literal_and_bad_urls_are_rejected(library):
    identifier,_=add(library)
    assert library.get('/api/v1/library/items',params={'q':'%'}).json()['total']==0
    assert library.get('/api/v1/library/items',params={'q':'_'}).json()['total']==0
    assert library.patch(f'/api/v1/library/items/{identifier}',json={'bibliography':{'url':'javascript:alert(1)'}}).status_code==400
    assert library.patch(f'/api/v1/library/items/{identifier}',json={'bibliography':{},'collection_ids':['bad']}).status_code==404
    assert library.get(f'/api/v1/library/items/{identifier}').json()['collection_ids']==[]


def test_pagination_and_reading_filters_apply_to_the_whole_library(library):
    app=library.app
    with app.state.database.session() as session:
        for index in range(105):
            session.add(Document(id=f'd{index:03d}',filename=f'paper-{index:03d}.pdf',format='pdf',size_bytes=1,
                sha256=f'hash-{index}',source_path=f'originals/{index}.pdf',title=f'Research {index}',
                extra={'bibliography':{'title':f'Research {index}','year':2000+index%20}}))
    first=library.get('/api/v1/library/items',params={'limit':100,'sort':'title'}).json()
    second=library.get('/api/v1/library/items',params={'limit':100,'sort':'title','offset':100}).json()
    assert first['total']==105 and len(first['items'])==100 and len(second['items'])==5
    assert not ({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
    assert batch(library,['d001','d002'],'mark_read').json()['affected']==2
    assert library.get('/api/v1/library/items',params={'view':'read'}).json()['total']==2
    assert library.get('/api/v1/library/overview').json()['counts']['to_read']==103
    assert batch(library,['d001'],'add_tags',tags=['脑影像']).status_code==200
    assert library.get('/api/v1/library/items',params={'q':'脑影像'}).json()['total']==1
