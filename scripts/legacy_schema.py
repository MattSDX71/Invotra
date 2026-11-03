"""Non-destructive migration of the v7 location-based Invotra SQLite schema.

The old tables/columns remain in place. New columns, parent/subunit mappings,
and inventory snapshots are added and populated. No DELETE/DROP operations.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def migrate(database_path: str | Path) -> list[str]:
    path = Path(database_path)
    if not path.is_file():
        return []
    actions = []
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA busy_timeout=30000')
        tables = {r['name'] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def cols(table):
            return {r['name'] for r in con.execute(f'PRAGMA table_info("{table}")')} if table in tables else set()
        def add(table, name, ddl):
            if table in tables and name not in cols(table):
                con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}')
                actions.append(f'{table}.{name}')
        # This migration only applies to a known legacy layout, or to an already
        # partly migrated database. It will never guess a location from its name.
        legacy = 'location' in tables and 'inventory_session' in tables and 'location_id' in cols('inventory_session')
        if not legacy:
            add('user', 'phone', 'VARCHAR(50)')
            con.commit()
            return actions
        required = {'company','location','inventory_session','inventory_line','item_master','gl_account','user'}
        if required - tables:
            raise RuntimeError('Legacy database missing required tables: ' + ', '.join(sorted(required - tables)))
        # Tables created by db.create_all() before this migration.
        needed_new = {'parent_location','sub_location','user_sub_location_access'}
        if needed_new - tables:
            raise RuntimeError('Missing new hierarchy tables; run db.create_all() before legacy migration')
        con.execute('BEGIN IMMEDIATE')
        add('user','phone','VARCHAR(50)')
        if 'phone_number' in cols('user'):
            con.execute('UPDATE "user" SET phone=phone_number WHERE phone IS NULL')
        add('gl_account','description','VARCHAR(120)')
        if 'name' in cols('gl_account'):
            con.execute('UPDATE gl_account SET description=name WHERE description IS NULL')
        add('item_master','each_upc','VARCHAR(100)')
        add('item_master','case_upc','VARCHAR(100)')
        add('item_master','updated_at','DATETIME')
        im = cols('item_master')
        if 'each_upc_code' in im:
            con.execute('UPDATE item_master SET each_upc=each_upc_code WHERE each_upc IS NULL')
        if 'case_upc_code' in im:
            con.execute('UPDATE item_master SET case_upc=case_upc_code WHERE case_upc IS NULL')
        con.execute('UPDATE item_master SET updated_at=COALESCE(updated_at,created_at,CURRENT_TIMESTAMP) WHERE updated_at IS NULL')
        add('inventory_session','sub_location_id','INTEGER')
        add('inventory_session','approved_by_id','INTEGER')
        add('inventory_session','total_quantity','INTEGER DEFAULT 0')
        add('inventory_session','total_value','NUMERIC(12,2) DEFAULT 0')
        if 'approved_by_user_id' in cols('inventory_session'):
            con.execute('UPDATE inventory_session SET approved_by_id=approved_by_user_id WHERE approved_by_id IS NULL')
        for name, ddl in [
            ('unknown_item_id','INTEGER'),('scanned_upc','VARCHAR(100)'),('scan_type','VARCHAR(20)'),
            ('item_name_snapshot','VARCHAR(255)'),('gl_label_snapshot','VARCHAR(160)'),
            ('unit_cost_snapshot','NUMERIC(10,2)'),('extended_value','NUMERIC(12,2)')]:
            add('inventory_line',name,ddl)
        il = cols('inventory_line')
        if 'upc_code' in il:
            con.execute("UPDATE inventory_line SET scanned_upc=COALESCE(NULLIF(upc_code,''),'LEGACY-LINE-'||id) WHERE scanned_upc IS NULL")
        else:
            con.execute("UPDATE inventory_line SET scanned_upc='LEGACY-LINE-'||id WHERE scanned_upc IS NULL")
        if 'unit_type' in il:
            con.execute("UPDATE inventory_line SET scan_type=COALESCE(NULLIF(unit_type,''),'each') WHERE scan_type IS NULL")
        else:
            con.execute("UPDATE inventory_line SET scan_type='each' WHERE scan_type IS NULL")
        if 'item_name' in il:
            con.execute("UPDATE inventory_line SET item_name_snapshot=COALESCE(NULLIF(item_name,''),'Legacy item '||id) WHERE item_name_snapshot IS NULL")
        else:
            con.execute("UPDATE inventory_line SET item_name_snapshot='Legacy item '||id WHERE item_name_snapshot IS NULL")
        if 'unit_cost' in il:
            con.execute('UPDATE inventory_line SET unit_cost_snapshot=COALESCE(unit_cost,0) WHERE unit_cost_snapshot IS NULL')
        else:
            con.execute('UPDATE inventory_line SET unit_cost_snapshot=0 WHERE unit_cost_snapshot IS NULL')
        con.execute('UPDATE inventory_line SET extended_value=ROUND(COALESCE(unit_cost_snapshot,0)*COALESCE(quantity,0),2) WHERE extended_value IS NULL')
        if 'gl_account_id' in il:
            con.execute("""UPDATE inventory_line SET gl_label_snapshot = COALESCE(
                (SELECT COALESCE(g.description,g.name,g.code)||' - '||g.code FROM gl_account g WHERE g.id=inventory_line.gl_account_id),
                'Legacy / Unassigned') WHERE gl_label_snapshot IS NULL""" if 'name' in cols('gl_account') else """UPDATE inventory_line SET gl_label_snapshot = COALESCE(
                (SELECT COALESCE(g.description,g.code)||' - '||g.code FROM gl_account g WHERE g.id=inventory_line.gl_account_id),
                'Legacy / Unassigned') WHERE gl_label_snapshot IS NULL""")
        # Preserve a stable, inspectable mapping from old location IDs to new subunit IDs.
        con.execute('CREATE TABLE IF NOT EXISTS invotra_legacy_location_map (legacy_location_id INTEGER PRIMARY KEY, sub_location_id INTEGER NOT NULL)')
        locs = con.execute('SELECT * FROM location ORDER BY id').fetchall()
        loc_by_id = {r['id']:r for r in locs}
        parent_map = {}
        def ensure_parent(old):
            root_id = old['parent_id'] if old['parent_id'] and old['parent_id'] in loc_by_id else old['id']
            if root_id in parent_map:
                return parent_map[root_id]
            root = loc_by_id[root_id]
            # Reuse new parent only by exact company ID and name.
            row = con.execute('SELECT id FROM parent_location WHERE company_id=? AND name=? ORDER BY id LIMIT 1',(root['company_id'],root['name'])).fetchone()
            if row: pid=row['id']
            else:
                cur=con.execute('INSERT INTO parent_location(company_id,name,active) VALUES(?,?,?)',
                    (root['company_id'],root['name'], root['active'] if 'active' in root.keys() else 1))
                pid=cur.lastrowid
            parent_map[root_id]=pid
            return pid
        for old in locs:
            existing=con.execute('SELECT sub_location_id FROM invotra_legacy_location_map WHERE legacy_location_id=?',(old['id'],)).fetchone()
            if existing:
                continue
            pid=ensure_parent(old)
            is_parent=not old['parent_id'] or old['parent_id'] not in loc_by_id
            sub_name='General' if is_parent else old['name']
            row=con.execute('SELECT id FROM sub_location WHERE parent_id=? AND name=? ORDER BY id LIMIT 1',(pid,sub_name)).fetchone()
            if row: sid=row['id']
            else:
                cur=con.execute('INSERT INTO sub_location(parent_id,name,active) VALUES(?,?,?)',
                    (pid,sub_name, old['active'] if 'active' in old.keys() else 1))
                sid=cur.lastrowid
            con.execute('INSERT INTO invotra_legacy_location_map(legacy_location_id,sub_location_id) VALUES (?,?)',(old['id'],sid))
        con.execute('''UPDATE inventory_session SET sub_location_id=(SELECT sub_location_id FROM invotra_legacy_location_map m WHERE m.legacy_location_id=inventory_session.location_id) WHERE sub_location_id IS NULL''')
        # Do not silently orphan sessions if an old location was deleted.
        missing=con.execute('SELECT COUNT(*) FROM inventory_session WHERE sub_location_id IS NULL').fetchone()[0]
        if missing:
            raise RuntimeError(f'{missing} historic sessions cannot be mapped to a subunit; migration stopped before commit')
        if 'user_location_access' in tables:
            for a in con.execute('SELECT user_id, location_id FROM user_location_access').fetchall():
                mapped=con.execute('SELECT sub_location_id FROM invotra_legacy_location_map WHERE legacy_location_id=?',(a['location_id'],)).fetchone()
                if mapped:
                    exists=con.execute('SELECT 1 FROM user_sub_location_access WHERE user_id=? AND sub_location_id=?',(a['user_id'],mapped['sub_location_id'])).fetchone()
                    if not exists:
                        con.execute('INSERT INTO user_sub_location_access(user_id,sub_location_id) VALUES(?,?)',(a['user_id'],mapped['sub_location_id']))
                old=loc_by_id.get(a['location_id'])
                if old and (not old['parent_id'] or old['parent_id'] not in loc_by_id):
                    for child in (l for l in locs if l['parent_id']==old['id']):
                        m=con.execute('SELECT sub_location_id FROM invotra_legacy_location_map WHERE legacy_location_id=?',(child['id'],)).fetchone()
                        if m and not con.execute('SELECT 1 FROM user_sub_location_access WHERE user_id=? AND sub_location_id=?',(a['user_id'],m['sub_location_id'])).fetchone():
                            con.execute('INSERT INTO user_sub_location_access(user_id,sub_location_id) VALUES(?,?)',(a['user_id'],m['sub_location_id']))
        # Missing-item queue from legacy v7.
        if 'unknown_item' in tables:
            for name, ddl in [('sub_location_id','INTEGER'),('scanned_upc','VARCHAR(100)'),('proposed_gl_account_id','INTEGER'),('reviewed_by_id','INTEGER'),('converted_item_master_id','INTEGER')]:
                add('unknown_item',name,ddl)
            ucols=cols('unknown_item')
            if 'gl_account_id' in ucols:
                con.execute('UPDATE unknown_item SET proposed_gl_account_id=gl_account_id WHERE proposed_gl_account_id IS NULL')
            if 'each_upc_code' in ucols or 'case_upc_code' in ucols:
                each='each_upc_code' if 'each_upc_code' in ucols else "NULL"
                case='case_upc_code' if 'case_upc_code' in ucols else "NULL"
                con.execute(f"UPDATE unknown_item SET scanned_upc=COALESCE(NULLIF({each},''),NULLIF({case},''),'LEGACY-UNKNOWN-'||id) WHERE scanned_upc IS NULL")
            else:
                con.execute("UPDATE unknown_item SET scanned_upc='LEGACY-UNKNOWN-'||id WHERE scanned_upc IS NULL")
            con.execute('UPDATE unknown_item SET sub_location_id=(SELECT s.sub_location_id FROM inventory_session s WHERE s.id=unknown_item.session_id) WHERE sub_location_id IS NULL')
        con.execute('''UPDATE inventory_session SET total_quantity=(SELECT COALESCE(SUM(quantity),0) FROM inventory_line l WHERE l.session_id=inventory_session.id), total_value=(SELECT COALESCE(SUM(extended_value),0) FROM inventory_line l WHERE l.session_id=inventory_session.id)''')
        con.commit()
        actions.append(f'legacy hierarchy mapped: {len(locs)} locations')
        return actions
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
