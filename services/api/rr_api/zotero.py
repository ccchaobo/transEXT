"""Zotero catalogue and a same-machine, explicitly paired write bridge."""
import base64
from contextlib import contextmanager
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
from tempfile import TemporaryFile
from urllib.parse import unquote,urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from html.parser import HTMLParser
from pydantic import BaseModel, Field
from sqlalchemy import delete,func,select

from rr_storage import Document, DocumentLabel, get_setting, utcnow
from rr_storage.models import ZoteroBinding,ZoteroRecord
from rr_services import DocumentService
from .library import metadata,item_wire

router=APIRouter(prefix='/api/v1/zotero',tags=['zotero'])
CHUNK_BYTES=256*1024
SHARED=['title','authors','year','publication','doi','url','item_type','abstract']


class Selection(BaseModel):
    library_id: int=Field(default=1,ge=0)
    keys: list[str]=Field(default_factory=list,max_length=100)


class ImportAttachment(BaseModel):
    library_id: int=Field(default=1,ge=0)
    key: str=Field(pattern=r'^[A-Z0-9]{8}$')


class CaptureRequest(BaseModel):
    source: str=Field(min_length=1,max_length=2048)


class CaptureSave(BaseModel):
    library_id: int=Field(ge=1)
    items: list[dict]=Field(min_length=1,max_length=50)
    confirm: bool=False


class PushRequest(BaseModel):
    document_id: str
    library_id: int=Field(default=1,ge=1)
    confirm: bool=False
    expected_fingerprint: str | None=None
    include_pdf: bool=False


class AssociateRequest(BaseModel):
    document_id: str
    library_id: int=Field(ge=1)
    key: str=Field(pattern=r'^[A-Z0-9]{8}$')
    expected_fingerprint: str
    confirm: bool=False


class ResolveRequest(BaseModel):
    document_id: str
    expected_fingerprint: str
    confirm: bool=False


class MergeRequest(BaseModel):
    library_id: int=Field(ge=1)
    master_key: str=Field(pattern=r'^[A-Z0-9]{8}$')
    other_keys: list[str]=Field(min_length=1,max_length=20)
    fingerprints: dict[str,str]
    confirm: bool=False


class NativeCommand(BaseModel):
    library_id: int=Field(default=1,ge=1)
    key: str=Field(default='',max_length=8)
    confirm: bool=False
    task: str | None=None


def sha(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def doi(value):return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)','',value or '',flags=re.I).strip().casefold()
def creators(data):return [' '.join(filter(None,[c.get('firstName'),c.get('lastName')])).strip() or c.get('name','') for c in data.get('creators',[]) if c.get('creatorType')=='author']
def display_text(value):
    # Zotero titles may contain its supported rich text. Keep raw source JSON in
    # the catalogue, but do not expose <i>/<sub> markup as literal UI text.
    if not re.search(r'</?(?:b|i|em|strong|sub|sup|span|p|div|br)(?:\s|/?>)',value or '',flags=re.I):return value or ''
    class Plain(HTMLParser):
        def __init__(self):super().__init__();self.parts=[]
        def handle_data(self,data):self.parts.append(data)
    parser=Plain();parser.feed(value);return ''.join(parser.parts)
def shared_values(data):
    year=re.search(r'\b([12]\d{3})\b',data.get('date',''))
    values={'title':display_text(data.get('title',''))[:512],'authors':[name[:255] for name in creators(data)][:100],
      'year':int(year.group(1)) if year else None,'publication':(data.get('publicationTitle') or data.get('publisher') or data.get('university') or data.get('institution') or '')[:512],
      'doi':data.get('DOI','')[:200],'url':data.get('url','')[:2048],
      'item_type':{'book':'book','thesis':'thesis','report':'report'}.get(data.get('itemType'),'article'),
      'abstract':data.get('abstractNote','')[:20000]}
    if values['url'] and not values['url'].lower().startswith(('http://','https://')):values['url']=''
    return values


def token(request):
    with request.app.state.database.session() as session:return get_setting(session,'zotero.bridge_token')


