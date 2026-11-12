# 졸업하지 않는 선배 · Backend

FastAPI + SQLite(FTS5 trigram). AI 로직은 형제 레포 `AI/`의 `senior_ai` 패키지를 import해서 쓴다.

## 로컬 실행

세 레포를 같은 폴더에 clone한다 (`AI/`, `Backend/`, `FrontEnd/`).

```bash
cd Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ../AI                   # 로컬 개발: 옆 폴더의 AI 레포를 바로 반영
chflags -R nohidden .venv              # macOS에서 editable 설치가 안 잡힐 때만
cp .env.example .env
python -m app.seed                     # DB를 시연용 시드로 초기화
uvicorn app.main:app --port 8000
```

- `LLM_PROVIDER=fake`(기본)는 키가 필요 없다. 실제 답변은 `LLM_PROVIDER=gemini` + `GEMINI_API_KEY`(+ `GEMINI_MODEL`)로 켠다. '선배에게 묻기'는 문서를 약 500자 구간으로 나눠 관련 구간을 찾은 뒤 모델이 그 구간만으로 답하고, 인용 구절이 원문에 있는지 서버가 다시 확인한다.
- 시드 재적재: `python -m app.seed` (기존 데이터는 지워진다).
- 테스트: `pytest` (항상 fake LLM, 네트워크 없음).
- API 문서: http://localhost:8000/docs

## 오류 코드

입력 오류 400, 없는 자료 404, AI 호출 실패 502.

## 문서 올리기와 가져오기

- 화면의 기록 탭에서 파일(PDF, hwp, hwpx, docx, txt)을 올릴 수 있다. API는 `POST /api/records/upload`(multipart, 최대 20MB).
- PDF는 원본이 저장되어 원문 팝업에서 그대로 보인다. hwp·hwpx는 글자만 읽어 텍스트로 보여준다.

한꺼번에 가져오기:

```bash
python -m scripts.import_docs ../test_docu/48th   # DB를 비우고 문서 전체를 가져온다
```

- `<폴더>/<N. 행사명>/파일` 구조를 기대한다. `0.`으로 시작하는 폴더, 명단·조표·유인물 파일은 건너뛴다.
- 파일명에 `평가`가 있으면 교훈(lesson), 아니면 회의(meeting)로 저장한다. 2024가 들어간 파일은 47대, 나머지는 48대.
- 문서 안의 날짜 오타(연도 복붙 등)는 고치지 않는다. 원본 문서와 `data/app.db`는 git에 올리지 않는다(실명 포함).

## 배포 (Render)

`render.yaml`이 서비스 설정이다. 디스크(`/var/data`)가 SQLite와 올린 PDF를 보관하므로 유료 플랜이 필요하다.

1. Render에서 이 레포를 Blueprint로 연결한다.
2. 환경 변수를 입력한다: `APP_ACCESS_KEY`(접근 키), `CORS_ORIGINS`(FE 주소), 필요하면 `GEMINI_API_KEY`와 `LLM_PROVIDER=gemini`.
3. 첫 부팅에서 디스크가 비어 있으면 가상 시드(`SEED_DEMO=1`)가 들어간다. 실제 문서는 화면의 파일 업로드로 올린다.
- `APP_ACCESS_KEY`가 있으면 `/api/health`를 뺀 모든 API가 `X-Access-Key` 헤더를 요구한다.
