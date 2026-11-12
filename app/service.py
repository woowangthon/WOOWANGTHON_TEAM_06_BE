"""All DB logic. Routes stay thin; AI calls come from senior_ai."""
from datetime import date, timedelta

from senior_ai import LLMError, extract_timeline, keywords

from .db import add_chunks


class NotFound(Exception):
    pass


def _one(conn, sql, *a):
    r = conn.execute(sql, a).fetchone()
    if r is None:
        raise NotFound()
    return dict(r)


def require_cohort(conn, cohort_id: int):
    _one(conn, "select id from cohorts where id=?", cohort_id)


def add_record(conn, cohort_id, type_, title, body, event_name, occurred_on: date, extractor=extract_timeline):
    """Insert a record; for meeting/event also extract timeline items. Returns (record, items, extraction_ok)."""
    require_cohort(conn, cohort_id)
    cur = conn.execute(
        "insert into records(cohort_id,type,title,body,event_name,occurred_on) values (?,?,?,?,?,?)",
        (cohort_id, type_, title, body, event_name, occurred_on.isoformat()))
    rid = cur.lastrowid
    conn.execute("insert into records_fts(rowid,title,body) values (?,?,?)", (rid, title, body))
    add_chunks(conn, rid, body)
    items, ok = [], True
    if type_ in ("meeting", "event"):
        try:
            for it in extractor(title, body, occurred_on, event_name):
                c = conn.execute(
                    "insert into timeline_items(record_id,cohort_id,title,done_on,category,note) values (?,?,?,?,?,?)",
                    (rid, cohort_id, it["title"], it["done_on"], it.get("category", ""), it.get("note", "")))
                items.append({**it, "id": c.lastrowid})
        except LLMError:
            ok = False
    conn.commit()
    return get_record(conn, rid), items, ok


def get_record(conn, rid):
    return _one(conn, "select * from records where id=?", rid)


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def _card(conn, r):
    c = dict(r)
    c["lessons"] = [dict(x) for x in conn.execute(
        "select id, title, body, occurred_on from records where type='lesson' and cohort_id=? and event_name=? and event_name!=''",
        (r["cohort_id"], r["event_name"]))]
    c["decisions"] = [dict(x) for x in conn.execute(
        "select id, body, reason, decided_on from decisions where timeline_item_id=?", (r["id"],))]
    return c


_CARD_SQL = """select t.id, t.title, t.done_on, t.category, t.note, t.record_id, t.cohort_id,
  r.event_name, r.title as record_title, c.name as cohort_name
  from timeline_items t join records r on r.id=t.record_id join cohorts c on c.id=t.cohort_id"""


def last_year(conn, today: date, window_days: int):
    center = _shift_year(today, -1)
    lo, hi = center - timedelta(days=window_days), center + timedelta(days=window_days)
    rows = conn.execute(_CARD_SQL + " where t.done_on between ? and ? order by t.done_on", (lo.isoformat(), hi.isoformat())).fetchall()
    notice = ""
    if not rows:  # nothing in the window: show the nearest records instead
        rows = conn.execute(_CARD_SQL + " order by abs(julianday(t.done_on) - julianday(?)) limit 3", (center.isoformat(),)).fetchall()
        notice = "이 시기에는 기록이 없다. 가장 가까운 시기의 기록을 보여준다." if rows else "등록된 기록이 없다."
    return {"center": center.isoformat(), "window_days": window_days, "notice": notice, "cards": [_card(conn, r) for r in rows]}


def search(conn, question: str, limit: int = 10, per_record: int = 3):
    """Passage retrieval. A passage scores the length of every keyword it contains; a keyword that appears in the
    record's title or event name lifts all of that record's passages (e.g. '정기총회' finds the whole 총회 documents)."""
    kws = keywords(question)
    if not kws:
        return []
    cand: set[int] = set()
    for k in kws:
        if len(k) >= 3:
            cand |= {r[0] for r in conn.execute("select rowid from chunks_fts where chunks_fts match ?", ('"' + k.replace('"', "") + '"',))}
        else:
            cand |= {r[0] for r in conn.execute("select id from chunks where text like ?", (f"%{k}%",))}
        cand |= {r[0] for r in conn.execute(
            "select c.id from chunks c join records r on r.id=c.record_id where r.title like ? or r.event_name like ?", (f"%{k}%", f"%{k}%"))}
    rows = []
    for cid in cand:
        c = conn.execute("select c.id, c.record_id, c.text, r.title, r.event_name, r.occurred_on from chunks c join records r on r.id=c.record_id where c.id=?", (cid,)).fetchone()
        head = c["title"] + " " + c["event_name"]
        score = sum(len(k) * ((k in c["text"]) + (k in head)) for k in kws)
        rows.append((score, c))
    rows.sort(key=lambda x: (-x[0], x[1]["record_id"], x[1]["id"]))
    out, used = [], {}
    for score, c in rows:
        if score <= 0 or used.get(c["record_id"], 0) >= per_record:
            continue
        used[c["record_id"]] = used.get(c["record_id"], 0) + 1
        out.append({"id": c["record_id"], "title": c["title"], "body": c["text"], "occurred_on": c["occurred_on"]})
        if len(out) >= limit:
            break
    return out


