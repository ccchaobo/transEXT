"""实际运行服务的自编样例联调；仅创建/删除本次样例，不使用用户文献。"""
import argparse
import io
import json
import time
import uuid
from pathlib import Path

import httpx
from reportlab.pdfgen import canvas


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:8765")
    parser.add_argument("--output",type=Path,default=Path("var/full-acceptance/live-stack.json"))
    parser.add_argument("--ask-ai",action="store_true",help="显式启用真实 AI 小样例请求，可能计费")
    parser.add_argument("--require-grobid",action="store_true",help="要求真实后台解析已调用 GROBID")
    args=parser.parse_args()
    stream=io.BytesIO()
    pdf=canvas.Canvas(stream,pagesize=(600,800))
    pdf.setFont("Helvetica-Bold",16); pdf.drawString(50,750,"Controlled acceptance sample "+uuid.uuid4().hex[:12])
    pdf.setFont("Helvetica",12); pdf.drawString(50,700,"The score S=(x+y)/2 is the arithmetic mean of x and y.")
    pdf.drawString(50,670,"For x=2 and y=4, the score S=3.")
    pdf.save()
    created_id=None
    result={"fixture":"自编算术平均数文献，未使用用户文献","checks":{}}
    with httpx.Client(trust_env=False,timeout=150) as client:
        try:
            health=client.get(args.url+"/health").raise_for_status().json()
            result["model"]=health["translation"].get("model")
            upload=client.post(args.url+"/api/v1/documents",files={"file":("rr-controlled-acceptance.pdf",stream.getvalue(),"application/pdf")}).raise_for_status().json()
            if upload["duplicate"]:
                raise RuntimeError("样例意外重复，未删除既有记录")
            created_id=upload["document"]["id"]
            for attempt in range(120):
                doc=client.get(f"{args.url}/api/v1/documents/{created_id}").raise_for_status().json()
                if doc["active_revision_id"]:
                    break
                if doc["parse_status"]=="failed":
                    raise RuntimeError("实际 worker 解析样例失败")
                time.sleep(.5)
            else:
                raise RuntimeError("实际 worker 未在等待期限内解析样例")
            result["checks"]["actual_worker_parse"]=True
            if args.require_grobid:
                for attempt in range(10):
                    jobs=client.get(f"{args.url}/api/v1/jobs?document_id={created_id}&limit=20").raise_for_status().json()["jobs"]
                    parsing=next((job for job in jobs if job["type"]=="parse" and job["document_id"]==created_id),{})
                    auxiliary=(parsing.get("result") or {}).get("stats",{}).get("grobid",{})
                    if auxiliary:break
                    time.sleep(.1)
                result["grobid"]=auxiliary
                result["checks"]["actual_worker_grobid"]=auxiliary.get("status")=="ok"

            page=client.get(f"{args.url}/api/v1/documents/{created_id}/pages/0").raise_for_status().json()
            result["checks"]["visual_regions_wire"]="visual_regions" in page["page"]
            state_url=f"{args.url}/api/v1/documents/{created_id}/reading-state"
            client.put(state_url,json={"view_mode":"overlay_side_by_side","page_index":0}).raise_for_status()
            result["checks"]["comparison_state_persists"]=client.get(state_url).json()["reading_state"]["view_mode"]=="overlay_side_by_side"
            if args.ask_ai:
                reply=client.post(f"{args.url}/api/v1/documents/{created_id}/assistant",json={
                    "question":"仅依据这份样例，x=2、y=4 时 S 是多少？简短解释并引用来源。","kind":"formula"}).raise_for_status().json()
                result["ai"]={"answer":reply["answer"],"model":reply["model"],"citations":reply["citations"],"usage":reply["usage"]}
                result["checks"]["real_ai_reply"]=bool(reply["answer"].strip()) and "3" in reply["answer"]
                result["checks"]["real_ai_citations"]=bool(reply["citations"])
        except Exception as exc:
            result["error"]=str(exc)
        finally:
            if created_id:
                client.delete(f"{args.url}/api/v1/documents/{created_id}?confirm=true").raise_for_status()
                result["checks"]["owned_fixture_removed"]=client.get(f"{args.url}/api/v1/documents/{created_id}").status_code==404
            required={"actual_worker_parse","visual_regions_wire","comparison_state_persists","owned_fixture_removed"}
            if args.require_grobid:
                required.add("actual_worker_grobid")
            if args.ask_ai:
                required.update({"real_ai_reply","real_ai_citations"})
            result["passed"]=not result.get("error") and required.issubset(result["checks"]) and all(result["checks"].values())
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed":result["passed"],"checks":result["checks"],"model":result.get("model")},ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