def call(request,path,data=None,bridge=True,params=None,raw=False):
    override=getattr(request.app.state,'zotero_transport',None)
    headers={'Zotero-API-Version':'3','Zotero-Allowed-Request':'1',
             'Host':'127.0.0.1:23119','User-Agent':'ResearchReader/0.2'}
    base=f'http://{request.app.state.settings.zotero_host}:23119'
    if bridge:
        key=token(request)
        if key:headers['X-Research-Reader-Key']=key
    try:
        with httpx.Client(trust_env=False,timeout=90,transport=override,follow_redirects=False) as client:
            response=client.request('POST' if bridge else 'GET',base+('/research-reader' if bridge else '/api')+path,
                                    json=data or {} if bridge else None,params=params,headers=headers)
    except httpx.HTTPError:
        raise HTTPException(503,detail={'message':'无法连接 Zotero，请先启动 Zotero。','error_code':'ZOTERO_UNAVAILABLE'})
    if not response.is_success:
        try:message=response.json().get('error') or response.text[:300]
        except ValueError:message=response.text[:300]
        if response.status_code==404 and bridge:message='Zotero 连接组件尚未加载，请确认组件已安装并重启 Zotero。'
        raise HTTPException(409 if response.status_code in {409,412} else 503,detail={'message':message,'error_code':'ZOTERO_CONFLICT' if response.status_code in {409,412} else 'ZOTERO_BRIDGE_UNAVAILABLE'})
    return response.text if raw else response.json()


def status_data(request):
    try:
        result=call(request,'/status')
        authorized=bool(token(request) and result.get('authorized'))
        result.update(connected=True,bridge_installed=True,can_write=authorized)
        if not authorized:result['message']='Zotero 组件已加载，但阅读器与 Zotero 配对不匹配，请重新配对当前数据卷。'
        return result
    except HTTPException:
        try:
            call(request,'/',bridge=False,raw=True)
            return {'connected':True,'bridge_installed':False,'can_write':False,'instance':'readonly-local',
                    'libraries':[{'id':0,'name':'Zotero 个人文献库（只读）','editable':False}],'user_library_id':0,
                    'message':'Zotero 已启动；连接组件未加载或与当前版本不兼容。可只读浏览，完整联动需升级并启用连接组件。'}
        except HTTPException:return {'connected':False,'bridge_installed':False,'can_write':False,'libraries':[],'message':'请先启动 Zotero。'}


def inventory(request,library_id):
    state=status_data(request)
    if state.get('bridge_installed'):
        return call(request,'/inventory',{'library_id':library_id or state['user_library_id']})
    items=[];start=0
    while True:
        rows=call(request,'/users/0/items',bridge=False,params={'limit':100,'start':start,'includeTrashed':1})
        if not rows:break
        for row in rows:
            data=row['data'];items.append({'key':row['key'],'data':data,'fingerprint':sha(data),'deleted':bool(data.get('deleted')),
               'parent_key':data.get('parentItem'),'is_attachment':data.get('itemType')=='attachment'})
        start+=len(rows)
        if len(rows)<100:break
    return {'instance':'readonly-local','library_id':0,'items':items,'collections':call(request,'/users/0/collections',bridge=False)}


