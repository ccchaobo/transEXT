"""Conservative scientific navigation; typography alone is not a section."""
import re

COMMON=re.compile(r'^(?:abstract|introduction|related\s+work|background|methods|methodology|materials\s+and\s+methods|experiments(?:\s+and\s+results)?|results(?:\s+and\s+discussion)?|discussion|conclusions?|limitations|references|bibliography|appendi(?:x|ces)|data\s+availability|acknowledg(?:e)?ments|CRediT\s+authorship\s+contribution\s+statement|supplementary\s+materials?|伦理声明|摘要|引言|绪论|相关工作|研究方法|实验结果|讨论|结论|参考文献|致谢|附录)$',re.I)
NUMBERED=re.compile(r'^(?P<number>\d{1,2}(?:\.\d{1,2})*)(?P<dot>\.)?\s+(?P<title>[A-Za-z\u4e00-\u9fff].*)$')
APPENDIX=re.compile(r'^(?P<number>[A-Z](?:\.\d+)*)(?P<dot>\.)?\s+(?P<title>[A-Za-z\u4e00-\u9fff].*)$')


def navigation_title(text):
    text=' '.join(text.split()).strip()
    if re.fullmatch(r'(?:[A-Z]\s+){2,}[A-Z]',text):
        compact=text.replace(' ','')
        if COMMON.fullmatch(compact):return compact.capitalize()
    return text


def navigation_level(text):
    text=navigation_title(text)
    if not text or len(text)>220 or len(text.split())>28:return None
    if re.search(r'[±=<>∑∫]|https?://|arXiv:|\b(?:Figure|Fig\.|Table)\s*\d',text,re.I):return None
    if text.endswith((',', ';', ':', '，', '；','。')):return None
    match=NUMBERED.fullmatch(text) or APPENDIX.fullmatch(text)
    if match:
        # "20 (A) Age Distribution", decimal metrics and list sentences are not headings.
        if text.endswith('.') or len(text.split())>24:return None
        number=match['number']
        if len(match['title'].split()[0])<2:return None
        if number.isalpha() and not match.groupdict().get('dot') and not match['title'][0].isupper():return None
        if number.isdigit() and int(number)==0:return None
        if number.isdigit() and not match.groupdict().get('dot') and not COMMON.fullmatch(match['title']) and not match['title'][0].isupper():return None
        return number.count('.')+1
    return 1 if COMMON.fullmatch(text.rstrip('.')) else None
