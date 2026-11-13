"""Import real club documents (hwp/hwpx/pdf) into the DB: `python -m scripts.import_docs ../test_docu/48th`.

Wipes the DB first. Layout expected: <root>/<N. 행사명>/<files>; folders starting with "0." are skipped.
Evaluation docs (파일명에 '평가') become `lesson` records, the rest `meeting`.
One timeline item per meeting record (title = file name, date = date in the text).
"""
import html
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

from app import db, service
from app.extract import find_date, read_text
from app.to_pdf import to_pdf, soffice
from app.seed import wipe

SKIP_NAMES = ("명단", "조표", "유인물", "복사본")
BIN = Path(sys.executable).parent
DOCS = Path(db.db_path()).parent / "docs"  # original PDFs served by /api/records/{id}/pdf


def nfc(s: str) -> str:  # macOS stores Hangul file names decomposed
    return unicodedata.normalize("NFC", s)


def sibling_pdf(p: Path):
    """The file itself if it is a PDF, else a PDF with the same name next to it (hand-exported or shipped by the club)."""
    key = re.sub(r"\s+", "", nfc(p.stem))
    for q in p.parent.glob("*.pdf"):
        if re.sub(r"\s+", "", nfc(q.stem)) == key:
            return q
    return None


def pick_files(root: Path):
    seen = {}
    rank = {".hwpx": 0, ".hwp": 1, ".pdf": 2}
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in rank or any(k in nfc(p.stem) for k in SKIP_NAMES):
            continue
        if nfc(p.relative_to(root).parts[0]).startswith("0."):
            continue
        key = (p.parent, re.sub(r"\s+", "", p.stem))
        if key not in seen or rank[p.suffix.lower()] < rank[seen[key].suffix.lower()]:
            seen[key] = p
    return sorted(seen.values())


def main(root: Path, append: bool = False, cohort: str | None = None, event: str | None = None):
    """Default: wipe the DB and import the club archive (47대/48대). --append --cohort "이름:연도" [--event 행사명] adds
    another source (e.g. public minutes) to the existing DB; every file of that folder goes to that cohort."""
    conn = db.connect()
    cohorts = {}
    if append:
        name, _, year = (cohort or root.name).partition(":")
        row = conn.execute("select id from cohorts where name=?", (name,)).fetchone()
        cohorts["*"] = row[0] if row else conn.execute("insert into cohorts(name,year) values (?,?)", (name, int(year or 0))).lastrowid
        conn.commit()
    else:
        wipe(conn)
        shutil.rmtree(DOCS, ignore_errors=True)
        for name, year in (("47대", 2024), ("48대", 2025)):
            cohorts[name] = conn.execute("insert into cohorts(name,year) values (?,?)", (name, year)).lastrowid
        conn.commit()
    fixes = {}
    if (root / "date_fixes.json").exists():  # {"문서 제목": "YYYY-MM-DD"} for typos in the originals; stays next to the (untracked) documents
        fixes = {nfc(k): date.fromisoformat(v) for k, v in json.loads((root / "date_fixes.json").read_text(encoding="utf-8")).items()}
    docs = []
    for p in pick_files(root):
        rel = p.relative_to(root)
        stem = nfc(p.stem).strip()
        try:
            text = read_text(p)
        except Exception as e:  # noqa: BLE001 - report and move on
            print("SKIP(read)", rel, e); continue
        docs.append({"rel": rel, "stem": stem, "text": text, "date": fixes.get(stem) or find_date(text, stem), "pdf": sibling_pdf(p), "src": p})
    # undated docs borrow the latest date seen in the same event folder
    latest = {}
    for d in docs:
        if d["date"]:
            k = d["rel"].parts[0]
            latest[k] = max(latest.get(k, d["date"]), d["date"])
    n = skipped = with_pdf = converted = 0
    if not soffice():
        print("LibreOffice not found: hwp/hwpx stay text-only")
    for d in docs:
        rel, stem, text = d["rel"], d["stem"], d["text"]
        day = d["date"] or latest.get(rel.parts[0])
        if not text or not day:
            print("SKIP(no text/date)", rel); skipped += 1; continue
        ev = event or (root.name if append else re.sub(r"^\d+\.\s*|\(.*?\)", "", nfc(rel.parts[0])).strip())
        old = "2024" in stem or any("2024" in x for x in rel.parts[:-1])
        type_ = "lesson" if "평가" in stem else "meeting"
        rec, _, _ = service.add_record(conn, cohorts["*"] if append else cohorts["47대" if old else "48대"], type_, stem, text, ev, day,
                                       extractor=lambda t, b, o, e: [{"title": t, "done_on": o.isoformat(), "category": e, "note": ""}])
        DOCS.mkdir(parents=True, exist_ok=True)
        dest = DOCS / f"{rec['id']}.pdf"
        if d["pdf"]:
            shutil.copyfile(d["pdf"], dest)
        elif d["src"].suffix.lower() in (".hwp", ".hwpx", ".docx") and to_pdf(d["src"], dest):
            converted += 1
        if dest.exists():
            conn.execute("update records set pdf_path=? where id=?", (str(dest), rec["id"]))
            conn.commit()
            with_pdf += 1
        n += 1
    print(f"imported {n} records ({with_pdf} with original PDF, {converted} converted to PDF), skipped {skipped}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--cohort", help='"이름:연도" (with --append)')
    ap.add_argument("--event", help="event name for every file (with --append; default: folder name)")
    a = ap.parse_args()
    main(a.root, a.append, a.cohort, a.event)
