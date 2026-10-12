"""Local library management. Collections reference immutable document originals."""
from typing import Literal
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, cast, delete, exists, func, or_, select

from rr_storage import Document, DocumentLabel, Job, utcnow
from rr_storage.models import CollectionDocument, LibraryCollection, ZoteroBinding
from rr_services import DocumentService
from rr_contracts import DocumentSummary
from .reads import document_summary

router=APIRouter(prefix='/api/v1/library',tags=['library'])


class Bibliography(BaseModel):
    title: str = Field(default='',max_length=512)
    authors: list[str] = Field(default_factory=list,max_length=100)
    year: int | None = Field(default=None,ge=1,le=9999)
    publication: str = Field(default='',max_length=512)
    doi: str = Field(default='',max_length=200)
    url: str = Field(default='',max_length=2048)
    item_type: Literal['article','book','thesis','report','other']='article'
    abstract: str = Field(default='',max_length=20000)
    notes: str = Field(default='',max_length=50000)
    read_status: Literal['to_read','reading','read']='to_read'
    starred: bool=False

    @field_validator('authors')
    @classmethod
    def clean_authors(cls,values):
        if any(len(value)>255 for value in values):raise ValueError('作者姓名过长')
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator('url')
    @classmethod
    def safe_url(cls,value):
        value=value.strip()
        if value and not value.lower().startswith(('https://','http://')):raise ValueError('来源链接须使用 http 或 https')
        return value


class ItemPatch(BaseModel):
    bibliography: Bibliography
    tags: list[str] = Field(default_factory=list,max_length=100)
    collection_ids: list[str] = Field(default_factory=list,max_length=100)

    @field_validator('tags')
    @classmethod
    def clean_tags(cls,values):
        if any(len(value.strip())>64 for value in values):raise ValueError('标签不能超过 64 个字符')
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class LibraryItemWire(DocumentSummary):
    bibliography: Bibliography
    deleted_at: datetime | None=None
    collection_ids: list[str]=Field(default_factory=list)
    original_url: str
    zotero_binding: dict | None=None


class LibraryListWire(BaseModel):
    items: list[LibraryItemWire]
    total: int
    offset: int
    limit: int


class CollectionName(BaseModel):
    name: str = Field(min_length=1,max_length=100)

    @field_validator('name')
    @classmethod
    def clean(cls,value):
        if not value.strip():raise ValueError('分类名称不能为空')
        return value.strip()


class BatchAction(BaseModel):
    ids: list[str] = Field(min_length=1,max_length=200)
    action: Literal['trash','restore','star','unstar','mark_read','mark_unread',
                    'add_collection','remove_collection','add_tags','remove_tags','permanent_delete']
    collection_id: str | None=None
    tags: list[str] = Field(default_factory=list,max_length=100)
    confirm: bool=False

    @field_validator('tags')
    @classmethod
    def clean_tags(cls,values):return ItemPatch.clean_tags(values)


def get_item(session,item_id):
    doc=session.get(Document,item_id)
    if doc is None:raise LookupError('文献不存在')
    return doc


def metadata(doc):
    data=dict((doc.extra or {}).get('bibliography') or {})
    data.setdefault('title',doc.title or doc.filename)
    return Bibliography.model_validate(data).model_dump()


def item_wire(session,doc):
    row=document_summary(session,doc)
    row.update(bibliography=metadata(doc),deleted_at=doc.deleted_at,
        collection_ids=list(session.scalars(select(CollectionDocument.collection_id).where(CollectionDocument.document_id==doc.id))),
        original_url=f'/api/v1/documents/{doc.id}/original')
    binding=session.get(ZoteroBinding,doc.id)
    row['zotero_binding']=({'library_id':binding.library_id,'key':binding.item_key,'fingerprint':binding.fingerprint,
        'instance':binding.instance} if binding else None)
    return row


def collection(session,identifier):
    row=session.get(LibraryCollection,identifier)
    if row is None:raise LookupError('分类不存在')
    return row


