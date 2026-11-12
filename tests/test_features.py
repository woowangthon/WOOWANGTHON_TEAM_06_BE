from conftest import rec


def test_f1_saves_record_and_items(client, cohorts):
    r = rec(client, cohorts[0])
    assert r.status_code == 201
    j = r.json()
    assert j["record"]["id"] and len(j["timeline_items"]) >= 1 and j["extraction"] == "ok"


def test_f1_missing_date_is_400(client, cohorts):
    r = client.post("/api/records", json={"cohort_id": cohorts[0], "type": "meeting", "title": "t", "body": "b"})
    assert r.status_code == 400


def test_unknown_cohort_is_404(client):
    assert rec(client, 999).status_code == 404


def test_f2_only_same_window_last_year(client, cohorts):
    rec(client, cohorts[0], body="2025-11-04 체육대회를 진행했다.")
    rec(client, cohorts[0], title="먼 기록", body="2025-03-05 모집 일정을 확정했다.", occurred_on="2025-03-05")
    cards = client.get("/api/timeline/last-year", params={"date": "2026-11-10", "window_days": 14}).json()["cards"]
    assert [c["done_on"] for c in cards] == ["2025-11-04"]


def test_f2_year_boundary(client, cohorts):
    rec(client, cohorts[0], body="2024-12-28 송년회를 진행했다.", occurred_on="2024-12-28")
    rec(client, cohorts[0], title="새해", body="2025-01-10 신년 회의를 했다.", occurred_on="2025-01-10")
    cards = client.get("/api/timeline/last-year", params={"date": "2026-01-03", "window_days": 14}).json()["cards"]
    assert [c["done_on"] for c in cards] == ["2024-12-28", "2025-01-10"]


def test_f2_empty_window_shows_nearest_with_notice(client, cohorts):
    rec(client, cohorts[0], body="2025-03-05 모집 일정을 확정했다.", occurred_on="2025-03-05")
    j = client.get("/api/timeline/last-year", params={"date": "2026-11-10"}).json()
    assert j["notice"] and len(j["cards"]) == 1


def test_f3_answer_has_sources_and_unknown_is_empty(client, cohorts):
    rec(client, cohorts[0], body="2025-05-09 부스 예산은 30만 원으로 책정했다.", event_name="봄 축제 부스")
    ok = client.post("/api/ask", json={"question": "축제 부스 예산은 얼마였어?"}).json()
    assert len(ok["sources"]) >= 1 and "30만 원" in ok["sources"][0]["quote"]
    none = client.post("/api/ask", json={"question": "학식 메뉴는 뭐였어?"}).json()
    assert none["sources"] == [] and "기록에 없다" in none["answer"]


def test_f4_lesson_saved_and_searchable(client, cohorts):
    rec(client, cohorts[0])
    q = client.post("/api/interviews/next", json={"cohort_id": cohorts[0], "event_name": "가을 체육대회"}).json()
    assert q["question"] and not q["done"]
    r = client.post("/api/interviews/answer", json={"cohort_id": cohorts[0], "event_name": "가을 체육대회",
                                                    "question": q["question"], "answer": "현수막 제작을 일찍 맡겨야 한다"})
    assert r.status_code == 201 and r.json()["type"] == "lesson"
    out = client.post("/api/ask", json={"question": "현수막은 어떻게 했어?"}).json()
    assert any(s["record_id"] == r.json()["id"] for s in out["sources"])
    nxt = client.post("/api/interviews/next", json={"cohort_id": cohorts[0], "event_name": "가을 체육대회"}).json()
    assert nxt["question"] != q["question"]


def test_f5_decision_creates_record_and_attaches_next_year(client, cohorts):
    item = rec(client, cohorts[1], body="2026-11-04 체육대회를 진행했다.", occurred_on="2026-11-04").json()["timeline_items"][0]
    d = client.post("/api/decisions", json={"timeline_item_id": item["id"], "body": "간식을 1.5배로 준비", "reason": "작년에 부족했다"})
    assert d.status_code == 201
    assert any(r["type"] == "decision" for r in client.get("/api/records").json())
    cards = client.get("/api/timeline/last-year", params={"date": "2027-11-10"}).json()["cards"]
    assert cards[0]["decisions"][0]["body"] == "간식을 1.5배로 준비"
    assert len(client.get("/api/decisions").json()) == 1


def test_decision_requires_content(client, cohorts):
    assert client.post("/api/decisions", json={"timeline_item_id": 1, "body": "", "reason": "x"}).status_code == 400


def test_seed_makes_demo_card(client, monkeypatch):
    from app import db, seed
    seed.run(db.connect())
    j = client.get("/api/timeline/last-year", params={"date": "2026-11-15"}).json()
    assert j["cards"] and any(c["lessons"] for c in j["cards"])


def test_record_exposes_has_pdf_not_path_and_pdf_404(client, cohorts):
    from conftest import rec
    r = rec(client, cohorts[0]).json()["record"]
    assert r["has_pdf"] is False and "pdf_path" not in r
    assert client.get(f"/api/records/{r['id']}/pdf").status_code == 404
