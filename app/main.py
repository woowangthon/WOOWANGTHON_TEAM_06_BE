from datetime import date
from pathlib import Path

import os
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from senior_ai import LLMError, answer_question, next_interview_question

from . import service
from .db import connect, db_path
from .extract import SUPPORTED, ExtractError, find_date, read_text
from .to_pdf import to_pdf

@asynccontextmanager
async def lifespan(_):
    if os.environ.get("SEED_DEMO") == "1":  # first boot on a fresh disk: load the synthetic demo data
        conn = connect()
        if not conn.execute("select 1 from cohorts limit 1").fetchone():
            from .seed import run
            run(conn)
        conn.close()
    yield


app = FastAPI(title="졸업하지 않는 선배", lifespan=lifespan)


@app.middleware("http")
async def access_key(request, call_next):
    """If APP_ACCESS_KEY is set, every /api call except health must carry it in X-Access-Key."""
    key = os.environ.get("APP_ACCESS_KEY", "")
    if key and request.method != "OPTIONS" and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        if not secrets.compare_digest(request.headers.get("x-access-key", ""), key):
            return JSONResponse({"detail": "접근 키가 필요합니다"}, status_code=401)
    return await call_next(request)


# added after the key check so CORS wraps it and 401s still reach the browser
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",")],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"ok": True}


@app.exception_handler(RequestValidationError)
async def _bad_input(_, exc):
    return JSONResponse({"detail": "입력값이 올바르지 않습니다", "errors": [e["msg"] for e in exc.errors()]}, status_code=400)


@app.exception_handler(service.NotFound)
async def _missing(_, exc):
    return JSONResponse({"detail": "찾을 수 없는 자료입니다"}, status_code=404)


@app.exception_handler(LLMError)
async def _ai_down(_, exc):
    return JSONResponse({"detail": f"AI 호출에 실패했습니다: {exc}"}, status_code=502)


def pub(r: dict) -> dict:
    """Hide the server-side file path; expose only whether an original PDF exists."""
    r = dict(r)
    r["has_pdf"] = bool(r.pop("pdf_path", ""))
    return r


def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


class CohortIn(BaseModel):
    name: str = Field(min_length=1)
    year: int
    starts_on: date | None = None
    ends_on: date | None = None


class RecordIn(BaseModel):
    cohort_id: int
    type: str = Field(pattern="^(meeting|event|lesson|decision)$")
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    event_name: str = ""
    occurred_on: date


class RecordPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    event_name: str | None = None
    occurred_on: date | None = None
    body: str | None = Field(default=None, min_length=1)


class AskIn(BaseModel):
    question: str = Field(min_length=1)


class InterviewNextIn(BaseModel):
    cohort_id: int
    event_name: str = Field(min_length=1)


class InterviewAnswerIn(InterviewNextIn):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class DecisionIn(BaseModel):
    timeline_item_id: int
    body: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decided_on: date | None = None


@app.get("/api/cohorts")
def list_cohorts(conn=Depends(db)):
    return [dict(r) for r in conn.execute("select * from cohorts order by year")]


@app.post("/api/cohorts", status_code=201)
def create_cohort(b: CohortIn, conn=Depends(db)):
    cur = conn.execute("insert into cohorts(name,year,starts_on,ends_on) values (?,?,?,?)",
                       (b.name, b.year, b.starts_on and b.starts_on.isoformat(), b.ends_on and b.ends_on.isoformat()))
    conn.commit()
    return dict(conn.execute("select * from cohorts where id=?", (cur.lastrowid,)).fetchone())


@app.post("/api/records", status_code=201)
def create_record(b: RecordIn, conn=Depends(db)):
    rec, items, ok = service.add_record(conn, b.cohort_id, b.type, b.title, b.body, b.event_name, b.occurred_on)
    return {"record": pub(rec), "timeline_items": items, "extraction": "ok" if ok else "failed"}


MAX_UPLOAD = 20 * 1024 * 1024


class Bad(Exception):
    pass


