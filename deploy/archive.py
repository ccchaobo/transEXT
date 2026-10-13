"""Consistent workspace archives; run inside the image, with writers stopped."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create(source, output, owner):
    if output.exists():
        raise ValueError('Refusing to overwrite an existing backup')
    if output.is_relative_to(source):
        raise ValueError('Archive must be outside the workspace')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.reader-backup-') as temporary:
        stage = Path(temporary)
        database = source/'reader.sqlite3'
        if not database.is_file():
            raise ValueError('Workspace database does not exist')
        with closing(sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True)) as original, closing(sqlite3.connect(stage/'reader.sqlite3')) as snapshot:
            original.backup(snapshot)
            assert snapshot.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        archive = stage/'workspace.tar.gz'
        manifest = stage/'manifest.json'
        manifest.write_text(json.dumps({'format':'research-reader-backup','version':1}), encoding='utf-8')
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(stage/'reader.sqlite3', arcname='reader.sqlite3')
            tar.add(manifest, arcname='manifest.json')
            for name in ['originals','derived','exports']:
                if (source/name).is_dir():
                    tar.add(source/name, arcname=name)
        archive.rename(output)
    checksum = output.with_name(output.name+'.sha256')
    checksum.write_text(digest(output)+'\n', encoding='ascii')
    for path in [output, checksum]:
        path.chmod(0o600)
        if owner and hasattr(os, 'chown'):
            uid, gid = map(int, owner.split(':'))
            os.chown(path, uid, gid)
    print(json.dumps({'archive':output.name,'sha256':digest(output),'bytes':output.stat().st_size}))


def restore(archive, destination, owner=''):
    checksum = archive.with_name(archive.name+'.sha256').read_text(encoding='ascii').strip()
    if digest(archive) != checksum:
        raise ValueError('Backup checksum mismatch')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Restore requires an EMPTY workspace; existing data is never overwritten')
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        for member in members:
            target = (destination/member.name).resolve()
            if not target.is_relative_to(destination) or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe archive entry')
            if member.name.split('/')[0] not in {'manifest.json','reader.sqlite3','originals','derived','exports'}:
                raise ValueError('Unknown workspace entry')
        manifest = json.load(tar.extractfile('manifest.json'))
        if manifest != {'format':'research-reader-backup','version':1}:
            raise ValueError('Unsupported backup format')
        tar.extractall(destination, members=[m for m in members if m.name!='manifest.json'], filter='data')
    with closing(sqlite3.connect(destination/'reader.sqlite3')) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Restored database failed integrity check')
    if owner and hasattr(os, 'chown'):
        uid,gid=map(int,owner.split(':'))
        for path in [destination,*destination.rglob('*')]:
            os.chown(path,uid,gid)
    print(json.dumps({'restored':True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create','restore'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--owner', default='')
    args = parser.parse_args()
    if args.action == 'create':
        create(args.source.resolve(), args.destination.resolve(), args.owner)
    else:
        restore(args.source.resolve(), args.destination.resolve(), args.owner or '10001:10001')
