"""Import real club documents (hwp/hwpx/pdf) into the DB: `python -m scripts.import_docs ../test_docu/48th`.

Wipes the DB first. Layout expected: <root>/<N. 행사명>/<files>; folders starting with "0." are skipped.
Evaluation docs (파일명에 '평가') become `lesson` records, the rest `meeting`.
One timeline item per meeting record (title = file name, date = date in the text).
"""
import html
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

from app import db, service
from app.seed import wipe

SKIP_NAMES = ("명단", "조표", "유인물", "복사본")
# "2025년 4월 18일", "2025 5월 19일", "2025 7.24", "2025.3.24"
DATE = re.compile(r"(\d{4})\s*[년.\-]?\s*(\d{1,2})\s*[월.\-]\s*(\d{1,2})")
# no year in the text: fall back to the year in the file name and a month/day like "3월 24일"
MD = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
BIN = Path(sys.executable).parent
DOCS = Path(db.db_path()).parent / "docs"  # original PDFs served by /api/records/{id}/pdf


def nfc(s: str) -> str:  # macOS stores Hangul file names decomposed
    return unicodedata.normalize("NFC", s)


def read_hwpx(p: Path) -> str:
    out = []
    with zipfile.ZipFile(p) as z:
        for n in sorted(z.namelist()):
            if n.startswith("Contents/section") and n.endswith(".xml"):
                for para in re.findall(r"<hp:p[ >].*?</hp:p>", z.read(n).decode("utf-8", "ignore"), flags=re.S):
                    t = html.unescape(re.sub(r"<[^>]+>", "", "".join(re.findall(r"<hp:t>(.*?)</hp:t>", para, flags=re.S)))).strip()
                    if t:
                        out.append(t)
    return "\n".join(out)


def run_tool(name: str, p: Path) -> str:
    exe = BIN / name if (BIN / name).exists() else shutil.which(name)
    return subprocess.run([str(exe), str(p)], capture_output=True, text=True, check=True).stdout


def read_text(p: Path) -> str:
    ext = p.suffix.lower()
    t = read_hwpx(p) if ext == ".hwpx" else run_tool("hwp5txt", p) if ext == ".hwp" else run_tool("markitdown", p)
    return re.sub(r"<표>|[ \t]+\n", "", t).strip()


def find_date(text: str, name: str):
    for src in (text[:600], name, text):
        for m in DATE.finditer(src):
            try:
                return date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                continue
    year = re.search(r"(20\d{2})", name)
    m = MD.search(text) or MD.search(name)
    if year and m:
        try:
            return date(int(year[1]), int(m[1]), int(m[2]))
        except ValueError:
            pass
    return None


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


def main(root: Path):
    conn = db.connect()
    wipe(conn)
    shutil.rmtree(DOCS, ignore_errors=True)
    cohorts = {}
    for name, year in (("47대", 2024), ("48대", 2025)):
        cohorts[name] = conn.execute("insert into cohorts(name,year) values (?,?)", (name, year)).lastrowid
    conn.commit()
    docs = []
    for p in pick_files(root):
        rel = p.relative_to(root)
        stem = nfc(p.stem).strip()
        try:
            text = read_text(p)
        except Exception as e:  # noqa: BLE001 - report and move on
            print("SKIP(read)", rel, e); continue
        docs.append({"rel": rel, "stem": stem, "text": text, "date": find_date(text, stem), "pdf": sibling_pdf(p)})
    # undated docs borrow the latest date seen in the same event folder
    latest = {}
    for d in docs:
        if d["date"]:
            k = d["rel"].parts[0]
            latest[k] = max(latest.get(k, d["date"]), d["date"])
    n = skipped = with_pdf = 0
    for d in docs:
        rel, stem, text = d["rel"], d["stem"], d["text"]
        day = d["date"] or latest.get(rel.parts[0])
        if not text or not day:
            print("SKIP(no text/date)", rel); skipped += 1; continue
        event = re.sub(r"^\d+\.\s*|\(.*?\)", "", nfc(rel.parts[0])).strip()
        old = "2024" in stem or any("2024" in x for x in rel.parts[:-1])
        type_ = "lesson" if "평가" in stem else "meeting"
        rec, _, _ = service.add_record(conn, cohorts["47대" if old else "48대"], type_, stem, text, event, day,
                                       extractor=lambda t, b, o, e: [{"title": t, "done_on": o.isoformat(), "category": e, "note": ""}])
        if d["pdf"]:
            DOCS.mkdir(parents=True, exist_ok=True)
            dest = DOCS / f"{rec['id']}.pdf"
            shutil.copyfile(d["pdf"], dest)
            conn.execute("update records set pdf_path=? where id=?", (str(dest), rec["id"]))
            conn.commit()
            with_pdf += 1
        n += 1
    print(f"imported {n} records ({with_pdf} with original PDF), skipped {skipped}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