@app.exception_handler(Bad)
async def _bad(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.post("/api/records/upload", status_code=201)
def upload_record(file: UploadFile = File(...), cohort_id: int = Form(...), type: str = Form("meeting"),
                  event_name: str = Form(""), occurred_on: date | None = Form(None), title: str = Form(""),
                  conn=Depends(db)):
    """Register a document from a file. The date comes from the form, else from the document text."""
    name = file.filename or "문서"
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise Bad(f"지원하지 않는 형식입니다. {', '.join(sorted(SUPPORTED))} 파일을 올려 주세요")
    if type not in ("meeting", "event", "lesson"):
        raise Bad("종류는 meeting, event, lesson 중 하나여야 합니다")
    service.require_cohort(conn, cohort_id)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{ext}"
        size = 0
        with path.open("wb") as f:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise Bad("파일이 20MB를 넘습니다")
                f.write(chunk)
        try:
            text = read_text(path)
        except ExtractError as e:
            raise Bad(str(e))
        if not text:
            raise Bad("문서에서 글자를 찾지 못했습니다. 스캔본이면 글자가 있는 파일로 올려 주세요")
        day = occurred_on or find_date(text, Path(name).stem)
        if not day:
            raise Bad("문서에서 날짜를 찾지 못했습니다. 날짜를 직접 입력해 주세요")
        rec, items, ok = service.add_record(conn, cohort_id, type, title.strip() or Path(name).stem, text, event_name, day)
        dest = Path(db_path()).parent / "docs" / f"{rec['id']}.pdf"  # keep the original (or a PDF rendering of it) for the overlay
        dest.parent.mkdir(parents=True, exist_ok=True)
        if ext == ".pdf":
            shutil.copyfile(path, dest)
        else:
            to_pdf(path, dest)
        if dest.exists():
            conn.execute("update records set pdf_path=? where id=?", (str(dest), rec["id"]))
            conn.commit()
            rec = service.get_record(conn, rec["id"])
    return {"record": pub(rec), "timeline_items": items, "extraction": "ok" if ok else "failed"}


@app.get("/api/records")
def list_records(cohort_id: int | None = None, type: str | None = None, conn=Depends(db)):
    sql, args = "select * from records where 1=1", []
    if cohort_id is not None:
        sql += " and cohort_id=?"; args.append(cohort_id)
    if type:
        sql += " and type=?"; args.append(type)
    return [pub(dict(r)) for r in conn.execute(sql + " order by occurred_on desc, id desc", args)]


@app.get("/api/records/{rid}")
def read_record(rid: int, conn=Depends(db)):
    return pub(service.get_record(conn, rid))


@app.patch("/api/records/{rid}")
def patch_record(rid: int, b: RecordPatch, conn=Depends(db)):
    return pub(service.update_record(conn, rid, b.title, b.event_name, b.occurred_on, b.body))


@app.delete("/api/records/{rid}")
def remove_record(rid: int, conn=Depends(db)):
    rec = service.delete_record(conn, rid)
    if rec["pdf_path"]:
        Path(rec["pdf_path"]).unlink(missing_ok=True)
    return {"deleted": rid}


@app.get("/api/records/{rid}/pdf")
def record_pdf(rid: int, conn=Depends(db)):
    path = service.get_record(conn, rid)["pdf_path"]
    if not path or not Path(path).is_file():
        raise service.NotFound()
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@app.get("/api/timeline/last-year")
def timeline_last_year(date: date = Query(...), window_days: int = Query(14, ge=0, le=120), conn=Depends(db)):
    return service.last_year(conn, date, window_days)


@app.post("/api/ask")
def ask(b: AskIn, conn=Depends(db)):
    return answer_question(b.question, service.search(conn, b.question))


@app.post("/api/interviews/next")
def interview_next(b: InterviewNextIn, conn=Depends(db)):
    service.require_cohort(conn, b.cohort_id)
    q = next_interview_question(b.event_name, service.asked_questions(conn, b.cohort_id, b.event_name))
    return {"question": q, "done": q is None}


@app.post("/api/interviews/answer", status_code=201)
def interview_answer(b: InterviewAnswerIn, conn=Depends(db)):
    return pub(service.add_lesson(conn, b.cohort_id, b.event_name, b.question, b.answer, date.today()))


@app.post("/api/decisions", status_code=201)
def create_decision(b: DecisionIn, conn=Depends(db)):
    return service.add_decision(conn, b.timeline_item_id, b.body, b.reason, b.decided_on or date.today())


@app.get("/api/decisions")
def list_decisions(conn=Depends(db)):
    return [dict(r) for r in conn.execute(
        """select d.*, t.title as item_title, t.done_on as item_done_on, r.event_name, r.id as record_id, c.name as cohort_name
           from decisions d join timeline_items t on t.id=d.timeline_item_id
           join records r on r.id=t.record_id join cohorts c on c.id=d.cohort_id
           order by d.decided_on desc, d.id desc""")]
