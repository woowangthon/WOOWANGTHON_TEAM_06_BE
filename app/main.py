from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from senior_ai import LLMError, answer_question, next_interview_question

from . import service
from .db import connect

app = FastAPI(title="졸업하지 않는 선배")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def _bad_input(_, exc):
    return JSONResponse({"detail": "입력 오류", "errors": [e["msg"] for e in exc.errors()]}, status_code=400)


@app.exception_handler(service.NotFound)
async def _missing(_, exc):
    return JSONResponse({"detail": "없는 자료"}, status_code=404)


@app.exception_handler(LLMError)
async def _ai_down(_, exc):
    return JSONResponse({"detail": f"AI 호출 실패: {exc}"}, status_code=502)


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
    return [dict(r) for r in conn.execute("select * from decisions order by decided_on desc, id desc")]
