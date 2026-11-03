"""Pre-deploy SQLite safety snapshot.

The full legacy conversion runs after db.create_all() in app startup, because
new hierarchy tables must exist before we can preserve location relationships.
"""
from __future__ import annotations
import os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

def main():
    url=os.getenv('DATABASE_URL','')
    if url and not url.startswith('sqlite:'):
        print('Invotra preflight: external database configured; use provider-managed migrations/backups')
        return 0
    if url.startswith('sqlite:////'):
        dbfile=Path(url.replace('sqlite:////','/',1))
    elif Path('/data').is_dir():
        dbfile=Path('/data/invotra.db')
    elif os.getenv('INVOTRA_ENV')=='production':
        print('Invotra preflight: production /data disk missing')
        return 1
    else:
        return 0
    if not dbfile.is_file():
        print(f'Invotra preflight: new database {dbfile}')
        return 0
    dbfile.parent.joinpath('backups').mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest=dbfile.parent/'backups'/f'invotra-before-start-{stamp}.db'
    with sqlite3.connect(str(dbfile),timeout=30) as src, sqlite3.connect(str(dest)) as backup:
        if src.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            print('Invotra preflight: database integrity check failed; refusing startup')
            return 1
        src.backup(backup)
    print(f'Invotra preflight: protected backup at {dest}')
    return 0
if __name__=='__main__':
    sys.exit(main())
