"""真实本地 GROBID 与阅读器解析联调；仅使用自编 PDF 和隔离数据库。"""
import argparse,io,json,tempfile
from pathlib import Path
from reportlab.pdfgen import canvas
from fastapi.testclient import TestClient
from rr_api import create_app
from rr_services import Settings
from rr_worker import Worker
from rr_storage import Document,ParseRevision


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:8070")
    parser.add_argument("--output",type=Path,default=Path("var/full-acceptance/grobid-pipeline-real.json"))
    args=parser.parse_args();report={"fixture":"self-authored PDF; isolated database","passed":False}
    stream=io.BytesIO();pdf=canvas.Canvas(stream,pagesize=(600,800))
    pdf.setFont("Helvetica-Bold",16);pdf.drawString(50,750,"A controlled parsing experiment")
    pdf.setFont("Helvetica-Bold",12);pdf.drawString(50,690,"1 Introduction")
    pdf.setFont("Helvetica",10);pdf.drawString(50,660,"This artificial paper evaluates an arithmetic mean of two inputs.")
    pdf.drawString(50,640,"The score is S=(x+y)/2. The text is only a software test fixture.")
    pdf.setFont("Helvetica-Bold",12);pdf.drawString(50,560,"2 Methods")
    pdf.setFont("Helvetica",10);pdf.drawString(50,530,"The experiment uses two controlled inputs. No real research data are used.")
    pdf.setFont("Helvetica-Bold",12);pdf.drawString(50,440,"References")
    pdf.setFont("Helvetica",10);pdf.drawString(50,410,"[1] A. Example. A fictional study. Test Journal, 2025.")
    pdf.save()
    try:
        with tempfile.TemporaryDirectory(prefix="rr-grobid-pipeline-",dir="var") as temporary:
            app=create_app(Settings(data_dir=Path(temporary),grobid_url=args.url),serve_frontend=False)
            with TestClient(app) as client:
                docid=client.post("/api/v1/documents",files={"file":("sample.pdf",stream.getvalue(),"application/pdf")}).json()["document"]["id"]
                worker=Worker(app.state.settings,app.state.workspace,app.state.queue,database=app.state.database)
                assert worker.serve(poll_seconds=0,max_jobs=1)==1
                page=client.get(f"/api/v1/documents/{docid}/pages/0").json()
                with app.state.database.session() as session:
                    revision=session.get(ParseRevision,session.get(Document,docid).active_revision_id)
                    report["grobid"]=dict(revision.stats.get("grobid",{}));report["warnings"]=list(revision.warnings)
                report["block_count"]=len(page["blocks"])
                report["matched_types"]=[b["metadata"].get("grobid_type") for b in page["blocks"] if b["metadata"].get("grobid_type")]
                report["source_image_available"]=client.get(f"/api/v1/documents/{docid}/pages/0/preview.png").status_code==200
                report["all_blocks_have_coordinates"]=all(b["spans"] for b in page["blocks"])
                report["passed"]=report["grobid"].get("status")=="ok" and report["grobid"].get("matched_blocks",0)>0 and report["source_image_available"] and report["all_blocks_have_coordinates"]
            app.state.database.engine.dispose()
    except Exception as error:
        report["passed"]=False
        report["error"]=str(error)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False));return 0 if report["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