@contextmanager
def pdf_source(request,state,library_id,key):
    """Copy authenticated PDF chunks across OS boundaries; never mount Zotero storage."""
    value=call(request,'/file',{'library_id':library_id,'key':key})
    if state.get('pdf_transfer')!='chunks-v1':
        path=Path(value['path'])
        if str(path).startswith('\\\\') or not path.is_file():
            raise HTTPException(409,detail={'message':'请升级 Zotero 连接组件到 0.2.0，以支持 Docker 附件传输。'})
        with path.open('rb') as stream:yield path.name,stream
        return
    size=value.get('size');checksum=value.get('md5','')
    if not isinstance(size,int) or not 5<=size<=min(request.app.state.settings.max_file_bytes,200*1024*1024):
        raise HTTPException(413,detail={'message':'Zotero PDF 超过文件大小上限或为空。'})
    if not re.fullmatch('[a-f0-9]{32}',checksum):
        raise HTTPException(409,detail={'message':'Zotero PDF 缺少完整性校验信息。'})
    filename=str(value.get('filename') or 'attachment.pdf').replace('\\','/').rsplit('/',1)[-1]
    with TemporaryFile() as stream:
        offset=0;digest=hashlib.md5(usedforsecurity=False)
        while offset<size:
            chunk=call(request,'/file-chunk',{'library_id':library_id,'key':key,'offset':offset,'size':size,'mtime':value['mtime']})
            try:
                encoded=chunk['base64']
                if len(encoded)>((CHUNK_BYTES+2)//3)*4:raise ValueError()
                raw=base64.b64decode(encoded,validate=True)
                if chunk['offset']!=offset or not 0<len(raw)<=min(CHUNK_BYTES,size-offset):raise ValueError()
            except (ValueError,TypeError,KeyError):
                raise HTTPException(409,detail={'message':'Zotero PDF 分块校验失败，未导入。'})
            stream.write(raw);digest.update(raw);offset+=len(raw)
        if digest.hexdigest()!=checksum:
            raise HTTPException(409,detail={'message':'Zotero PDF 在传输中发生变化，未导入，请重试。'})
        stream.seek(0)
        yield filename,stream


def attach_pdf(request,state,library_id,key,path):
    if state.get('pdf_transfer')!='chunks-v1':
        return call(request,'/attach',{'library_id':library_id,'key':key,'path':str(path)})
    size=path.stat().st_size
    if size>200*1024*1024:raise HTTPException(413,detail={'message':'PDF 超过 Zotero 传输的 200 MB 上限。'})
    with path.open('rb') as stream:
        digest=hashlib.file_digest(stream,lambda:hashlib.md5(usedforsecurity=False)).hexdigest();stream.seek(0)
        upload=call(request,'/attachment-upload',{'action':'begin','library_id':library_id,'key':key,'size':size,'md5':digest})['upload']
        try:
            offset=0
            while raw:=stream.read(CHUNK_BYTES):
                result=call(request,'/attachment-upload',{'action':'append','upload':upload,'offset':offset,'base64':base64.b64encode(raw).decode('ascii')})
                offset+=len(raw)
                if result.get('offset')!=offset:raise HTTPException(409,detail={'message':'Zotero PDF 传输位置不匹配，未导入。'})
            result=call(request,'/attachment-upload',{'action':'finish','upload':upload});upload=None
            return result
        finally:
            if upload:
                try:call(request,'/attachment-upload',{'action':'cancel','upload':upload})
                except HTTPException:pass


def apply_shared(session,doc,values,tags=None):
    current=metadata(doc);current.update(values)
    doc.extra={**(doc.extra or {}),'bibliography':current}
    if current['title']:doc.title=current['title']
    if tags is not None:
        session.execute(delete(DocumentLabel).where(DocumentLabel.document_id==doc.id))
        session.add_all([DocumentLabel(document_id=doc.id,label=name[:64]) for name in dict.fromkeys(tags) if name.strip()])


def pull(request,library_id):
    source=inventory(request,library_id);instance=source['instance'];library_id=source['library_id']
    conflicts=[];updated=0
    with request.app.state.database.session() as session:
        bindings={b.item_key:[] for b in session.scalars(select(ZoteroBinding).where(ZoteroBinding.instance==instance,ZoteroBinding.library_id==library_id))}
        for b in session.scalars(select(ZoteroBinding).where(ZoteroBinding.instance==instance,ZoteroBinding.library_id==library_id)):bindings[b.item_key].append(b)
        seen=set()
        for row in source['items']:
            data=row['data'];key=row['key'];seen.add(key)
            record=session.get(ZoteroRecord,(instance,library_id,key))
            if record is None:
                record=ZoteroRecord(instance=instance,library_id=library_id,item_key=key,fingerprint=row['fingerprint']);session.add(record)
            record.data=data;record.fingerprint=row['fingerprint'];record.deleted=row.get('deleted',False)
            record.title=data.get('title','')[:1024];record.item_type=data.get('itemType','');record.parent_key=data.get('parentItem') or row.get('parent_key')
            record.search_text=' '.join([record.title,*creators(data),data.get('DOI',''),data.get('publicationTitle','')]).casefold();record.synced_at=utcnow()
            for binding in bindings.get(key,[]):
                doc=session.get(Document,binding.document_id)
                if not doc or binding.fingerprint==row['fingerprint']:continue
                local={name:metadata(doc)[name] for name in SHARED}
                if local!=binding.baseline or record.deleted:
                    conflicts.append({'document_id':doc.id,'key':key,'reason':'来源已删除' if record.deleted else '本地与 Zotero 均有变化'});continue
                apply_shared(session,doc,shared_values(data))
                binding.baseline=shared_values(data);binding.fingerprint=row['fingerprint'];binding.synced_at=utcnow();updated+=1
        for record in session.scalars(select(ZoteroRecord).where(ZoteroRecord.instance==instance,ZoteroRecord.library_id==library_id)):
            if record.item_key not in seen:record.deleted=True
        session.flush()
    return {'instance':instance,'library_id':library_id,'cached_items':len(seen),'updated_documents':updated,'conflicts':conflicts,
            'message':'已更新本机 Zotero 目录缓存；这不表示云端同步完成。'}


@router.get('/status')
def status(request: Request):return status_data(request)


@router.post('/pull')
def sync_pull(request: Request,body: Selection):return pull(request,body.library_id)


@router.get('/items')
def list_items(request: Request,library_id: int=0,q: str='',offset: int=Query(0,ge=0),limit: int=Query(100,ge=1,le=200)):
    state=status_data(request);instance=state.get('instance','readonly-local');library_id=library_id or state.get('user_library_id',0)
    with request.app.state.database.session() as session:
        stmt=select(ZoteroRecord).where(ZoteroRecord.instance==instance,ZoteroRecord.library_id==library_id,ZoteroRecord.deleted==False,
             ZoteroRecord.item_type.not_in(['attachment','note','annotation']))
        if q:stmt=stmt.where(ZoteroRecord.search_text.contains(q.casefold(),autoescape=True))
        total=session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        records=session.scalars(stmt.order_by(ZoteroRecord.title,ZoteroRecord.item_key).offset(offset).limit(limit)).all()
        rows=[]
        for r in records:
            attachments=session.scalars(select(ZoteroRecord).where(ZoteroRecord.instance==instance,ZoteroRecord.library_id==library_id,
               ZoteroRecord.parent_key==r.item_key,ZoteroRecord.item_type=='attachment',ZoteroRecord.deleted==False)).all()
            docs=list(session.scalars(select(ZoteroBinding.document_id).join(Document).where(ZoteroBinding.instance==instance,ZoteroBinding.library_id==library_id,ZoteroBinding.item_key==r.item_key,Document.deleted_at.is_(None))))
            rows.append({'key':r.item_key,'library_id':library_id,'bibliography':shared_values(r.data),'fingerprint':r.fingerprint,
               'attachments':[{'key':a.item_key,'title':a.title,'pdf':a.data.get('contentType')=='application/pdf'} for a in attachments],
               'document_ids':docs,'item_type':r.item_type,'select_url':f'zotero://select/library/items/{r.item_key}'})
        return {'items':rows,'total':total,'offset':offset,'limit':limit,'instance':instance}


@router.post('/import-pdf')
def import_pdf(request: Request,body: ImportAttachment):
    state=status_data(request)
    if state.get('bridge_installed'):
        instance=state['instance'];lib_id=body.library_id
    else:
        url=call(request,f'/users/0/items/{body.key}/file/view/url',bridge=False,raw=True).strip();parsed=urlparse(url)
        if parsed.scheme!='file' or parsed.netloc not in {'','localhost'}:raise HTTPException(409,detail={'message':'附件尚未下载到本机，请先在 Zotero 打开 PDF。'})
        value=unquote(parsed.path)
        if re.match(r'^/[A-Za-z]:',value):value=value[1:]
        path=Path(value);instance='readonly-local';lib_id=0
        if str(path).startswith('\\\\') or not path.is_file():raise HTTPException(409,detail={'message':'此 PDF 没有可读取的本机副本；Docker 导入需启用新版连接组件。'})
    with request.app.state.database.session() as session:
        attachment=session.get(ZoteroRecord,(instance,lib_id,body.key))
        if not attachment or attachment.data.get('contentType')!='application/pdf':raise HTTPException(409,detail={'message':'请先同步并选择 PDF 附件'})
        parent=session.get(ZoteroRecord,(instance,lib_id,attachment.parent_key)) if attachment.parent_key else attachment
        if not parent:raise HTTPException(409,detail={'message':'附件的书目条目不存在，请重新同步'})
        service=DocumentService(request.app.state.settings,request.app.state.workspace,request.app.state.queue)
        if state.get('bridge_installed'):
            with pdf_source(request,state,lib_id,body.key) as (filename,stream):
                result=service.import_file(session,filename=filename,stream=stream)
        else:
            with path.open('rb') as stream:result=service.import_file(session,filename=path.name,stream=stream)
        doc=result.document;binding=session.get(ZoteroBinding,doc.id)
        if binding and (binding.instance,binding.library_id,binding.item_key)!=(instance,lib_id,parent.item_key):
            raise HTTPException(409,detail={'message':'同一 PDF 已关联另一条 Zotero 文献，请先核对重复条目'})
        values=shared_values(parent.data)
        if not (doc.extra or {}).get('bibliography'):apply_shared(session,doc,values)
        if not binding:
            binding=ZoteroBinding(document_id=doc.id,instance=instance,library_id=lib_id,item_key=parent.item_key,
                attachment_key=body.key,fingerprint=parent.fingerprint,baseline=values);session.add(binding)
        session.flush()
        return {'document':item_wire(session,doc),'duplicate':result.duplicate,'copied':True,'zotero_original_unchanged':True}


@router.get('/export')
def export(request: Request,keys: str,format: str='bibtex',style: str='apa',library_id: int=0):
    wanted=keys.split(',')
    if not wanted or len(wanted)>100 or any(not re.fullmatch('[A-Z0-9]{8}',key) for key in wanted):raise HTTPException(400,detail={'message':'文献标识无效'})
    if format not in {'bibtex','ris','bib'}:raise HTTPException(400,detail={'message':'导出格式无效'})
    state=status_data(request)
    selected=next((l for l in state.get('libraries',[]) if l['id']==library_id),None)
    if library_id and library_id!=state.get('user_library_id'):
        if not selected or not selected.get('group_id'):raise HTTPException(400,detail={'message':'无法识别所选文献库的导出地址'})
        path=f"/groups/{selected['group_id']}/items"
    else:path='/users/0/items'
    text=call(request,path,bridge=False,params={'itemKey':keys,'format':format,'style':style},raw=True)
    if format=='bib':
        class TextBibliography(HTMLParser):
            def __init__(self):super().__init__();self.parts=[]
            def handle_data(self,data):self.parts.append(data)
            def handle_endtag(self,tag):
                if tag in {'div','p'}:self.parts.append('\n')
        parser=TextBibliography();parser.feed(text);text=''.join(parser.parts).strip()+'\n'
    return PlainTextResponse(text,headers={'Content-Disposition':f'attachment; filename="references.{"txt" if format=="bib" else format}"'})


@router.post('/capture-preview')
def capture_preview(request: Request,body: CaptureRequest):
    source=body.source.strip();identifier=doi(source)
    if identifier.startswith('10.'):
        return call(request,'/capture',{'identifier':{'DOI':identifier}})
    parsed=urlparse(source)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:raise HTTPException(400,detail={'message':'请输入网页地址或 DOI'})
    try:
        addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):raise ValueError()
    except (OSError,ValueError):raise HTTPException(400,detail={'message':'网页抓取只接受可访问的公开网页地址'})
    return call(request,'/capture',{'url':source})


