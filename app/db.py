import os
import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists cohorts (
  id integer primary key, name text not null, year integer not null, starts_on text, ends_on text);
create table if not exists records (
  id integer primary key, cohort_id integer not null references cohorts(id),
  type text not null check (type in ('meeting','event','lesson','decision')),
  title text not null, body text not null, event_name text not null default '', occurred_on text not null,
  pdf_path text not null default '');
create table if not exists timeline_items (
  id integer primary key, record_id integer not null references records(id),
  cohort_id integer not null references cohorts(id),
  title text not null, done_on text not null, category text not null default '', note text not null default '');
create table if not exists interview_turns (
  id integer primary key, cohort_id integer not null references cohorts(id),
  event_name text not null, question text not null, answer text not null, created_at text not null);
create table if not exists decisions (
  id integer primary key, cohort_id integer not null references cohorts(id),
  timeline_item_id integer not null references timeline_items(id),
  body text not null, reason text not null, decided_on text not null);
create index if not exists idx_items_done on timeline_items(done_on);
create index if not exists idx_records_event on records(event_name, type);
create virtual table if not exists records_fts using fts5(title, body, tokenize='trigram');
create table if not exists chunks (
  id integer primary key, record_id integer not null references records(id), idx integer not null, text text not null);
create index if not exists idx_chunks_record on chunks(record_id);
create table if not exists briefings (key text primary key, body text not null, created_at text not null);
create virtual table if not exists chunks_fts using fts5(text, tokenize='trigram');
"""


def db_path() -> str:
    return os.environ.get("DB_PATH", "data/app.db")


def connect() -> sqlite3.Connection:
    p = db_path()
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.executescript(SCHEMA)
    if not any(r[1] == 'pdf_path' for r in conn.execute('pragma table_info(records)')):  # older DB files
        conn.execute("alter table records add column pdf_path text not null default ''")
    backfill_chunks(conn)
    return conn


def add_chunks(conn: sqlite3.Connection, record_id: int, body: str) -> None:
    from .chunks import chunk_text
    for i, text in enumerate(chunk_text(body)):
        cid = conn.execute("insert into chunks(record_id,idx,text) values (?,?,?)", (record_id, i, text)).lastrowid
        conn.execute("insert into chunks_fts(rowid,text) values (?,?)", (cid, text))


def backfill_chunks(conn: sqlite3.Connection) -> None:
    """DB files created before passages existed: build them once."""
    if conn.execute("select 1 from chunks limit 1").fetchone() or not conn.execute("select 1 from records limit 1").fetchone():
        return
    for r in conn.execute("select id, body from records").fetchall():
        add_chunks(conn, r["id"], r["body"])
    conn.commit()
