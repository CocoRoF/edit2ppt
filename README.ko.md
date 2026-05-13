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

1. **로컬 설치형이 아닌 서버형.** 사용자 머신에 아무것도 설치하지 않습니다. 파일
   입출력은 HTTPS, 저장소는 S3 호환, 작업은 워커에서 비동기 처리.
2. **한국어 네이티브.** Hangul 텍스트 폭 계산, 한국어 폰트 스택, OOXML
   `lang="ko-KR"`, 이중 언어 에러 메시지, 한국식 레이아웃 템플릿이 기본 탑재.

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
