"""Preflight check works with only Python standard library, independent of Flask."""
from pathlib import Path
import importlib.util
import sqlite3

SCRIPT=Path(__file__).resolve().parents[1]/'scripts'/'db_preflight.py'
spec=importlib.util.spec_from_file_location('db_preflight',SCRIPT)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_sqlite_preflight_backup_and_no_data_loss(tmp_path,monkeypatch):
    path=tmp_path/'invotra.db'
    with sqlite3.connect(str(path)) as db:
        for table, cols in m.CHECK.items():
            schema=','.join(f'"{field}" TEXT' for field in sorted(cols))
            db.execute(f'CREATE TABLE "{table}"({schema})')
        db.execute('INSERT INTO "company" ("id","name","active") VALUES (?, ?, ?)',
                   ('7','Keep My Company','1'))
        db.commit()
    monkeypatch.setenv('INVOTRA_ENV','production')
    monkeypatch.setenv('DATABASE_URL','sqlite:////'+str(path).lstrip('/'))
    assert m.main()==0
    backup=next((tmp_path/'backups').glob('invotra-before-start-*.db'))
    with sqlite3.connect(str(backup)) as db:
        assert db.execute('SELECT name FROM company WHERE id=?',('7',)).fetchone()[0]=='Keep My Company'
    with sqlite3.connect(str(path)) as db:
        assert db.execute('SELECT name FROM company WHERE id=?',('7',)).fetchone()[0]=='Keep My Company'


def test_preflight_blocks_incompatible_schema_but_still_backs_up(tmp_path,monkeypatch):
    path=tmp_path/'invotra.db'
    with sqlite3.connect(str(path)) as db:
        db.execute('CREATE TABLE company (id INTEGER, name TEXT)')
        db.commit()
    monkeypatch.setenv('INVOTRA_ENV','production')
    monkeypatch.setenv('DATABASE_URL','sqlite:////'+str(path).lstrip('/'))
    assert m.main()==1
    assert list((tmp_path/'backups').glob('*.db'))


def test_legacy_user_phone_migration_is_additive_and_repeatable(tmp_path, monkeypatch):
    path = tmp_path / 'invotra.db'
    with sqlite3.connect(str(path)) as db:
        for table, fields in m.CHECK.items():
            existing = fields - {'phone'} if table == 'user' else fields
            cols = ','.join(f'"{field}" TEXT' for field in sorted(existing))
            db.execute(f'CREATE TABLE "{table}"({cols})')
        db.execute('INSERT INTO "user" (id, email, full_name) VALUES (?, ?, ?)', ('21', 'existing@example.com', 'Existing Employee'))
        db.commit()
    monkeypatch.setenv('INVOTRA_ENV', 'production')
    monkeypatch.setenv('DATABASE_URL', 'sqlite:////' + str(path).lstrip('/'))
    assert m.main() == 0
    assert m.main() == 0  # repeated deploy must be safe
    with sqlite3.connect(str(path)) as db:
        cols = {row[1] for row in db.execute('PRAGMA table_info("user")')}
        assert 'phone' in cols
        assert db.execute('SELECT email, full_name FROM "user" WHERE id=?', ('21',)).fetchone() == ('existing@example.com','Existing Employee')
    backup = sorted((tmp_path / 'backups').glob('invotra-before-start-*.db'))[0]
    with sqlite3.connect(str(backup)) as db:
        assert 'phone' not in {row[1] for row in db.execute('PRAGMA table_info("user")')}
