"""可选、本地 GROBID 客户端；TEI 仅用于结构证据，不替换原页坐标。"""
from dataclasses import dataclass, field
from pathlib import Path
import ipaddress
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import httpx

TEI = "{http://www.tei-c.org/ns/1.0}"
MAX_TEI_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class StructureHint:
    kind: str
    text: str
    pages: tuple[int, ...] = ()


@dataclass
class GrobidResult:
    title: str = ""
    hints: list[StructureHint] = field(default_factory=list)


def local_endpoint(url: str, allowed_hosts: tuple[str, ...] = ()) -> str:
    parsed = urlparse(url)
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        local = False
    explicitly_trusted = parsed.hostname in allowed_hosts
    if parsed.scheme != "http" or not (local or explicitly_trusted) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("GROBID 地址必须是本机 HTTP 回环地址或管理员显式允许的内部服务名")
    if parsed.path.rstrip("/") not in {"", "/api"}:
        raise ValueError("GROBID 地址只接受服务根路径或 /api")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("GROBID 端口无效")
    return url.rstrip("/").removesuffix("/api") + "/api/processFulltextDocument"


def parse_tei(data: bytes) -> GrobidResult:
    if len(data)>MAX_TEI_BYTES:
        raise ValueError("GROBID XML 超过读取上限")
    decoded=data.decode("utf-8")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", decoded, re.I):
        raise ValueError("GROBID XML 过大或含不支持的实体声明")
    try:
        root=ET.fromstring(decoded)
    except ET.ParseError as error:
        raise ValueError("GROBID XML 不完整或格式错误") from error
    if root.tag != TEI+"TEI":
        raise ValueError("GROBID 没有返回 TEI 文档")
    def text(node):
        return "".join(node.itertext()).strip() if node is not None else ""
    result=GrobidResult(title=text(root.find(f"{TEI}teiHeader/{TEI}fileDesc/{TEI}titleStmt/{TEI}title")))
    def add(kind,node):
        value=text(node)
        pages=[]
        for box in node.get("coords", "").split(";"):
            try:
                page=int(box.split(",")[0])-1
                if page>=0: pages.append(page)
            except ValueError:
                pass
        if value:
            result.hints.append(StructureHint(kind,value,tuple(sorted(set(pages)))))
    body=root.find(f"{TEI}text/{TEI}body")
    if body is not None:
        figures={id(n) for fig in body.iter(TEI+"figure") for n in fig.iter()}
        for node in body.iter():
            if id(node) in figures:
                continue
            if node.tag == TEI+"head": add("heading",node)
            elif node.tag == TEI+"p": add("paragraph",node)
        for node in body.iter(TEI+"figDesc"): add("caption",node)
    for abstract in root.iter(TEI+"abstract"):
        for node in abstract.iter(TEI+"p"): add("abstract",node)
    for node in root.iter(TEI+"biblStruct"):
        raw=next((n for n in node.iter(TEI+"note") if n.get("type")=="raw_reference"),None)
        if raw is not None:
            add("reference",raw)
    return result


class GrobidClient:
    def __init__(self,url: str,timeout: float=45,client=None,*,allowed_hosts: tuple[str, ...] = ()):
        self.endpoint=local_endpoint(url,allowed_hosts)
        self.timeout=timeout
        self.client=client

    def analyse(self,path: Path) -> GrobidResult:
        owned=self.client is None
        client=self.client or httpx.Client(timeout=httpx.Timeout(self.timeout,connect=3),trust_env=False,follow_redirects=False)
        try:
            with path.open("rb") as source:
                with client.stream("POST",self.endpoint,files={"input":(path.name,source,"application/pdf")},
                    data={"consolidateHeader":"0","consolidateCitations":"0","consolidateFunders":"0",
                          "includeRawCitations":"1","teiCoordinates":["head","p","biblStruct"]},
                    headers={"Accept":"application/xml"}) as response:
                    if response.status_code != 200:
                        raise ValueError(f"GROBID 返回 HTTP {response.status_code}")
                    pieces=[]; size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>MAX_TEI_BYTES: raise ValueError("GROBID XML 超过读取上限")
                        pieces.append(chunk)
            return parse_tei(b"".join(pieces))
        finally:
            if owned: client.close()
