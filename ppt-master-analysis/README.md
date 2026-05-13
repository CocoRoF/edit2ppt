# ppt-master 분석 및 edit2ppt 설계 문서

## 정체성 한 줄

**edit2ppt 는 "AI Agent 시대의 PPT 생성 인프라" 입니다.** 사용자가 로컬에 아무것도 설치하지 않고, **MCP 서버 URL 한 줄 설정** 만으로 외부 AI Agent 가 즉시 사용할 수 있는 호스팅 서비스. 한국어 네이티브 지원.

ppt-master 의 강력한 SVG → 네이티브 PPTX 변환 엔진 (MIT 라이선스) 을 기반으로 하지만, **로컬 스킬 패키지에서 호스팅 서버로 패러다임을 전환** 합니다.

## 핵심 패러다임 전환

| 측면 | ppt-master (Before) | edit2ppt (After) |
|------|--------------------|--------------------|
| 설치 | `git clone + pip install` | 없음 |
| 진입점 | AI IDE 안의 채팅 (SKILL.md) | MCP URL 또는 HTTP API |
| LLM 위치 | 사용자의 IDE | 서버 (BYOK) |
| 파일 I/O | 로컬 디렉토리 | HTTP 업로드/다운로드, presigned URL |
| 상태 저장 | 로컬 디렉토리 트리 | PostgreSQL + Object Storage |
| 멀티 사용자 | N/A | 테넌트 격리, API 키 |
| 한국어 | 미지원 | 네이티브 (폰트/프롬프트/템플릿) |

## 문서 목차

| # | 문서 | 내용 |
|---|------|------|
| 00 | [philosophy.md](00-philosophy.md) | **핵심 철학** — 로컬 스킬에서 호스팅 엔진으로 + 이중 트랙 |
| 01 | [architecture.md](01-architecture.md) | ppt-master 내부 구조 분석 |
| 02 | [pipeline.md](02-pipeline.md) | 7단계 파이프라인, 결정적 스크립트 vs LLM 역할 |
| 03 | [korean-gaps.md](03-korean-gaps.md) | 한국어 지원 누락 13지점 (파일:라인 + 우선순위, G13 영문 리네임 포함) |
| 04 | [integration-plan.md](04-integration-plan.md) | **서버 아키텍처** — MCP-first, HTTP I/O, 멀티테넌트, i18n 카탈로그 |
| 05 | [roadmap.md](05-roadmap.md) | **8개 마일스톤** — 서버 골격부터 운영 + 정체성까지 |
| 06 | [bilingual-conventions.md](06-bilingual-conventions.md) | **이중 트랙 컨벤션** — 영문 파일 시스템 + 한국어 완벽 동작 |

## TL;DR

### 무엇을 만드는가

```
외부 AI Agent (Claude Desktop / Cursor / 자체)
    │
    │ MCP URL 한 줄 설정
    │ {"edit2ppt": {"url": "https://edit2ppt.example.com/mcp"}}
    │
    ▼
edit2ppt 서버
    ├─ MCP Server (HTTP+SSE)
    ├─ REST API
    ├─ Worker (Job Executor)
    ├─ 한국어 패치된 핵심 엔진 (ppt-master 포크)
    ├─ LLM 오케스트레이션 (BYOK)
    ├─ PostgreSQL (메타데이터)
    └─ Object Storage (파일들)
```

### 어떻게 동작하는가

1. **AI Agent** 가 우리 MCP 서버를 등록 (사용자가 한 줄 설정)
2. 사용자가 Agent 에게 "이 PDF로 한국어 PPT 만들어줘"
3. Agent 가 우리 도구 `edit2ppt.upload_source` 호출 → 파일 업로드
4. Agent 가 우리 도구 `edit2ppt.generate_deck` 호출
5. 서버가 LLM (BYOK 키) 로 Strategist/Executor 오케스트레이션
6. Agent 에게 progress notification 으로 진행률 푸시
7. 완료 시 도구 응답에 PPTX 다운로드 URL 반환
8. Agent 가 사용자에게 결과 전달

**사용자 머신에 설치 없음**, **LLM 비용은 사용자 부담 (BYOK)**, **한국어 네이티브 결과물**.

### 기존 모델과의 결정적 차이

ppt-master 는 "사용자의 LLM이 SKILL.md를 읽고 로컬 스크립트를 호출" 하는 모델. edit2ppt 는 "서버가 모든 것을 오케스트레이션하고 결과만 외부에 노출" 하는 모델. **AI Agent 입장에서 마찰 제로**.

## 한국어 + 이중 트랙 — Critical 패치 4건

| ID | 위치 | 문제 | 영향 |
|----|------|------|------|
| G1 | `drawingml_utils.py:427-433` | `is_cjk_char()` 주석엔 "Korean" 인데 실제로 **Hangul 범위 누락** | 한국어 텍스트 폭이 절반으로 추정 → 레이아웃 깨짐 |
| G2 | `pptx_notes.py:75,80,85` + `drawingml_elements.py:1002` | OOXML `lang="zh-CN"` 하드코딩 4곳 | 한국어가 중국어로 마킹됨 → PPT 교정 도구 오작동 |
| G3 | `drawingml_utils.py:31-88` + `config.py` | 한국어 폰트 fallback / 기본 스택 누락 | 한국어 글자에 PingFang SC 적용 → 글꼴 누락 |
| **G13** | `templates/layouts/重庆大学/`, `招商银行/`, `中国电建_*/`, `中汽研_*/` | 중국어 디렉토리/자산 파일명 7+개 | **트랙 A 위반** — 자동화/객체 스토리지 안전성 ↓ |

자세한 13개 항목은 [03-korean-gaps.md](03-korean-gaps.md), 컨벤션은 [06-bilingual-conventions.md](06-bilingual-conventions.md) 참조.

## 이중 트랙 원칙 (요약)

> **"디렉토리 이름은 영문, 화면에 보이는 글자는 한국어, 슬라이드 안의 내용은 사용자 언어"**

- **트랙 A (기계)**: 파일/디렉토리, 코드, DB 스키마, API path, 객체 스토리지 키 — **영문 ASCII만**
- **트랙 B (사람)**: UI 텍스트, 에러 메시지, MCP 도구 설명 — **한국어 + 영어 병기**
- **트랙 C (콘텐츠)**: 사용자 입력, 슬라이드 텍스트, 발표자 노트 — **사용자 언어 그대로**

## 다음 행동

[05-roadmap.md](05-roadmap.md) 의 12개 의사결정 (D1-D12) 을 정한 뒤 M0 (서버 골격, 1주) 부터 시작.

**M0 → M1 → ... → M7 총 11-15주 (1인 풀타임 기준)** 면 외부 사용자가 가입하여 한국식 PPT 를 받을 수 있는 상태에 도달.

## 분석 작성 기준

- 모든 사실 주장에는 ppt-master 원본의 파일:라인 인용
- 추측과 사실 구분 (추측은 "추정" 명시)
- 작업량/우선순위는 Critical / High / Medium / Low 로 표시
- 이 문서들은 살아있는 설계 — 의사결정 변화 시 즉시 업데이트
