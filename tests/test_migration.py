import os
import sqlite3
import tempfile

from tado_local.database import ensure_schema_and_migrate


def create_old_db(path: str):
    conn = sqlite3.connect(path)
    # Create minimal zones table without uuid
    conn.execute("""
    CREATE TABLE IF NOT EXISTS zones (
        zone_id INTEGER PRIMARY KEY AUTOINCREMENT,
        tado_zone_id INTEGER,
        tado_home_id INTEGER,
        name TEXT NOT NULL
    )
    """)
    # Insert sample rows
    conn.execute("INSERT INTO zones (tado_zone_id, tado_home_id, name) VALUES (?,?,?)", (101, 1, 'Living'))
    conn.execute("INSERT INTO zones (tado_zone_id, tado_home_id, name) VALUES (?,?,?)", (102, 1, 'Bedroom'))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


def test_migration_adds_uuid_and_sets_user_version(tmp_path):
    db_file = str(tmp_path / "test_migrate.db")
    create_old_db(db_file)

    # Run migrator
    ensure_schema_and_migrate(db_file)

    conn = sqlite3.connect(db_file)
    cur = conn.execute("PRAGMA user_version")
    ver = cur.fetchone()[0]
    assert ver == 2

    # Check uuid column exists and populated
    cur = conn.execute("PRAGMA table_info(zones)")
    cols = [r[1] for r in cur.fetchall()]
    assert 'uuid' in cols

    cur = conn.execute("SELECT zone_id, uuid FROM zones ORDER BY zone_id")
    rows = cur.fetchall()
    assert len(rows) == 2
    for zone_id, uuid_val in rows:
        assert uuid_val is not None and len(uuid_val) > 0

    conn.close()
