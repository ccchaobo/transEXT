"""Package tracked project sources and deployment scripts; never package private data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'release/research-reader-docker.zip')
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode('utf-8').split('\0')
    roots={'apps','adapters','deploy','packages','services','scripts','tests','.github'}
    top={'Dockerfile','compose.yaml','deploy.sh','deploy.ps1','deploy.cmd','README.md','pyproject.toml','.dockerignore','.gitattributes','.gitignore','.env.example'}
    entries={}
    with zipfile.ZipFile(args.output,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(set(paths)):
            path=ROOT/name
            if not name or not path.is_file() or path.is_symlink():continue
            if not (name in top or name.split('/')[0] in roots or name.startswith('benchmarks/rr_m0/') or
                    (name.startswith('docs/') and not name.startswith('docs/acceptance/')) or name.startswith('plugins/zotero-reader-bridge/')):continue
            if any(part in {'__pycache__','.pytest_cache','node_modules','dist','build'} for part in Path(name).parts):continue
            entries[name]=hashlib.sha256(path.read_bytes()).hexdigest()
            bundle.write(path,'research-reader/'+name)
        bundle.writestr('research-reader/DEPLOYMENT-MANIFEST.json',json.dumps({'format':1,'files':entries},indent=2))
    checksum=hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_name(args.output.name+'.sha256').write_text(checksum+'\n',encoding='ascii')
    print(json.dumps({'package':str(args.output),'files':len(entries),'bytes':args.output.stat().st_size,'sha256':checksum}))


if __name__=='__main__':main()
