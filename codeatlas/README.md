# CodeAtlas — 처음 보는 코드 지도

**AI 기반 소스코드 구조 시각화 및 이해 지원 시스템** (2026 Capstone Design)

GitHub 저장소 주소를 넣으면 파이썬 코드의 구조를 **폴더 → 파일 → 함수** 순으로 확대해 볼 수 있는 지도로 그리고,
각 부분의 역할을 AI가 요약하고, 질문하면 답과 함께 **관련 코드의 호출 경로를 지도 위에 강조**해 줍니다.
처음 보는 프로젝트라면 **읽기 투어**가 실행 시작점부터 핵심 코드를 순서대로 안내합니다.

| 기능 | 설명 |
|---|---|
| 코드 지도 | 폴더·파일·클래스·함수를 묶음(compound) 그래프로 표시. 두 번 누르면 안쪽이 펼쳐짐. 노드 크기 = 중요도(PageRank), 색 = 서로 많이 부르는 파일 묶음(Louvain) |
| AI 요약 | 함수·클래스 → 파일 → 폴더 순으로 아래에서 위로 요약을 쌓음. 한 번 만든 요약은 캐시 |
| 질문하기 | 질문 → 관련 코드 검색(BM25 + 한국어 용어 사전) → 호출 그래프에서 근거 경로 찾기 → 그 코드만 근거로 LLM이 답변 |
| 읽기 투어 | `__main__.py`, `if __name__ == "__main__"`, 또는 "호출을 따라가면 가장 많은 코드에 닿는 함수"에서 출발해 핵심 흐름을 따라감 |

> LLM 키가 없어도 지도·검색·질문(관련 코드와 경로 찾기)·투어가 모두 동작합니다. 요약은 docstring과 구조로 자동 생성됩니다.
> 분석은 코드를 **실행하지 않고** 읽기만 합니다(ast 정적 분석). 모르는 저장소를 넣어도 안전합니다.

---

## 1. 바로 실행하기 (파이썬만 있으면 됨)

필요한 것: **Python 3.10 이상**, GitHub 주소로 분석하려면 **git**.

- Windows: `run_windows.bat` 더블클릭 → 잠시 뒤 브라우저가 `http://localhost:8000` 을 엽니다.
직접 실행하고 싶다면:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

화면(React)은 `frontend/dist`에 미리 빌드해 두어서 서버가 같이 띄워 줍니다. API 문서는 `http://localhost:8000/docs`.

처음 화면에서 `psf/requests` 같은 예제를 누르면 바로 분석이 시작됩니다 (파일 20개 정도면 몇 초).

## 2. AI 켜기 (.env)

`.env.example`을 같은 폴더에 `.env`로 복사한 뒤 하나를 고르세요.

| 방법 | 설정 | 비고 |
|---|---|---|
| 끄기 | `LLM_PROVIDER=none` | 기본값 |
| Claude | `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` | 기본 모델 `claude-haiku-5-5` (빠르고 저렴) |
| OpenAI 등 | `LLM_PROVIDER=openai`, `OPENAI_API_KEY=...`, `LLM_MODEL=...` | OpenAI 호환이면 `LLM_BASE_URL`만 바꿔서 사용 |
| Ollama | `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen2.5-coder:7b` | 내 컴퓨터에서 무료로. 먼저 `ollama pull` |

비용은 `SUMMARY_MAX_ENTITIES`(기본 120), `SUMMARY_MAX_FILES`(기본 60)로 조절합니다. 중요도 상위만 LLM으로 요약하고 나머지는 자동 요약합니다.
서버를 다시 켜면 적용되고, 같은 저장소를 다시 분석하면 바뀐 코드만 새로 요약합니다(캐시).

## 3. 화면 고치기 (Node.js 18 이상)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173  (/api 요청은 8000번 서버로 자동 전달)
npm run build      # frontend/dist 갱신 → 백엔드가 이 화면을 제공
```

백엔드 서버(8000)를 먼저 켜 두세요.

## 4. 폴더 구조

```
codeatlas/
├─ backend/
│  ├─ app/
│  │  ├─ main.py              API 서버 (FastAPI)
│  │  ├─ config.py            .env 설정
│  │  ├─ store.py             분석 작업 실행·진행률·결과 저장
│  │  ├─ analyzer/
│  │  │  ├─ fetch.py          GitHub clone / 로컬 폴더
│  │  │  ├─ parser.py         ast로 함수·클래스·import·호출·타입 힌트 추출
│  │  │  └─ graph.py          호출 해석, NetworkX 그래프, PageRank, Louvain 묶음
│  │  └─ ai/
│  │     ├─ llm.py            LLM 연결 (none/anthropic/openai/ollama, 표준 라이브러리만 사용)
│  │     ├─ summarize.py      계층적 요약 + 캐시
│  │     ├─ search.py         BM25 검색 + 한국어 용어 사전
│  │     ├─ qa.py             그래프 기반 질의응답 (Graph RAG)
│  │     └─ tour.py           읽기 투어
│  ├─ cli.py                  명령줄 도구 (화면 없이 분석·질문·투어)
│  └─ tests/                  단위 테스트 + 샘플 저장소
└─ frontend/
   ├─ src/
   │  ├─ App.jsx              화면 상태 관리
   │  ├─ graphModel.js        펼침/접힘에 따라 보일 노드·간선 계산 (순수 함수)
   │  └─ components/          지도(MapView), 질문, 투어, 상세 패널 …
   └─ dist/                   빌드된 화면
