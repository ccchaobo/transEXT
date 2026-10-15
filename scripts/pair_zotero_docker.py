"""Reuse the already-authorized Zotero pairing in a same-host Docker reader.

No Zotero preferences or library records are changed. The existing key travels
over subprocess stdin, never command-line arguments, a file, or console output.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import urllib.request


def profile_key(profile):
    source=(Path(profile)/'prefs.js').read_text(encoding='utf-8')
    match=re.search(r'^user_pref\("extensions\.zotero\.researchReader\.token",\s*("(?:\\.|[^"\\])*")\);$',source,re.M)
    key=json.loads(match[1]) if match else None
    if not isinstance(key,str) or len(key)<32:
        raise ValueError('Zotero 尚未初次配对，请先按安装说明完成本机配对。')
    return key


CONTAINER_CODE='''
import json,sys,httpx
from rr_services import open_runtime
from rr_storage import set_setting
value=json.load(sys.stdin)
runtime=open_runtime()
url='http://'+runtime.settings.zotero_host+':23119/research-reader/status'
headers={'Host':'127.0.0.1:23119','Zotero-Allowed-Request':'1'}
with httpx.Client(trust_env=False,timeout=10,follow_redirects=False) as client:
    initial=client.post(url,json={},headers=headers)
    initial.raise_for_status()
    if initial.json().get('instance')!=value['instance']:
        raise SystemExit('Zotero instance mismatch; no key sent and pairing not changed')
    headers['X-Research-Reader-Key']=value['key']
    response=client.post(url,json={},headers=headers)
    response.raise_for_status()
    state=response.json()
if not state.get('authorized') or state.get('instance')!=value['instance']:
    raise SystemExit('Zotero instance or authorization mismatch; pairing not changed')
with runtime.database.session() as session:
    set_setting(session,'zotero.bridge_token',value['key'],is_secret=True)
print(json.dumps({'paired':True,'bridge_version':state['bridge_version'],'zotero_version':state['zotero_version'],'library_modified':False}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--container',default='research-reader-api-1')
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',args.container):parser.error('Invalid container name')
    key=profile_key(args.profile)
    request=urllib.request.Request('http://127.0.0.1:23119/research-reader/status',data=b'{}',
        headers={'Content-Type':'application/json','X-Research-Reader-Key':key,'Zotero-Allowed-Request':'1'})
    with urllib.request.urlopen(request,timeout=10) as response:state=json.load(response)
    if not state.get('authorized'):raise SystemExit('Zotero 未授权当前配对，未修改 Docker 配置。')
    inspected=subprocess.run(['docker','inspect',args.container],check=True,capture_output=True,text=True)
    container=json.loads(inspected.stdout)[0]
    if container.get('Config',{}).get('Labels',{}).get('com.docker.compose.service')!='api' or not container.get('State',{}).get('Running'):
        raise SystemExit('目标不是正在运行的阅读器 Compose API 容器，未修改配置。')
    result=subprocess.run(['docker','exec','-i',args.container,'python','-c',CONTAINER_CODE],
        input=json.dumps({'key':key,'instance':state['instance']}),capture_output=True,text=True)
    if result.returncode:raise SystemExit('Docker 配对未完成，请检查容器连接配置及 Zotero 状态；未输出密钥。')
    print(result.stdout.strip())


if __name__=='__main__':main()
