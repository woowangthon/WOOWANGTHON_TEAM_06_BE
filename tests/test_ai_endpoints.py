from conftest import rec

LESSON = "다음에는 버스를 한 달 전에 예약합니다. 짐 정리 시간을 줄입니다."


def seed(client, cohorts):
    item = rec(client, cohorts[0], title="MT 임원회의", event_name="MT", occurred_on="2025-05-03",
               body="2025-05-03 MT를 진행했다.").json()["timeline_items"][0]
    rec(client, cohorts[0], type="lesson", title="MT 교훈", event_name="MT", occurred_on="2025-05-04", body=LESSON)
    return item


def test_briefing_lists_events_with_sources_and_is_cached(client, cohorts):
    seed(client, cohorts)
    a = client.get("/api/briefing", params={"date": "2026-05-03", "window_days": 3}).json()
    assert a["cached"] is False and a["todos"] and "MT" in a["events"] and a["records"]
    assert client.get("/api/briefing", params={"date": "2026-05-03", "window_days": 3}).json()["cached"] is True
    rec(client, cohorts[0], title="추가", event_name="MT", occurred_on="2025-05-04", body="2025-05-04 정리했다.")
    assert client.get("/api/briefing", params={"date": "2026-05-03", "window_days": 3}).json()["cached"] is False  # records changed
    assert client.get("/api/briefing", params={"date": "2026-05-03", "window_days": 3, "refresh": "true"}).json()["cached"] is False


def test_interview_next_reports_progress(client, cohorts):
    seed(client, cohorts)
    q = client.post("/api/interviews/next", json={"cohort_id": cohorts[0], "event_name": "MT"}).json()
    assert q["question"] and q["answered"] == 0 and q["total"] >= 3 and q["done"] is False


def test_decision_draft_is_a_suggestion_not_a_saved_decision(client, cohorts):
    item = seed(client, cohorts)
    d = client.post("/api/decisions/suggest", json={"timeline_item_id": item["id"]}).json()
    assert d["body"] and d["reason"] and d["sources"][0]["title"] == "MT 교훈"
    assert client.get("/api/decisions").json() == []  # nothing saved until a person saves it
    assert client.post("/api/decisions/suggest", json={"timeline_item_id": 9999}).status_code == 404