@router.get('/overview')
def overview(request: Request):
    with request.app.state.database.session() as session:
        active=Document.deleted_at.is_(None)
        def count(condition):return session.scalar(select(func.count()).select_from(Document).where(condition)) or 0
        counts={'all':count(active),'trash':count(Document.deleted_at.is_not(None)),
                'unfiled':count(active & ~exists(select(CollectionDocument.document_id).where(CollectionDocument.document_id==Document.id))),
                'starred':count(active & (Document.extra['bibliography']['starred'].as_boolean()==True))}
        for status in ['to_read','reading','read']:
            counts[status]=count(active & (func.coalesce(Document.extra['bibliography']['read_status'].as_string(),'to_read')==status))
        collections=[]
        for row in session.scalars(select(LibraryCollection).order_by(LibraryCollection.name)):
            number=session.scalar(select(func.count()).select_from(CollectionDocument).join(Document).where(CollectionDocument.collection_id==row.id,active)) or 0
            collections.append({'id':row.id,'name':row.name,'count':number})
        tags=[{'name':name,'count':number} for name,number in session.execute(
            select(DocumentLabel.label,func.count()).join(Document).where(active).group_by(DocumentLabel.label).order_by(DocumentLabel.label))]
        return {'counts':counts,'collections':collections,'tags':tags}


@router.get('/items',response_model=LibraryListWire)
def items(request: Request,q: str=Query(default='',max_length=500),
          view: Literal['all','unfiled','starred','to_read','reading','read','trash']='all',
          collection_id: str | None=None,tag: str | None=None,
          sort: Literal['added_desc','title','year_desc']='added_desc',
          offset: int=Query(default=0,ge=0),limit: int=Query(default=100,ge=1,le=200)):
    with request.app.state.database.session() as session:
        stmt=select(Document).where(Document.deleted_at.is_not(None) if view=='trash' else Document.deleted_at.is_(None))
        if q.strip():
            pattern='%'+q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            fields=[Document.filename,Document.title,Document.extra['bibliography']['title'].as_string(),
                    Document.extra['bibliography']['publication'].as_string(),
                    Document.extra['bibliography']['doi'].as_string()]
            labels=exists(select(DocumentLabel.id).where(DocumentLabel.document_id==Document.id,DocumentLabel.label.ilike(pattern,escape='\\')))
            # json_each decodes Unicode author names. Searching the raw JSON
            # array would miss Chinese names stored as escaped JSON strings.
            author_values=func.json_each(Document.extra,'$.bibliography.authors').table_valued('value').alias('author_values')
            authors=exists(select(1).select_from(author_values).where(author_values.c.value.ilike(pattern,escape='\\')))
            stmt=stmt.where(or_(*[field.ilike(pattern,escape='\\') for field in fields],labels,authors))
        if collection_id:
            collection(session,collection_id)
            stmt=stmt.where(exists(select(CollectionDocument.document_id).where(CollectionDocument.document_id==Document.id,CollectionDocument.collection_id==collection_id)))
        if tag:stmt=stmt.where(exists(select(DocumentLabel.id).where(DocumentLabel.document_id==Document.id,DocumentLabel.label==tag)))
        if view=='unfiled':stmt=stmt.where(~exists(select(CollectionDocument.document_id).where(CollectionDocument.document_id==Document.id)))
        if view=='starred':stmt=stmt.where(Document.extra['bibliography']['starred'].as_boolean()==True)
        if view in {'to_read','reading','read'}:stmt=stmt.where(func.coalesce(Document.extra['bibliography']['read_status'].as_string(),'to_read')==view)
        total=session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        order={'added_desc':Document.imported_at.desc(),'title':func.coalesce(Document.extra['bibliography']['title'].as_string(),Document.title,Document.filename),
               'year_desc':Document.extra['bibliography']['year'].as_integer().desc()}[sort]
        rows=session.scalars(stmt.order_by(order,Document.id).offset(offset).limit(limit)).all()
        return {'items':[item_wire(session,doc) for doc in rows],'total':total,'offset':offset,'limit':limit}


@router.get('/items/{item_id}',response_model=LibraryItemWire)
def detail(request: Request,item_id: str):
    with request.app.state.database.session() as session:return item_wire(session,get_item(session,item_id))


