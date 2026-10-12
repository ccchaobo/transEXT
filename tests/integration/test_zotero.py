"""Native boundary tests with an isolated reader DB; never change the user's Zotero."""
import copy
import json
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient
from rr_api import create_app
from rr_api.zotero import sha,shared_values
from rr_services import Settings
from rr_storage import set_setting


@pytest.fixture
def linked(tmp_path):
    app=create_app(Settings(data_dir=tmp_path/'data'),serve_frontend=False)
    native={'instance':'test-local','items':{},'calls':[],'bridge':True,'authorized':True,'new':0}
    def transport(request):
        path=request.url.path;body=json.loads(request.content) if request.content else {}
        native['calls'].append((path,body))
        if path=='/research-reader/status':
            if not native['bridge']:return httpx.Response(404)
            return httpx.Response(200,json={'instance':native['instance'],'authorized':native['authorized'],'paired':True,
                'user_library_id':1,'libraries':[{'id':1,'name':'Tests','editable':True},{'id':4,'name':'Group','editable':True,'group_id':42}]})
        if path=='/api/':return httpx.Response(200,text='ok')
        if path.startswith('/research-reader/'):
            assert request.headers.get('X-Research-Reader-Key')=='test-token'
        if path=='/research-reader/inventory':return httpx.Response(200,json={'instance':native['instance'],'library_id':body['library_id'],
            'items':[{'key':key,'data':value,'fingerprint':sha(value),'deleted':value.get('deleted',False)} for key,value in native['items'].items()],'collections':[]})
        if path=='/research-reader/file':return httpx.Response(200,json={'path':str(native['pdf'])})
        if path=='/research-reader/update':
            data=native['items'][body['key']]
            if body['expected_fingerprint']!=sha(data):return httpx.Response(409,json={'error':'changed'})
            data.update(body['fields']);return httpx.Response(200,json={'key':body['key'],'data':data,'fingerprint':sha(data)})
        if path=='/research-reader/save':
            native['new']+=1;key=f'NEW{native["new"]:05d}';data=body['items'][0];native['items'][key]=data
            return httpx.Response(200,json={'items':[{'key':key,'data':data,'fingerprint':sha(data)}]})
        if path=='/research-reader/attach':return httpx.Response(200,json={'key':'PDF00001'})
        if path.startswith('/api/') and path.endswith('/items'):return httpx.Response(200,text='<div class="csl-entry">A &amp; B. <i>Study</i>.</div>')
        return httpx.Response(400,json={'error':'not mocked'})
    app.state.zotero_transport=httpx.MockTransport(transport)
    with app.state.database.session() as session:set_setting(session,'zotero.bridge_token','test-token',is_secret=True)
    data={'itemType':'journalArticle','title':'MRI progression','date':'2025-07-13','DOI':'10.1234/test',
        'creators':[{'creatorType':'author','firstName':'Alice','lastName':'Smith'}],'publicationTitle':'Journal','abstractNote':'Abstract'}
    native['items']['BOOK0001']=data
    pdf=pymupdf.open();pdf.new_page().insert_text((60,80),'Isolated MRI research test');native['pdf']=tmp_path/'source.pdf';pdf.save(native['pdf']);pdf.close()
    native['items']['PDF00001']={'itemType':'attachment','parentItem':'BOOK0001','contentType':'application/pdf','title':'PDF'}
    with TestClient(app) as client:yield client,native
    app.state.database.dispose()


def imported(client):
    assert client.post('/api/v1/zotero/pull',json={'library_id':1}).status_code==200
    response=client.post('/api/v1/zotero/import-pdf',json={'library_id':1,'key':'PDF00001'})
    assert response.status_code==200,response.text
    return response.json()['document']['id']


def test_authenticated_status_and_readonly_fallback(linked):
    client,native=linked
    assert client.get('/api/v1/zotero/status').json()['can_write']
    native['authorized']=False
    assert not client.get('/api/v1/zotero/status').json()['can_write']
    native['bridge']=False
    result=client.get('/api/v1/zotero/status').json()
    assert result['connected'] and not result['can_write'] and not result['bridge_installed']
    assert 'test-token' not in json.dumps(result)


def test_pdf_copy_cache_and_changed_fields_preserve_native_details(linked):
    client,native=linked;source=native['pdf'].read_bytes();identifier=imported(client)
    assert native['pdf'].read_bytes()==source
    assert client.get(f'/api/v1/documents/{identifier}/original').content==source
    row=client.get(f'/api/v1/library/items/{identifier}').json()
    row['bibliography']['title']='MRI revised';row['bibliography']['notes']='Local note'
    assert client.patch(f'/api/v1/library/items/{identifier}',json={'bibliography':row['bibliography']}).status_code==200
    result=client.post('/api/v1/zotero/push',json={'document_id':identifier,'confirm':True,'library_id':1,'include_pdf':True})
    assert result.status_code==200,result.text
    update=next(body for path,body in native['calls'] if path=='/research-reader/update')
    assert update['fields']=={'title':'MRI revised'}
    assert native['items']['BOOK0001']['date']=='2025-07-13'
    assert native['items']['BOOK0001']['creators'][0]['firstName']=='Alice'
    assert result.json()['pdf_uploaded']
    assert client.get(f'/api/v1/library/items/{identifier}').json()['bibliography']['notes']=='Local note'


