import sqlite3
from pathlib import Path
from scripts.legacy_schema import migrate


def test_v7_inventory_history_migration(tmp_path):
    path=tmp_path/'invotra.db'
    db=sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE user(id INTEGER PRIMARY KEY,full_name TEXT,email TEXT,password_hash TEXT,role TEXT,status TEXT,initial_password_reset_required INTEGER,created_at TEXT,phone_number TEXT);
        CREATE TABLE company(id INTEGER PRIMARY KEY,name TEXT,active INTEGER);
        CREATE TABLE location(id INTEGER PRIMARY KEY,company_id INTEGER,name TEXT,code TEXT,parent_id INTEGER,location_type TEXT,active INTEGER);
        CREATE TABLE user_location_access(id INTEGER PRIMARY KEY,user_id INTEGER,location_id INTEGER);
        CREATE TABLE gl_account(id INTEGER PRIMARY KEY,company_id INTEGER,code TEXT,name TEXT,active INTEGER);
        CREATE TABLE item_master(id INTEGER PRIMARY KEY,company_id INTEGER,item_name TEXT,each_upc_code TEXT,each_cost NUMERIC,case_upc_code TEXT,case_cost NUMERIC,gl_account_id INTEGER,active INTEGER,created_at TEXT);
        CREATE TABLE inventory_session(id INTEGER PRIMARY KEY,location_id INTEGER,created_by_user_id INTEGER,status TEXT,started_at TEXT,submitted_at TEXT,approved_at TEXT,approved_by_user_id INTEGER,rejection_note TEXT);
        CREATE TABLE inventory_line(id INTEGER PRIMARY KEY,session_id INTEGER,item_master_id INTEGER,item_name TEXT,upc_code TEXT,unit_type TEXT,unit_cost NUMERIC,gl_account_id INTEGER,quantity INTEGER,is_missing INTEGER);
        CREATE TABLE unknown_item(id INTEGER PRIMARY KEY,session_id INTEGER,submitted_by_user_id INTEGER,company_id INTEGER,proposed_name TEXT,each_upc_code TEXT,each_cost NUMERIC,case_upc_code TEXT,case_cost NUMERIC,gl_account_id INTEGER,status TEXT,reviewed_at TEXT);
        CREATE TABLE parent_location(id INTEGER PRIMARY KEY,company_id INTEGER,name TEXT,active INTEGER);
        CREATE TABLE sub_location(id INTEGER PRIMARY KEY,parent_id INTEGER,name TEXT,active INTEGER);
        CREATE TABLE user_sub_location_access(id INTEGER PRIMARY KEY,user_id INTEGER,sub_location_id INTEGER);
        INSERT INTO user VALUES(1,'Alice','alice@example.com','hash','admin','active',0,'2026-01-01','555-0101');
        INSERT INTO company VALUES(1,'Sodexo',1);
        INSERT INTO location VALUES(1,1,'East Texas A&M','ETAMU',NULL,'parent',1);
        INSERT INTO location VALUES(2,1,'C-Store','STORE',1,'subunit',1);
        INSERT INTO user_location_access VALUES(1,1,1);
        INSERT INTO gl_account VALUES(1,1,'60110020','BEVERAGE',1);
        INSERT INTO item_master VALUES(1,1,'Cherry Cola','12345',2.00,'4444',20.00,1,1,'2026-01-01');
        INSERT INTO inventory_session VALUES(5,2,1,'approved','2026-01-02','2026-01-02','2026-01-03',1,NULL);
        INSERT INTO inventory_line VALUES(10,5,1,'Cherry Cola','12345','each',2.00,1,3,0);
        INSERT INTO unknown_item VALUES(9,5,1,1,'Unknown Soda','9999',1.00,NULL,NULL,1,'pending',NULL);
    ''')
    db.commit(); db.close()
    changes=migrate(path)
    assert changes
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        sess=conn.execute('SELECT sub_location_id,total_quantity,total_value,approved_by_id FROM inventory_session WHERE id=5').fetchone()
        assert sess['sub_location_id'] is not None and sess['total_quantity']==3 and sess['total_value']==6
        assert sess['approved_by_id']==1
        line=conn.execute('SELECT scanned_upc,item_name_snapshot,gl_label_snapshot,extended_value FROM inventory_line WHERE id=10').fetchone()
        assert line['scanned_upc']=='12345' and line['item_name_snapshot']=='Cherry Cola' and line['extended_value']==6
        assert 'BEVERAGE' in line['gl_label_snapshot']
        assert conn.execute('SELECT phone FROM user WHERE id=1').fetchone()[0]=='555-0101'
        assert conn.execute('SELECT COUNT(*) FROM user_sub_location_access WHERE user_id=1').fetchone()[0]==2
        assert conn.execute('SELECT scanned_upc,sub_location_id FROM unknown_item WHERE id=9').fetchone()['scanned_upc']=='9999'
        assert conn.execute('SELECT each_upc FROM item_master WHERE id=1').fetchone()[0]=='12345'
    assert migrate(path) == ['legacy hierarchy mapped: 2 locations']
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sub_location').fetchone()[0]==2


def test_legacy_nonnullable_location_allows_new_sessions_and_preserves_history(tmp_path):
    from scripts.legacy_schema import relax_legacy_session_location_constraint
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as con:
        con.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE location(id INTEGER PRIMARY KEY);
            CREATE TABLE inventory_session (
                id INTEGER PRIMARY KEY,
                location_id INTEGER NOT NULL REFERENCES location(id),
                sub_location_id INTEGER,
                created_by_user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                started_at TEXT
            );
            CREATE INDEX idx_invotra_sessions ON inventory_session(created_by_user_id);
            CREATE TABLE inventory_line(id INTEGER PRIMARY KEY,session_id INTEGER NOT NULL REFERENCES inventory_session(id));
            INSERT INTO location(id) VALUES (7);
            INSERT INTO inventory_session(id,location_id,sub_location_id,created_by_user_id,status)
                VALUES (12,7,33,4,'approved');
            INSERT INTO inventory_line(id,session_id) VALUES (1,12);
        ''')
    assert relax_legacy_session_location_constraint(path)
    with sqlite3.connect(path) as con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute("INSERT INTO inventory_session (sub_location_id,created_by_user_id,status) VALUES (1,4,'in_progress')")
        assert con.execute('SELECT location_id,sub_location_id FROM inventory_session WHERE id=12').fetchone() == (7,33)
        assert con.execute('SELECT location_id,sub_location_id FROM inventory_session WHERE id=13').fetchone() == (None,1)
        assert con.execute('SELECT session_id FROM inventory_line WHERE id=1').fetchone()[0] == 12
        assert con.execute('SELECT name FROM sqlite_master WHERE type="index" AND name="idx_invotra_sessions"').fetchone()
        assert con.execute('PRAGMA foreign_key_check').fetchall() == []
    assert not relax_legacy_session_location_constraint(path)


def test_migrate_relaxes_location_id_and_creates_backup(tmp_path):
    path = tmp_path / 'db.sqlite'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE inventory_session(id INTEGER PRIMARY KEY, location_id INTEGER NOT NULL, sub_location_id INTEGER)')
    # The generic migrator must remove the old NOT NULL requirement even when
    # other legacy tables are absent.
    assert any("location_id now nullable" in action for action in migrate(path))
    with sqlite3.connect(path) as con:
        assert con.execute('PRAGMA table_info(inventory_session)').fetchall()[1][3] == 0
    assert list((tmp_path / 'backups').glob('*.db'))