def add_lesson(conn, cohort_id, event_name, question, answer, today: date):
    require_cohort(conn, cohort_id)
    conn.execute("insert into interview_turns(cohort_id,event_name,question,answer,created_at) values (?,?,?,?,?)",
                 (cohort_id, event_name, question, answer, today.isoformat()))
    last = conn.execute("select max(occurred_on) from records where cohort_id=? and event_name=?", (cohort_id, event_name)).fetchone()[0]
    rec, _, _ = add_record(conn, cohort_id, "lesson", f"{event_name} 교훈", f"{question} {answer}", event_name,
                           date.fromisoformat(last) if last else today)
    return rec


def asked_questions(conn, cohort_id, event_name):
    return [r[0] for r in conn.execute("select question from interview_turns where cohort_id=? and event_name=? order by id", (cohort_id, event_name))]


def add_decision(conn, timeline_item_id, body, reason, decided_on: date):
    item = _one(conn, "select t.*, r.event_name from timeline_items t join records r on r.id=t.record_id where t.id=?", timeline_item_id)
    cur = conn.execute("insert into decisions(cohort_id,timeline_item_id,body,reason,decided_on) values (?,?,?,?,?)",
                       (item["cohort_id"], timeline_item_id, body, reason, decided_on.isoformat()))
    add_record(conn, item["cohort_id"], "decision", f"{item['title']} 결정", f"{body} (이유: {reason})", item["event_name"], decided_on)
    return _one(conn, "select * from decisions where id=?", cur.lastrowid)


def _drop_chunks(conn, rid):
    for (cid,) in conn.execute("select id from chunks where record_id=?", (rid,)).fetchall():
        conn.execute("delete from chunks_fts where rowid=?", (cid,))
    conn.execute("delete from chunks where record_id=?", (rid,))


def update_record(conn, rid, title=None, event_name=None, occurred_on: date | None = None, body=None):
    """Edit a record. A new date shifts its timeline items by the same number of days; a new body rebuilds search passages."""
    old = get_record(conn, rid)
    new = {"title": title if title is not None else old["title"],
           "event_name": event_name if event_name is not None else old["event_name"],
           "body": body if body is not None else old["body"],
           "occurred_on": occurred_on.isoformat() if occurred_on else old["occurred_on"]}
    conn.execute("update records set title=?, event_name=?, body=?, occurred_on=? where id=?",
                 (new["title"], new["event_name"], new["body"], new["occurred_on"], rid))
    if occurred_on:
        delta = (occurred_on - date.fromisoformat(old["occurred_on"])).days
        if delta:
            for it in conn.execute("select id, done_on from timeline_items where record_id=?", (rid,)).fetchall():
                conn.execute("update timeline_items set done_on=? where id=?",
                             ((date.fromisoformat(it["done_on"]) + timedelta(days=delta)).isoformat(), it["id"]))
    if new["title"] != old["title"]:  # items that carry the record title follow it
        conn.execute("update timeline_items set title=? where record_id=? and title=?", (new["title"], rid, old["title"]))
    if new["title"] != old["title"] or new["body"] != old["body"]:
        conn.execute("delete from records_fts where rowid=?", (rid,))
        conn.execute("insert into records_fts(rowid,title,body) values (?,?,?)", (rid, new["title"], new["body"]))
    if new["body"] != old["body"]:
        _drop_chunks(conn, rid)
        add_chunks(conn, rid, new["body"])
    conn.commit()
    return get_record(conn, rid)


def delete_record(conn, rid):
    """Remove a record with its passages, timeline items, the decisions made on those items, and the stored PDF."""
    rec = get_record(conn, rid)
    ids = [r[0] for r in conn.execute("select id from timeline_items where record_id=?", (rid,))]
    for i in ids:
        conn.execute("delete from decisions where timeline_item_id=?", (i,))
    conn.execute("delete from timeline_items where record_id=?", (rid,))
    _drop_chunks(conn, rid)
    conn.execute("delete from records_fts where rowid=?", (rid,))
    conn.execute("delete from records where id=?", (rid,))
    conn.commit()
    return rec
