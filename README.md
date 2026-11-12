# 졸업하지 않는 선배 · Backend

FastAPI + SQLite(FTS5 trigram). AI 로직은 형제 레포 `AI/`의 `senior_ai` 패키지를 import해서 쓴다.

## 로컬 실행

세 레포를 같은 폴더에 clone한다 (`AI/`, `Backend/`, `FrontEnd/`).

```bash
cd Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # ../AI 를 editable로 설치
chflags -R nohidden .venv              # macOS에서 editable 설치가 안 잡힐 때만
cp .env.example .env
python -m app.seed                     # DB를 시연용 시드로 초기화
uvicorn app.main:app --port 8000
```

- `LLM_PROVIDER=fake`(기본)는 키가 필요 없다. Gemini는 `LLM_PROVIDER=gemini` + `GEMINI_API_KEY`.
- 시드 재적재: `python -m app.seed` (기존 데이터는 지워진다).
- 테스트: `pytest` (항상 fake LLM, 네트워크 없음).
- API 문서: http://localhost:8000/docs

## 오류 코드

입력 오류 400, 없는 자료 404, AI 호출 실패 502.
