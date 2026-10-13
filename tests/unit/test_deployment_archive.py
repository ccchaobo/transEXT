import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

spec=importlib.util.spec_from_file_location('reader_archive',Path(__file__).resolve().parents[2]/'deploy/archive.py')
archive=importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


def workspace(path):
    path.mkdir()
    with sqlite3.connect(path/'reader.sqlite3') as db:
        db.execute('CREATE TABLE items (title TEXT)')
        db.execute('INSERT INTO items VALUES (?)',('测试文献',))
    (path/'originals').mkdir()
    (path/'originals'/'test.pdf').write_bytes(b'%PDF test')
    (path/'tmp').mkdir()
    (path/'tmp'/'incomplete').write_bytes(b'scratch')


def test_backup_restore_preserves_database_and_originals_without_overwriting(tmp_path):
    source=tmp_path/'source';workspace(source)
    output=tmp_path/'backups'/'reader.tar.gz'
    archive.create(source,output,'')
    restored=tmp_path/'restored'
    archive.restore(output,restored)
    assert (restored/'originals'/'test.pdf').read_bytes()==b'%PDF test'
    with sqlite3.connect(restored/'reader.sqlite3') as db:
        assert db.execute('SELECT title FROM items').fetchone()[0]=='测试文献'
    assert not (restored/'tmp').exists()
    with pytest.raises(ValueError,match='EMPTY'):archive.restore(output,restored)
    with pytest.raises(ValueError,match='overwrite'):archive.create(source,output,'')


def test_restore_rejects_checksum_mismatch_and_path_escape(tmp_path):
    output=tmp_path/'bad.tar.gz'
    with tarfile.open(output,'w:gz') as tar:
        entry=tarfile.TarInfo('../../escape');entry.size=3
        tar.addfile(entry,io.BytesIO(b'bad'))
    output.with_name(output.name+'.sha256').write_text('wrong')
    with pytest.raises(ValueError,match='checksum'):archive.restore(output,tmp_path/'restored')
    output.with_name(output.name+'.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest())
    with pytest.raises(ValueError,match='Unsafe'):archive.restore(output,tmp_path/'restored')
    assert not (tmp_path.parent/'escape').exists()