@router.post('/capture-save')
def capture_save(request: Request,body: CaptureSave):
    if not body.confirm:raise HTTPException(400,detail={'message':'请先核对书目信息和目标 Zotero 文献库'})
    result=call(request,'/save',{'library_id':body.library_id,'items':body.items});pull(request,body.library_id)
    return result


@router.post('/push')
def push(request: Request,body: PushRequest):
    if not body.confirm:raise HTTPException(400,detail={'message':'请先确认写入 Zotero 的条目及目标文献库'})
    with request.app.state.database.session() as session:
        doc=session.get(Document,body.document_id)
        if not doc or doc.deleted_at:raise LookupError('文献不存在')
        if body.include_pdf and doc.format!='pdf':raise HTTPException(400,detail={'message':'当前仅支持复制 PDF 原件，请取消附件复制或先导入 PDF'})
        values=metadata(doc);binding=session.get(ZoteroBinding,doc.id)
        state=status_data(request)
        if not state.get('can_write'):raise HTTPException(409,detail={'message':'Zotero 写入连接尚未启用'})
        if binding and binding.instance!=state.get('instance'):raise HTTPException(409,detail={'message':'Zotero 来源实例不同，未写回；请重新核对关联'})
        if binding and binding.library_id!=body.library_id:raise HTTPException(409,detail={'message':'目标文献库与原关联不同，未写回'})
        target=next((l for l in state['libraries'] if l['id']==body.library_id),None)
        if not target or not target.get('editable'):raise HTTPException(409,detail={'message':'所选文献库不可编辑'})
        if binding and values['item_type']!=binding.baseline.get('item_type'):raise HTTPException(409,detail={'message':'关联条目的类型请在 Zotero 中修改后同步，避免丢失类型专属字段'})
        publication_field={'book':'publisher','thesis':'university','report':'institution'}.get(values['item_type'],'publicationTitle')
        fields={'title':values['title'],'date':str(values['year'] or ''),'DOI':values['doi'],'url':values['url'],
                publication_field:values['publication'],'abstractNote':values['abstract']}
        author_rows=[{'creatorType':'author','name':name} for name in values['authors']]
        if binding:
            record=session.get(ZoteroRecord,(binding.instance,binding.library_id,binding.item_key))
            field_map={'title':'title','year':'date','doi':'DOI','url':'url','publication':publication_field,'abstract':'abstractNote'}
            for name,key in field_map.items():
                if values[name]==binding.baseline.get(name):fields.pop(key,None)
            if values['authors']!=binding.baseline.get('authors'):fields['creators']=author_rows
            result=call(request,'/update',{'library_id':binding.library_id,'key':binding.item_key,
               'expected_fingerprint':body.expected_fingerprint or binding.fingerprint,'fields':fields})
        else:
            fields.update(itemType={'book':'book','thesis':'thesis','report':'report'}.get(values['item_type'],'journalArticle'),creators=author_rows)
            saved=call(request,'/save',{'library_id':body.library_id,'items':[fields],'operation_id':f"reader:{state['instance']}:{doc.id}"});result=saved['items'][0]
            if result.get('reused'):raise HTTPException(409,detail={'message':'Zotero 已有同 DOI 条目，请先关联已有文献，避免覆盖其信息'})
            binding=ZoteroBinding(document_id=doc.id,instance=state['instance'],library_id=body.library_id,item_key=result['key'],
                fingerprint=result['fingerprint'],baseline={name:values[name] for name in SHARED});session.add(binding)
        accepted=shared_values(result.get('data') or {})
        apply_shared(session,doc,accepted)
        binding.fingerprint=result['fingerprint'];binding.baseline=accepted;binding.synced_at=utcnow()
        document_key=binding.item_key;target_library=binding.library_id
        pdf_path=request.app.state.workspace.resolve(doc.source_path) if body.include_pdf else None
    attachment=None
    if pdf_path:
        attachment=attach_pdf(request,state,target_library,document_key,pdf_path)
    pull(request,target_library)
    return {'key':document_key,'saved':True,'pdf_uploaded':bool(attachment)}


