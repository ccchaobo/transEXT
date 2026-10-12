"""验证真实本地 GROBID；只发送临时生成的测试 PDF，不使用用户文献。"""
import argparse
import json
from pathlib import Path
import tempfile
from reportlab.pdfgen import canvas
from rr_adapters.grobid import GrobidClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:8070")
    parser.add_argument("--output",type=Path,default=Path("var/full-acceptance/grobid-live.json"))
    args=parser.parse_args()
    report={"fixture":"self-authored PDF","passed":False}
    try:
        client=GrobidClient(args.url)
        with tempfile.TemporaryDirectory(prefix="rr-grobid-") as temporary:
            path=Path(temporary)/"sample.pdf"
            pdf=canvas.Canvas(str(path),pagesize=(600,800))
            pdf.setFont("Helvetica-Bold",16);pdf.drawString(50,750,"A controlled parsing experiment")
            pdf.setFont("Helvetica-Bold",12);pdf.drawString(50,680,"1 Introduction")
            pdf.setFont("Helvetica",10);pdf.drawString(50,650,"This artificial paper evaluates an arithmetic mean of two inputs.")
            pdf.drawString(50,630,"The score is S=(x+y)/2. The text is only a software test fixture.")
            pdf.setFont("Helvetica-Bold",12);pdf.drawString(50,570,"References")
            pdf.setFont("Helvetica",10);pdf.drawString(50,540,"[1] A. Example. A fictional study. Test Journal, 2025.")
            pdf.save()
            result=client.analyse(path)
            report.update(passed=bool(result.hints),title=result.title,hints=[{"kind":h.kind,"text":h.text,"pages":h.pages} for h in result.hints])
    except Exception as error:
        report["error"]=f"{type(error).__name__}: {error}"
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"hint_count":len(report.get("hints",[])),"error":report.get("error")},ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
