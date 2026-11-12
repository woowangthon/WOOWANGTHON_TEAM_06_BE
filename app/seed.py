"""Reset the DB to the demo seed: `python -m app.seed`. Timeline items come from the fake extractor (no key needed)."""
import json
import sqlite3
from datetime import date
from pathlib import Path

from senior_ai import fake

from . import db, service

SEED = Path(__file__).resolve().parent.parent / "seed" / "records.json"


def wipe(conn: sqlite3.Connection) -> None:
    for t in ("decisions", "interview_turns", "timeline_items", "chunks", "records", "cohorts"):
        conn.execute(f"delete from {t}")
    conn.execute("delete from records_fts")
    conn.execute("delete from chunks_fts")
    conn.commit()


def run(conn: sqlite3.Connection | None = None) -> None:
    conn = conn or db.connect()
    wipe(conn)
    data = json.loads(SEED.read_text(encoding="utf-8"))
    ids = {}
    for c in data["cohorts"]:
        cur = conn.execute("insert into cohorts(name,year,starts_on,ends_on) values (?,?,?,?)", (c["name"], c["year"], c["starts_on"], c["ends_on"]))
        ids[c["name"]] = cur.lastrowid
    for r in data["records"]:
        service.add_record(conn, ids[r["cohort"]], r["type"], r["title"], r["body"], r["event_name"],
                           date.fromisoformat(r["occurred_on"]), extractor=fake.extract_timeline)
    print(f"seeded {len(data['cohorts'])} cohorts, {len(data['records'])} records")


if __name__ == "__main__":
    run()