@router.post('/associate')
def associate(request: Request,body: AssociateRequest):
    if not body.confirm:raise HTTPException(400,detail={'message':'请确认本地文献与 Zotero 条目是同一文献'})
    source=inventory(request,body.library_id)
    entry=next((r for r in source['items'] if r['key']==body.key and not r.get('deleted')),None)
    if not entry or entry['data'].get('itemType') in {'attachment','note','annotation'} or entry['fingerprint']!=body.expected_fingerprint:
        raise HTTPException(409,detail={'message':'所选书目已变化，请重新同步并核对'})
    with request.app.state.database.session() as session:
        doc=session.get(Document,body.document_id)
        if not doc or doc.deleted_at:raise LookupError('文献不存在')
        if session.get(ZoteroBinding,doc.id):raise HTTPException(409,detail={'message':'此文献已有 Zotero 关联'})
        values=shared_values(entry['data']);apply_shared(session,doc,values)
        session.add(ZoteroBinding(document_id=doc.id,instance=source['instance'],library_id=source['library_id'],item_key=body.key,
            fingerprint=entry['fingerprint'],baseline=values))
    pull(request,body.library_id)
    return {'associated':True,'zotero_modified':False}


@router.post('/resolve-from-zotero')
def resolve_from_zotero(request: Request,body: ResolveRequest):
    if not body.confirm:raise HTTPException(400,detail={'message':'请确认采用 Zotero 书目信息'})
    state=status_data(request)
    with request.app.state.database.session() as session:
        binding=session.get(ZoteroBinding,body.document_id);doc=session.get(Document,body.document_id)
        if not binding or not doc or doc.deleted_at or binding.instance!=state.get('instance'):raise HTTPException(409,detail={'message':'关联不可用'})
        source=inventory(request,binding.library_id)
        row=next((r for r in source['items'] if r['key']==binding.item_key and not r.get('deleted')),None)
        if not row or row['fingerprint']!=body.expected_fingerprint:raise HTTPException(409,detail={'message':'Zotero 条目再次变化，请重新同步'})
        values=shared_values(row['data']);apply_shared(session,doc,values)
        binding.baseline=values;binding.fingerprint=row['fingerprint'];binding.synced_at=utcnow()
    return {'resolved':True,'zotero_modified':False}


