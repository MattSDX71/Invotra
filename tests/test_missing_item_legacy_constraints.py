import sqlite3
from scripts.legacy_schema import migrate, relax_legacy_item_constraints


def test_existing_rows_preserved_and_new_missing_item_works(tmp_path):
    path = tmp_path / 'invotra.db'
    with sqlite3.connect(path) as con:
        con.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE company(id INTEGER PRIMARY KEY);
            CREATE TABLE inventory_session(id INTEGER PRIMARY KEY);
            CREATE TABLE unknown_item (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES inventory_session(id),
                company_id INTEGER NOT NULL REFERENCES company(id),
                gl_account_id INTEGER NOT NULL,
                each_upc_code VARCHAR(100),
                each_cost NUMERIC(10,2) NOT NULL,
                sub_location_id INTEGER,
                scanned_upc VARCHAR(100),
                proposed_name VARCHAR(255) NOT NULL,
                proposed_gl_account_id INTEGER,
                status VARCHAR(30) NOT NULL
            );
            CREATE INDEX ix_unknown_item_session_id ON unknown_item(session_id);
            CREATE TABLE inventory_line (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL REFERENCES inventory_session(id),
                unknown_item_id INTEGER REFERENCES unknown_item(id),
                upc_code VARCHAR(100) NOT NULL,
                is_missing BOOLEAN NOT NULL,
                scanned_upc VARCHAR(100),
                item_name_snapshot TEXT,
                quantity INTEGER NOT NULL
            );
            CREATE INDEX ix_inventory_line_unknown_item_id ON inventory_line(unknown_item_id);
            INSERT INTO company(id) VALUES (1);
            INSERT INTO inventory_session(id) VALUES (6);
            INSERT INTO unknown_item(id,session_id,company_id,gl_account_id,each_cost,scanned_upc,proposed_name,status)
                VALUES (3,6,1,2,3.49,'OLDUPC','Old item','pending');
            INSERT INTO inventory_line(id,session_id,unknown_item_id,upc_code,is_missing,quantity)
                VALUES(9,6,3,'OLDUPC',1,1);
        ''')
    result = migrate(path)
    assert any('unknown_item' in action for action in result)
    assert any('inventory_line' in action for action in result)
    with sqlite3.connect(path) as con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('''INSERT INTO unknown_item (session_id, sub_location_id, scanned_upc,
            proposed_name, proposed_gl_account_id, status)
            VALUES (6,2,'11111111','Bonsai Fertilizer Spray',1,'pending')''')
        new_id=con.execute('SELECT last_insert_rowid()').fetchone()[0]
        con.execute('''INSERT INTO inventory_line (session_id,unknown_item_id,scanned_upc,item_name_snapshot,quantity)
            VALUES (6,?,'11111111','Bonsai Fertilizer Spray',1)''',(new_id,))
        assert con.execute('SELECT company_id,each_cost FROM unknown_item WHERE id=3').fetchone() == (1,3.49)
        assert con.execute('SELECT upc_code,is_missing FROM inventory_line WHERE id=9').fetchone() == ('OLDUPC',1)
        assert con.execute('PRAGMA foreign_key_check').fetchall() == []
        for idx in ['ix_unknown_item_session_id','ix_inventory_line_unknown_item_id']:
            assert con.execute('SELECT 1 FROM sqlite_master WHERE name=?',(idx,)).fetchone()
        assert con.execute('PRAGMA table_info(unknown_item)').fetchall()[2][3] == 0
    assert list((tmp_path/'backups').glob('*.db'))
    assert migrate(path) == []


def test_noop_on_current_schema(tmp_path):
    path=tmp_path/'current.db'
    with sqlite3.connect(path) as con:
        con.executescript('''CREATE TABLE unknown_item(id INTEGER PRIMARY KEY, sub_location_id INTEGER NOT NULL, scanned_upc TEXT NOT NULL);
            CREATE TABLE inventory_line(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL);''')
    assert relax_legacy_item_constraints(path) == []
    assert migrate(path) == []
