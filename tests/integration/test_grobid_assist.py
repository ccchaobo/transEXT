import io
from reportlab.pdfgen import canvas
from fastapi.testclient import TestClient
from rr_api import create_app
from rr_services import Settings
from rr_storage import Document,ParseRevision
from rr_worker import Worker
from rr_adapters.grobid import parse_tei,GrobidClient


def sample():
    stream=io.BytesIO();pdf=canvas.Canvas(stream,pagesize=(600,800))
    pdf.setFont("Helvetica-Bold",16);pdf.drawString(60,740,"Controlled Document")
    pdf.setFont("Helvetica",12);pdf.drawString(60,660,"Methods")
    pdf.setFont("Helvetica",10);pdf.drawString(60,610,"This experiment measures an arithmetic mean of two inputs.")
    pdf.save();return stream.getvalue()


def run(tmp_path,url):
    app=create_app(Settings(data_dir=tmp_path,grobid_url=url),serve_frontend=False)
    with TestClient(app) as client:
        docid=client.post("/api/v1/documents",files={"file":("sample.pdf",sample(),"application/pdf")}).json()["document"]["id"]
        worker=Worker(app.state.settings,app.state.workspace,app.state.queue,database=app.state.database)
        assert worker.serve(poll_seconds=0,max_jobs=1)==1
        page=client.get(f"/api/v1/documents/{docid}/pages/0").json()
        image=client.get(f"/api/v1/documents/{docid}/pages/0/preview.png").content
        with app.state.database.session() as session:
            revision=session.get(ParseRevision,session.get(Document,docid).active_revision_id)
            stats=dict(revision.stats);warnings=list(revision.warnings)
    return page,image,stats,warnings


def test_optional_grobid_changes_structure_preserves_source_and_coordinates(tmp_path,monkeypatch):
    tei=b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><div><head coords="1,60,140,60,10">Methods</head><p>This experiment measures an arithmetic mean of two inputs.</p></div></body></text></TEI>'
    def auxiliary(self,path):
        import sqlite3
        # 真实外部等待期间另一连接仍可写入，避免辅助器阻塞设置/标注操作。
        with sqlite3.connect(tmp_path/"assisted"/"reader.sqlite3",timeout=.2) as db:
            db.execute("CREATE TABLE network_wait_probe (value INTEGER)")
        return parse_tei(tei)
    monkeypatch.setattr(GrobidClient,"analyse",auxiliary)
    baseline,image,_,_=run(tmp_path/"baseline","")
    assisted,new_image,stats,_=run(tmp_path/"assisted","http://127.0.0.1:8070")
    methods=next(b for b in assisted["blocks"] if b["text"]=="Methods")
    assert methods["type"]=="heading"
    assert methods["metadata"]["grobid_type"]=="heading"
    assert stats["grobid"]["status"]=="ok" and stats["grobid"]["matched_blocks"]>=2
    assert [(b["text"],b["bbox"],b["raw_text"]) for b in baseline["blocks"]]==[(b["text"],b["bbox"],b["raw_text"]) for b in assisted["blocks"]]
    assert image==new_image


def test_optional_service_failure_does_not_fail_parse(tmp_path,monkeypatch):
    def unavailable(self,path):raise ValueError("Bad XML")
    monkeypatch.setattr(GrobidClient,"analyse",unavailable)
    page,_,stats,warnings=run(tmp_path,"http://127.0.0.1:8070")
    assert page["blocks"]
    assert stats["grobid"]["status"]=="fallback"
    assert any("GROBID" in message for message in warnings)
