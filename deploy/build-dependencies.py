"""Validate a trusted dependency cache and write the canonical runtime requirements."""
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import sys
import tomllib


def compatible(requirements, installed=version):
    for requirement in requirements:
        pin=re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s]+)',requirement)
        if not pin:return False
        try:
            if installed(pin[1])!=pin[2]:return False
        except PackageNotFoundError:return False
    return True


def main():
    base=Path('/tmp/reader-python-base')/Path(sys.executable).resolve().name
    if not base.is_file() or hashlib.sha256(base.read_bytes()).digest()!=hashlib.sha256(Path(sys.executable).resolve().read_bytes()).digest():
        raise SystemExit('Dependency cache uses a different Python base; omit DEPENDENCY_CACHE_IMAGE and rebuild normally.')
    project=tomllib.loads(Path('pyproject.toml').read_text())['project']
    direct=project['dependencies']+project['optional-dependencies']['ocr']
    locked=[line.strip() for line in Path('requirements.lock').read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
    Path('/tmp/reader-runtime-requirements.txt').write_text('\n'.join(direct)+'\n')
    cached=compatible(direct+locked)
    print('Using verified dependency cache' if cached else 'Installing locked runtime dependencies')
    return 0 if cached else 1


if __name__=='__main__':raise SystemExit(main())
