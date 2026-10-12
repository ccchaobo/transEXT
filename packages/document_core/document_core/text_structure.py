"""Semantic paragraph/list boundaries, independent of PDF visual wrapping."""
import re
from statistics import median
from .cleaning import clean_text

LIST_START = re.compile(r'^\s*(?P<marker>[•●▪◦‣⁃○■□∙·➢➤\uf0b7]|[-–—](?=\s)|[（(](?:\d{1,3}|[ivxlcdmIVXLCDM]{1,8}|[a-zA-Z])[)）]|\d{1,3}[.、](?=\s))\s*(?P<text>\S.*)$')


def structure(raw_text, spans=(), *, block_type='paragraph'):
    lines=[]
    for span in sorted(spans,key=lambda s:getattr(s,'ordinal',0)):
        text=raw_text[span.text_start:span.text_end].strip()
        if text:lines.append((text,span.bbox[0],span.bbox[3]-span.bbox[1]))
    if not lines:
        lines=[(line.strip(),len(line)-len(line.lstrip()),1) for line in raw_text.splitlines() if line.strip()]
    if not lines:return {'parts':[],'has_list':False,'version':1}
    base=min(x for _,x,_ in lines)
    em=max(median(h for _,_,h in lines)*1.05,1e-6)
    parts=[];current=[];marker='';start_x=lines[0][1]
    def flush():
        if not current:return
        text=clean_text('\n'.join(text for text,_,_ in current))
        continuation=min((x for _,x,_ in current[1:]),default=start_x)
        parts.append({'kind':'list_item' if marker else 'paragraph','marker':marker,'text':text,
            'indent_em':round(min(8,max(0,(start_x-base)/em)),3) if marker else 0,
            'first_indent_em':round(min(4,max(0,(start_x-continuation)/em)),3) if not marker else 0,
            'hanging_em':max(1,len(marker)*.55+.35) if marker else 0})
    for text,x,h in lines:
        match=LIST_START.match(text)
        # A numbered heading is not a list item; keep its original text intact.
        if match and not (block_type in {'title','heading'} and match['marker'].endswith('.')):
            flush();current=[];marker=match['marker'];start_x=x;current.append((match['text'],x,h))
        else:current.append((text,x,h))
    flush()
    return {'parts':parts,'has_list':any(p['kind']=='list_item' for p in parts),'version':1}


def translation_input(raw_text,spans=(),*,block_type='paragraph',fallback=''):
    value=structure(raw_text,spans,block_type=block_type)
    if not value['has_list']:return fallback or clean_text(raw_text)
    return '\n'.join(('  '*int(round(p['indent_em']/1.5)) + (p['marker']+' ' if p['marker'] else '') + p['text']) for p in value['parts'])


def _marker_pattern(marker):
    wrapped=re.fullmatch(r'[（(](.+?)[)）]',marker)
    if wrapped:return r'[（(]\s*'+re.escape(wrapped[1])+r'\s*[)）]'
    numbered=re.fullmatch(r'(\d+)[.、]',marker)
    if numbered:return re.escape(numbered[1])+r'[.、]'
    if marker in {'-','–','—'}:return r'[-–—]'
    return re.escape(marker)


def translation_parts(value,text):
    source=value.get('parts',[])
    if not value.get('has_list'):
        indent=source[0].get('first_indent_em',0) if source else 0
        return {'parts':[{'kind':'paragraph','text':text,'first_indent_em':indent}], 'preserved':True}
    items=[p for p in source if p['kind']=='list_item'];positions=[];cursor=0
    for part in items:
        match=re.search(_marker_pattern(part['marker']),text[cursor:],flags=re.I)
        if not match:return {'parts':[{'kind':'paragraph','text':text}], 'preserved':False}
        positions.append((cursor+match.start(),cursor+match.end(),part));cursor+=match.end()
    parts=[]
    intro=text[:positions[0][0]].strip()
    if intro:parts.append({'kind':'paragraph','text':intro})
    for index,(start,end,part) in enumerate(positions):
        stop=positions[index+1][0] if index+1<len(positions) else len(text)
        parts.append({**part,'marker':text[start:end],'text':text[end:stop].strip()})
    return {'parts':parts,'preserved':True}
