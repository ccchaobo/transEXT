"""把官方源码构建出的两个 ZIP 整理为可重复打包的 Linux 运行目录。"""
import argparse,hashlib,json,zipfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts",type=Path,default=Path("var/grobid-build-cache"))
    parser.add_argument("--output",type=Path,default=Path("var/grobid-runtime"))
    args=parser.parse_args();root=args.output.resolve();marker=root/".reader-grobid-runtime.json"
    if root.exists() and any(root.iterdir()) and not marker.exists():
        raise ValueError("输出目录已有非本脚本管理的文件，请选择空目录")
    root.mkdir(parents=True,exist_ok=True)
    checksums={}
    for name in ["grobid-service-0.9.1.zip","grobid-home-0.9.1.zip"]:
        archive=args.artifacts/name
        checksums[name]=hashlib.sha256(archive.read_bytes()).hexdigest()
        with zipfile.ZipFile(archive) as zip:
            for item in zip.infolist():
                if not (root/item.filename).resolve().is_relative_to(root):raise ValueError("ZIP 路径越界")
            zip.extractall(root)
    old=(root/"grobid-service-0.9.1").resolve();new=(root/"grobid-service").resolve()
    if old.exists():
        if new.exists():
            # 重新准备时保留现有目录；版本输出由明确文件内容覆盖。
            import shutil
            for item in old.iterdir():
                target=new/item.name
                if item.is_dir():shutil.copytree(item,target,dirs_exist_ok=True)
                else:shutil.copy2(item,target)
            assert old.is_relative_to(root) and old.name == "grobid-service-0.9.1"
            shutil.rmtree(old)
        else:
            assert old.is_relative_to(root) and new.is_relative_to(root)
            old.rename(new)
    for wrapper in (root/"grobid-home/pdfalto").glob("**/*_server"):
        wrapper.write_bytes(wrapper.read_bytes().replace(b"\r\n",b"\n"))
    config=root/"grobid-home/config/grobid.yaml"
    value=config.read_text(encoding="utf-8").replace("memoryLimitMb: 6096","memoryLimitMb: 1024").replace("timeoutSec: 120","timeoutSec: 40")
    config.write_text(value,encoding="utf-8")
    marker.write_text(json.dumps({"version":"0.9.1","source_commit":"523bb8e97f357e93682dc4eaa6d22faa8b5b93de","artifacts":checksums},indent=2),encoding="utf-8")
    print(json.dumps({"prepared":True,"output":str(root),"artifacts":checksums}))


if __name__=="__main__":main()