def test_conflict_is_not_overwritten_and_explicit_resolution_preserves_local_notes(linked):
    client,native=linked;identifier=imported(client)
    row=client.get(f'/api/v1/library/items/{identifier}').json();row['bibliography'].update(title='Local',notes='Keep note',starred=True)
    client.patch(f'/api/v1/library/items/{identifier}',json={'bibliography':row['bibliography']})
    native['items']['BOOK0001']['title']='Remote'
    result=client.post('/api/v1/zotero/pull',json={'library_id':1}).json()
    assert len(result['conflicts'])==1
    assert client.get(f'/api/v1/library/items/{identifier}').json()['bibliography']['title']=='Local'
    assert client.post('/api/v1/zotero/push',json={'document_id':identifier,'confirm':True}).status_code==409
    snapshot=client.get(f'/api/v1/zotero/binding/{identifier}').json();assert snapshot['conflict']
    body={'document_id':identifier,'expected_fingerprint':snapshot['expected_fingerprint'],'confirm':True}
    native['items']['BOOK0001']['title']='Remote again'
    assert client.post('/api/v1/zotero/resolve-from-zotero',json=body).status_code==409
    body['expected_fingerprint']=sha(native['items']['BOOK0001'])
    assert client.post('/api/v1/zotero/resolve-from-zotero',json=body).status_code==200
    meta=client.get(f'/api/v1/library/items/{identifier}').json()['bibliography']
    assert meta['title']=='Remote again' and meta['notes']=='Keep note' and meta['starred']


def test_confirmation_instance_and_library_guards(linked):
    client,native=linked;identifier=imported(client)
    assert client.post('/api/v1/zotero/push',json={'document_id':identifier}).status_code==400
    assert client.post('/api/v1/zotero/push',json={'document_id':identifier,'library_id':4,'confirm':True}).status_code==409
    native['instance']='another-machine'
    assert client.post('/api/v1/zotero/push',json={'document_id':identifier,'confirm':True}).status_code==409
    for command in ['word','sync']:
        assert client.post('/api/v1/zotero/native/'+command,json={}).status_code==400
    assert not any(path in ['/research-reader/update','/research-reader/save'] for path,body in native['calls'])


def test_associate_does_not_write_zotero_and_can_push_book_publication(linked):
    client,native=linked
    native['items']['BOOK0001'].update(itemType='book',publisher='Publisher');native['items']['BOOK0001'].pop('publicationTitle')
    upload=client.post('/api/v1/documents',files={'file':('local.pdf',native['pdf'].read_bytes(),'application/pdf')})
    identifier=upload.json()['document']['id']
    body={'document_id':identifier,'library_id':1,'key':'BOOK0001','expected_fingerprint':sha(native['items']['BOOK0001']),'confirm':True}
    assert client.post('/api/v1/zotero/associate',json=body).status_code==200
    row=client.get(f'/api/v1/library/items/{identifier}').json();row['bibliography']['publication']='New Publisher'
    client.patch(f'/api/v1/library/items/{identifier}',json={'bibliography':row['bibliography']})
    assert client.post('/api/v1/zotero/push',json={'document_id':identifier,'confirm':True}).status_code==200
    assert native['items']['BOOK0001']['publisher']=='New Publisher'
    assert 'publicationTitle' not in native['items']['BOOK0001']
    assert not any(path=='/research-reader/save' for path,body in native['calls'])


def test_exports_plain_bibliography_and_target_library(linked):
    client,native=linked
    result=client.get('/api/v1/zotero/export',params={'keys':'BOOK0001','format':'bib','library_id':4})
    assert result.status_code==200 and result.text=='A & B. Study.\n'
    assert any(path=='/api/groups/42/items' for path,body in native['calls'])
    assert client.get('/api/v1/zotero/export',params={'keys':'bad'}).status_code==400
    assert shared_values({'title':'<b>mice</b> in <i>R</i>; PM<sub>2.5</sub>'})['title']=='mice in R; PM2.5'


def test_deleted_remote_and_duplicate_candidates_never_delete_local_or_merge(linked):
    client,native=linked;identifier=imported(client)
    native['items']['BOOK0002']=copy.deepcopy(native['items']['BOOK0001'])
    result=client.get('/api/v1/zotero/duplicates').json()
    assert len(result['groups'])==1
    assert not any(path=='/research-reader/merge' for path,body in native['calls'])
    native['items']['BOOK0001']['deleted']=True
    client.post('/api/v1/zotero/pull',json={'library_id':1})
    assert client.get(f'/api/v1/library/items/{identifier}').json()['deleted_at'] is None
    assert client.get(f'/api/v1/zotero/binding/{identifier}').json()['deleted']