@router.patch('/items/{item_id}',response_model=LibraryItemWire)
def edit(request: Request,item_id: str,body: ItemPatch):
    with request.app.state.database.session() as session:
        doc=get_item(session,item_id)
        if doc.deleted_at:raise HTTPException(409,detail={'message':'请先从回收站恢复文献'})
        for identifier in set(body.collection_ids):collection(session,identifier)
        doc.extra={**(doc.extra or {}),'bibliography':body.bibliography.model_dump()}
        if body.bibliography.title.strip():doc.title=body.bibliography.title.strip()
        session.execute(delete(DocumentLabel).where(DocumentLabel.document_id==doc.id))
        session.add_all([DocumentLabel(document_id=doc.id,label=tag) for tag in body.tags])
        session.execute(delete(CollectionDocument).where(CollectionDocument.document_id==doc.id))
        session.add_all([CollectionDocument(document_id=doc.id,collection_id=identifier) for identifier in set(body.collection_ids)])
        session.flush()
        return item_wire(session,doc)


@router.post('/collections',status_code=201)
def create_collection(request: Request,body: CollectionName):
    with request.app.state.database.session() as session:
        row=LibraryCollection(id='col_'+uuid4().hex[:20],name=body.name)
        session.add(row);session.flush()
        return {'id':row.id,'name':row.name,'count':0}


@router.patch('/collections/{collection_id}')
def rename_collection(request: Request,collection_id: str,body: CollectionName):
    with request.app.state.database.session() as session:
        row=collection(session,collection_id);row.name=body.name
        return {'id':row.id,'name':row.name}


@router.delete('/collections/{collection_id}')
def remove_collection(request: Request,collection_id: str):
    with request.app.state.database.session() as session:
        session.delete(collection(session,collection_id));session.flush()
        return {'deleted':collection_id,'documents_kept':True}


@router.post('/batch')
def batch(request: Request,body: BatchAction):
    with request.app.state.database.session() as session:
        docs=[get_item(session,identifier) for identifier in dict.fromkeys(body.ids)]
        action=body.action
        if action in {'add_collection','remove_collection'}:
            if not body.collection_id:raise HTTPException(400,detail={'message':'请选择目标分类'})
            collection(session,body.collection_id)
        if action not in {'trash','restore','permanent_delete'} and any(doc.deleted_at for doc in docs):
            raise HTTPException(409,detail={'message':'请先从回收站恢复文献'})
        if action=='permanent_delete':
            if not body.confirm:raise HTTPException(400,detail={'message':'永久删除需要明确确认'})
            if any(not doc.deleted_at for doc in docs):raise HTTPException(409,detail={'message':'请先移入回收站'})
            running=session.scalar(select(Job.id).where(Job.document_id.in_(body.ids),Job.status=='running').limit(1))
            if running:raise HTTPException(409,detail={'message':'后台任务正在停止，请稍后再永久删除'})
        for doc in docs:
            if action=='trash':
                doc.deleted_at=doc.deleted_at or utcnow()
                for job in session.scalars(select(Job).where(Job.document_id==doc.id,Job.status.in_(['queued','running']))):
                    request.app.state.queue.request_cancel(session,job.id)
            elif action=='restore':
                was_deleted=bool(doc.deleted_at);doc.deleted_at=None
                if was_deleted and not doc.active_revision_id:
                    request.app.state.queue.enqueue(session,job_type='parse',document_id=doc.id,request={},idempotency_key=f'restore:{doc.id}:{uuid4().hex}')
            elif action=='permanent_delete':
                DocumentService(request.app.state.settings,request.app.state.workspace,request.app.state.queue).delete_document(session,doc.id)
            elif action in {'star','unstar','mark_read','mark_unread'}:
                values=metadata(doc)
                if action in {'star','unstar'}:values['starred']=action=='star'
                else:values['read_status']='read' if action=='mark_read' else 'to_read'
                doc.extra={**(doc.extra or {}),'bibliography':values}
            elif action in {'add_collection','remove_collection'}:
                link=session.get(CollectionDocument,(body.collection_id,doc.id))
                if action=='add_collection' and not link:session.add(CollectionDocument(collection_id=body.collection_id,document_id=doc.id))
                elif action=='remove_collection' and link:session.delete(link)
            elif action in {'add_tags','remove_tags'}:
                labels=set(session.scalars(select(DocumentLabel.label).where(DocumentLabel.document_id==doc.id)))
                updated=labels|set(body.tags) if action=='add_tags' else labels-set(body.tags)
                session.execute(delete(DocumentLabel).where(DocumentLabel.document_id==doc.id))
                session.add_all([DocumentLabel(document_id=doc.id,label=value) for value in sorted(updated)])
        session.flush()
        return {'action':action,'affected':len(docs),'ids':[doc.id for doc in docs]}