```

## 5. 동작 원리

1. **가져오기**: GitHub 저장소를 `git clone --depth 1` (이미 받았으면 `git pull`).
2. **파싱**: 모든 `.py` 파일을 `ast`로 읽어 함수·클래스·메서드, import, 호출 이름(`self.save`, `utils.load` …), 타입 힌트를 뽑습니다. 기본으로 `tests/ docs/ examples/`는 제외합니다.
3. **호출 해석** (`graph.py`): 호출 이름을 실제 정의로 연결합니다.
   - `self.x()` → 자기 클래스와 부모 클래스의 메서드, `super().x()` → 부모 클래스
   - `from .utils import f`, `import pkg.mod as m` → 다른 파일의 정의 (src 레이아웃, 재수출 포함)
   - `svc = AuthService(); svc.login()`, `self.repo = Repo()`, `def f(x: Foo)` → 변수 타입을 기억해서 연결
   - 그래도 모르면 "저장소 전체에 그 이름의 함수가 딱 하나"일 때만 점선(추정)으로 연결
4. **중요도와 묶음**: 호출 그래프에 PageRank → 노드 크기, 파일 사이 관계에 Louvain → 색.
5. **요약**: 중요도 상위 함수부터 LLM 요약 → 그 요약들로 파일 요약 → 폴더 요약.
6. **질문하기**: 질문 + (LLM이 만든 영어 키워드 / 한국어 용어 사전) → BM25로 관련 코드 → 관련 코드들을 잇는 호출 경로 중 관련도 합이 가장 큰 경로 → 그 코드 조각만 LLM에 주고 답변, 근거 번호 [1][2]를 지도와 연결.
7. **읽기 투어**: 진입점 후보마다 "호출을 따라가면 닿는 코드 수"를 세어 출발점을 고르고, 흐름이 이어지는 함수를 먼저 깊이 우선으로 따라갑니다.

Django(파일 909개, 함수 9천여 개)도 분석·그래프 생성이 수 초 안에 끝납니다(LLM 요약 제외).

## 6. API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 서버·LLM 상태 |
| POST | `/api/projects` | 분석 시작 `{"source": "https://github.com/psf/requests"}` |
| GET | `/api/projects/{id}/status` | 진행률 |
| GET | `/api/projects/{id}/graph` | 지도 데이터 (노드·간선) |
| GET | `/api/projects/{id}/node?id=...` | 코드, 요약, 부르는/불리는 곳 |
| POST | `/api/projects/{id}/ask` | 질문 `{"question": "..."}` |
| GET | `/api/projects/{id}/tour` | 읽기 투어 |

## 7. 명령줄 도구와 테스트

```bash
cd backend
python cli.py analyze https://github.com/psf/requests
python cli.py list
python cli.py ask psf-requests "세션은 요청을 어떻게 보내?"
python cli.py tour psf-requests

python -m unittest discover -s tests -v     # API 테스트까지 돌리려면 pip install httpx
```

## 8. 알려진 한계

- 파이썬만 분석합니다. `getattr`, 동적 import, 데코레이터로 바뀌는 호출처럼 **실행해야 알 수 있는 호출**은 잡지 못합니다.
- 한국어 질문은 LLM이 꺼져 있으면 `search.py`의 용어 사전에 있는 단어만 영어로 바뀝니다. 사전을 늘리거나 LLM을 켜면 좋아집니다.
- 함수 단위 보기는 노드가 1,500개를 넘으면 막아 둡니다. 큰 저장소는 폴더·파일을 두 번 눌러 필요한 곳만 펼치세요.

## 9. 다음 단계 (계획서의 수행 방법과 연결)

- **평가 실험 도구**: "특정 기능 위치 찾기" 과제를 주고 화면에서 걸린 시간·정답을 기록하는 실험 모드 → 코드만 볼 때 / 기존 AI 문서화 도구 / CodeAtlas 비교
- **질의응답 정확도 측정**: 정답이 정해진 질문 30~50개로 그래프 경로 사용 여부에 따른 정확도 비교 (`answer_question`의 결과 `nodes`, `path`를 정답과 대조)
- **검색 개선**: 임베딩 검색 추가, 한국어 용어 사전 확장
- **확장**: tree-sitter로 Java·JavaScript 지원, VS Code 확장
