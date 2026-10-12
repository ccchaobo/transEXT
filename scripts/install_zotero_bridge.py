"""Install the reviewed local bridge; pair only backend and Zotero profile secrets.

Close Zotero first. No bibliographic record is created/edited by this installer.
"""
import argparse
import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile
from rr_services import open_runtime
from rr_storage import get_setting,set_setting

ROOT=Path(__file__).resolve().parents[1]
ID='research-reader-bridge@local.research-reader'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',type=Path,required=True);args=p.parse_args()
    profile=args.profile.resolve();prefs=profile/'prefs.js'
    if not prefs.is_file():raise ValueError('Zotero profile prefs.js missing')
    # Native installation can occur with Zotero open; pairing prefs cannot.
    if (profile/'parent.lock').exists():
        try:
            with (profile/'parent.lock').open('r+b'):pass
        except OSError:raise RuntimeError('请先保存并正常退出 Zotero，再运行配对安装脚本。')
    code=ROOT/'plugins/zotero-reader-bridge'
    manifest=json.loads((code/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['applications']['zotero']['id']==ID
    output=ROOT/'var/zotero-bridge';output.mkdir(parents=True,exist_ok=True)
    archive=output/('research-reader-bridge-'+manifest['version']+'.xpi')
    with ZipFile(archive,'w') as z:
        for name in ['manifest.json','bootstrap.js']:z.write(code/name,name)
    runtime=open_runtime()
    with runtime.database.session() as session:
        token=get_setting(session,'zotero.bridge_token') or secrets.token_hex(32)
        set_setting(session,'zotero.bridge_token',token,is_secret=True)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    shutil.copy2(prefs,prefs.with_name('prefs.js.reader-bridge-backup-'+stamp))
    source=prefs.read_text(encoding='utf-8')
    key='extensions.zotero.researchReader.token'
    source=re.sub(r'user_pref\("'+re.escape(key)+r'",[^\n]+\);\n?','',source)
    prefs.write_text(source.rstrip()+'\nuser_pref('+json.dumps(key)+', '+json.dumps(token)+');\n',encoding='utf-8')
    destination=profile/'extensions'/(ID+'.xpi');destination.parent.mkdir(exist_ok=True)
    if destination.exists():shutil.copy2(destination,output/(ID+'.previous-'+stamp+'.xpi'))
    shutil.copy2(archive,destination)
    print(json.dumps({'prepared':str(destination),'archive':str(archive),'paired':True,'library_modified':False,
        'next_step':'在 Zotero 插件管理器选择此版本安装包；配对文件已准备不代表组件已经启用。'},ensure_ascii=False))


if __name__=='__main__':main()
