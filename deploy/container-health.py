"""Container liveness checks; functional job completion is a separate acceptance."""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

if sys.argv[1] == 'api':
    with urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3) as response:
        assert json.load(response)['status'] == 'ok'
else:
    assert any(b'rr_worker' in path.read_bytes().split(b'\0') for path in Path('/proc').glob('[0-9]*/cmdline') if path.is_file())
    with sqlite3.connect('file:/data/reader.sqlite3?mode=ro', uri=True, timeout=3) as db:
        db.execute('SELECT 1 FROM jobs LIMIT 1').fetchall()
