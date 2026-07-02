# edit2ppt

**AI Agent 시대의 PPT 생성 인프라. 한국어 네이티브. MCP 호환.**

[English README](./README.md) · [ppt-master 기반 (Hugo He, MIT)](https://github.com/hugohe3/ppt-master)

---

`edit2ppt` 는 호스팅형 PPT 생성 엔진입니다. ppt-master 처럼 로컬에 설치한 스킬을
IDE 의 LLM 이 직접 구동하는 모델이 아니라, **MCP 호환 Agent (Claude Desktop, Cursor,
자체 봇 등) 가 우리 서버 URL 만 등록하면 즉시 사용** 할 수 있는 모델입니다. Agent 는
소수의 도구 (`generate_deck`, `upload_source`, `list_templates`) 를 얻고, 진짜
편집 가능한 PPTX 를 받아 갑니다.

ppt-master 와의 두 가지 결정적 차이:

**네 가지 사용 방식** (같은 엔진, 표면만 선택):

```bash
pip install edit2ppt              # 라이브러리 + 에이전트 도구 + 로컬 MCP
pip install "edit2ppt[server]"    # + 호스팅 서비스
```

1. **Python 라이브러리** — `from edit2ppt import generate_pptx, edit_pptx,
   preview_pptx, set_pptx_text, analyze_pptx`. 파일 경로 기반, 무인프라.
   BYOK는 `api_key=` 또는 `ANTHROPIC_API_KEY`; preview/set_text/analyze는 키 불필요.
2. **에이전트 도구** — `from edit2ppt.agent_tools import ANTHROPIC_TOOLS,
   run_tool`. Anthropic tool-use에 바로 넣는 스키마 + 디스패처.
3. **로컬 MCP 서버** — `edit2ppt-mcp` (stdio). Claude Desktop/Code/Cursor 설정에
   command 한 줄이면 끝. DB·스토리지·서버 없음.
4. **호스팅 서비스** — `edit2ppt serve` (REST + SSE 잡 + 호스티드 MCP).
   [웹 스튜디오](https://hrletsgo.me/edit2ppt)가 이걸로 돌아갑니다.

ppt-master 와의 두 가지 결정적 차이:

1. **모든 계층이 에이전트 네이티브.** 동일한 무상태 도구 함수가 라이브러리·
   함수호출 스키마·두 MCP 서버·호스팅 API를 모두 구동.
2. **한국어 네이티브.** Hangul 텍스트 폭 계산, 한국어 폰트 스택, OOXML
   `lang="ko-KR"`, 이중 언어 에러 메시지, 한국식 레이아웃 템플릿이 기본 탑재.

## 내 PPTX 를 템플릿으로 쓰기

`.pptx` 파일을 `POST /v1/assets` (또는 MCP `upload_source` 도구) 로 업로드한 뒤,
`generate-deck` 에 `template_asset_id` 로 넘기면 됩니다:

- `deck_mode: "template_restyle"` — **템플릿 패키지 안에 새 덱을 생성**: 슬라이드
  마스터·레이아웃·테마 색·폰트가 그대로 유지되고 (로고·배경 크롬이 모든 생성
  슬라이드 뒤에 렌더링), 원본 슬라이드는 제거됩니다. Strategist 는 템플릿 테마의
  결정론적 분석 결과 (색상/폰트/캔버스/문체 샘플) 를 받아 그에 맞춰 디자인합니다.
- `deck_mode: "template_extend"` — 생성된 슬라이드 (네이티브 DrawingML 도형,
  차트, 아이콘, SVG 기반 그래픽) 를 **기존 슬라이드 뒤에 추가**합니다.

16:9 / 4:3 템플릿을 지원하며, 생성 좌표는 호스트 덱의 실제 슬라이드 크기에 맞춰
자동 리스케일됩니다.

## 기존 덱을 채팅으로 편집하기

웹 스튜디오의 "PPT 같이 만들기"를 구동하는 두 엔드포인트 (MCP `edit_deck`
도구로도 노출):

- `POST /v1/preview` — 결정론적·동기: 모든 슬라이드를 자기완결형 SVG 로 렌더링
  (마스터·레이아웃 인라인, 이미지 base64 임베드) — 브라우저 미리보기용.
- `POST /v1/jobs/edit-deck` — 채팅 한 턴: LLM 플래너가 지시 + 덱 아웃라인을
  슬라이드 단위 작업 (edit / add / delete) 으로 변환하고, 대상 슬라이드마다
  LLM 이 SVG 를 다시 쓴 뒤, 결정론적 recompose 가 패키지에 반영합니다.
  건드리지 않은 슬라이드는 정체성 (id·노트·애니메이션) 을 유지합니다. 매 턴이
  새 pptx 에셋을 만들고 이전 리비전은 보존됩니다. 질문만 하는 턴은 덱을
  바꾸지 않고 채팅으로만 답합니다.

## 아키텍처 개요

```
외부 Agent (Claude / Cursor / 자체 봇)
        │ MCP (HTTP+SSE)
        ▼
┌─────────────────────────────────────────────────┐
│  edit2ppt 서버                                   │
│   MCP 라우트  ─┐                                 │
│   REST 라우트 ─┤── Job 큐 (arq + Redis) ────┐   │
│                │                            │   │
│                ▼                            ▼   │
│         Tool 함수 (Python)              워커     │
│                │                            │   │
│                ▼                            ▼   │
│   핵심 엔진 (ppt-master 포크 + 한국어 패치)       │
│                                                  │
│   PostgreSQL · Object Storage (S3) · Redis      │
└─────────────────────────────────────────────────┘
```

상세 설계 문서는 [`ppt-master-analysis/`](./ppt-master-analysis/) 참조 (철학, 파이프라인,
한국어 갭 분석, 통합 계획, 로드맵, 이중 트랙 컨벤션).

## 현재 진행 상황

| 마일스톤 | 완료 | 다음 |
|---------|------|------|
| M0 (진행 중) | 패키지 골격, i18n 카탈로그, FastAPI 스캐폴드, 헬스체크 | DB + Redis + S3 연결, docker-compose |
| M1 (진행 중) | 핵심 엔진 임포트, 중국어 자산 영문 리네임, G1/G2/G3 한국어 패치 + 단위 테스트 66건 통과 | 한국어 프롬프트 변종 |
| M2 | — | Tool 함수 + Anthropic SDK (BYOK) |
| M3 | — | REST API + Job 큐 + SSE |
| M4 | — | MCP 서버 (HTTP+SSE) |
| M5–M7 | — | 한국어 프롬프트/템플릿, 멀티테넌트 운영, 브랜딩 |

## 이중 트랙 컨벤션 (핵심)

두 트랙을 엄격히 분리합니다.

| 트랙 | 영역 | 언어 |
|------|------|------|
| **A** | 파일 시스템, 코드 식별자, DB 스키마, API path, 스토리지 키 | **영문 ASCII 만** |
| **B** | UI 텍스트, 에러 메시지, MCP 도구 설명 | **한국어 + 영어 병기** |
| **C** | 사용자 입력, 슬라이드 텍스트, 발표자 노트, TTS | **사용자 언어 그대로** |

pre-commit ASCII lint 와 단위 테스트로 강제됩니다.
[`ppt-master-analysis/06-bilingual-conventions.md`](./ppt-master-analysis/06-bilingual-conventions.md) 참조.

## 개발 환경

Python 3.11+ 필요. [`uv`](https://github.com/astral-sh/uv) 권장.

```bash
# 가상환경 + 의존성 설치
uv venv .venv
uv pip install --python .venv/bin/python -e .[dev]

# 테스트
.venv/bin/python -m pytest

# ASCII 경로 lint
.venv/bin/python scripts/lint_ascii_paths.py

# 개발 서버
.venv/bin/python -m edit2ppt.cli serve --reload
# → http://localhost:8000/health
# → http://localhost:8000/v1/messages/sample (Accept-Language: ko-KR 헤더와 함께)
```

## 라이선스

[MIT](./LICENSE). [ppt-master](https://github.com/hugohe3/ppt-master) (Hugo He, MIT)
기반. attribution 유지.

## 감사

- [ppt-master](https://github.com/hugohe3/ppt-master) — Layer 1 의 SVG → OOXML 변환 엔진
- SVG Repo · Tabler Icons · Simple Icons · Phosphor Icons — ppt-master 에서
  계승된 아이콘 라이브러리