@router.get('/binding/{document_id}')
def get_binding(request: Request,document_id: str):
    state=status_data(request)
    with request.app.state.database.session() as session:
        doc=session.get(Document,document_id);binding=session.get(ZoteroBinding,document_id)
        if not doc or doc.deleted_at:raise LookupError('文献不存在')
        if not binding:return {'binding':None,'local':metadata(doc)}
        if binding.instance!=state.get('instance'):raise HTTPException(409,detail={'message':'Zotero 来源实例已变化，请核对文献库'})
        source=inventory(request,binding.library_id)
        row=next((r for r in source['items'] if r['key']==binding.item_key),None)
        return {'binding':{'key':binding.item_key,'library_id':binding.library_id},'local':metadata(doc),
            'remote':shared_values(row['data']) if row else None,'deleted':not row or row.get('deleted',False),
            'expected_fingerprint':row['fingerprint'] if row else None,
            'conflict':bool(row and row['fingerprint']!=binding.fingerprint and
                {name:metadata(doc)[name] for name in SHARED}!=binding.baseline)}


@router.get('/duplicates')
def duplicates(request: Request,library_id: int=1):
    source=inventory(request,library_id);groups={}
    for row in source['items']:
        data=row['data']
        if row.get('deleted') or data.get('itemType') in {'attachment','note','annotation'}:continue
        code=doi(data.get('DOI',''))
        title=re.sub(r'\W+','',data.get('title','').casefold())
        names=creators(data);year=shared_values(data)['year']
        identity='doi:'+code if code else f'title:{title}|{year}|{names[0].casefold()}' if len(title)>12 and year and names else None
        if identity:groups.setdefault(identity,[]).append({'key':row['key'],'title':data.get('title',''),
                'fingerprint':row['fingerprint'],'item_type':data.get('itemType')})
    return {'groups':[{'identity':key,'items':rows} for key,rows in groups.items() if len(rows)>1],
            'message':'仅列出候选项；合并需确认主条目，由 Zotero 原生合并保留附件及引用关联。'}


@router.post('/merge')
def merge(request: Request,body: MergeRequest):
    if not body.confirm:raise HTTPException(400,detail={'message':'请确认主条目及将合并的重复条目'})
    result=call(request,'/merge',body.model_dump());state=status_data(request)
    with request.app.state.database.session() as session:
        for binding in session.scalars(select(ZoteroBinding).where(ZoteroBinding.instance==state['instance'],ZoteroBinding.library_id==body.library_id,
                                  ZoteroBinding.item_key.in_(result['merged_keys']))):binding.item_key=body.master_key
    pull(request,body.library_id);return result


@router.post('/native/{command}')
def native(request: Request,command: str,body: NativeCommand):
    if command not in {'word','sync','task','locate'}:raise HTTPException(400,detail={'message':'操作无效'})
    if command in {'word','sync'} and not body.confirm:raise HTTPException(400,detail={'message':'请明确确认原生操作'})
    return call(request,'/'+command,body.model_dump())
