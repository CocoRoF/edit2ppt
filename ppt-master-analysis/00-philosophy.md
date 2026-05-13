# 00. 핵심 철학 — Local Skill → Hosted Engine

이 문서는 edit2ppt가 ppt-master로부터 **어떤 아키텍처적 전환을 하는지** 를 정의합니다. 이후 모든 설계 문서 (04, 05) 는 이 철학에서 파생됩니다.

---

## 0.1 ppt-master 의 패러다임 (Before)

```
┌───────────────────────────────────────────┐
│        사용자의 로컬 머신                    │
│ ┌───────────────────────────────────────┐ │
│ │ AI IDE (Claude Code / Cursor)         │ │
│ │  ├─ LLM (외부 API)                    │ │
│ │  └─ ppt-master/ 디렉토리 전체 로컬     │ │
│ │      ├─ SKILL.md (LLM이 읽는 워크플로)│ │
│ │      ├─ scripts/ (LLM이 호출)        │ │
│ │      ├─ templates/ (LLM이 참조)      │ │
│ │      ├─ projects/ (산출물 저장)      │ │
│ │      └─ requirements.txt 등          │ │
│ └───────────────────────────────────────┘ │
└───────────────────────────────────────────┘
```

특징:
- 사용자가 `git clone` + `pip install` 해야 함
- LLM이 로컬 파일 시스템에 직접 접근 (Read/Write/Bash)
- 모든 산출물이 사용자 디스크
- 업데이트는 사용자가 `git pull` 해야 적용
- 시스템 사양/Python 버전에 의존
- AI Agent가 사용하려면 사용자의 환경을 신뢰해야 함

이 모델은 **개인 사용자가 자기 IDE에서 직접 작업** 하는 데 최적화됨. AI Agent 가 자율적으로 사용하기엔 마찰이 큼 (설치, 파일 시스템 권한, 환경 차이).

---

## 0.2 edit2ppt 의 패러다임 (After)

```
┌─────────────────────────────────┐         ┌────────────────────────────────────┐
│  외부 클라이언트                  │         │  edit2ppt 서버 (어딘가 호스팅)        │
│  (사용자/AI Agent)                │         │ ┌────────────────────────────────┐ │
│ ┌───────────────────────────┐   │  HTTPS  │ │  MCP Server (HTTP+SSE)         │ │
│ │ MCP Client                │───┼────────►│ ├────────────────────────────────┤ │
│ │  - server URL 한 줄 설정   │   │   MCP   │ │  HTTP REST API                 │ │
│ │  - 도구 자동 발견           │   │         │ ├────────────────────────────────┤ │
│ └───────────────────────────┘   │         │ │  내부 Tool 함수 (Layer 2)       │ │
│                                 │         │ ├────────────────────────────────┤ │
│ 파일 업로드/다운로드 ◄──────────┼─HTTPS──►│ │  핵심 엔진 (ppt-master 포크)    │ │
│  - 소스 PDF/DOCX 업로드         │         │ │  + 한국어 패치 + LLM 오케스트   │ │
│  - 결과 PPTX 다운로드           │         │ ├────────────────────────────────┤ │
│                                 │         │ │  Object Storage (S3 호환)      │ │
│                                 │         │ │   - 업로드된 소스                │ │
│                                 │         │ │   - 중간 산출물 (SVG, 이미지)    │ │
│                                 │         │ │   - 최종 PPTX                  │ │
│                                 │         │ └────────────────────────────────┘ │
└─────────────────────────────────┘         └────────────────────────────────────┘
```

특징:
- 사용자는 **아무것도 로컬에 설치하지 않음**
- 외부 AI Agent는 **MCP 서버 URL 한 줄만 설정하면 즉시 사용**
- 모든 파일 입출력은 HTTP(S) — 업로드/다운로드 또는 presigned URL
- 템플릿/스크립트/LLM 오케스트레이션 전부 서버 내부
- 업데이트는 서버 측 1회 배포 → 모든 사용자 즉시 적용
- 한국어 폰트, 차트 템플릿, 아이콘 라이브러리도 서버에 상주
- 멀티 테넌트 — 다수 사용자가 동시 사용

---

## 0.3 패러다임 전환의 함의

이 전환은 단순한 "원격화" 가 아니라 **여러 곳에서 가정이 바뀝니다**.

### F1. 파일 시스템 가정 폐기

원본은 모든 스크립트가 로컬 경로 (`projects/<name>/sources/file.pdf`) 를 입출력. edit2ppt 서버에서는:

- 클라이언트는 "파일 경로" 를 모름. 대신 **upload_id / file_uri / presigned URL** 로 식별
- 서버 내부 작업 디렉토리는 **사용자에게 노출되지 않음** (보안, 동시성)
- 업로드는 multipart POST → server 가 internal storage 에 저장 → `file_id` 반환
- 다운로드는 server 가 만드는 단기 presigned GET URL

**결과**: 모든 Tool 함수 시그니처가 "bytes / file_id / URL" 기반으로 통일. 로컬 경로 절대 노출 X.

### F2. 상태 저장소 명시화

원본은 디렉토리 트리 = 상태. edit2ppt 는 **명시적 데이터 모델**:

- `Project` — 사용자 작업 단위, 메타데이터 (제목, 언어, 템플릿, 생성 시각, 만료 정책)
- `SourceFile` — 업로드된 원본 파일 (파일 자체는 object storage, 메타는 DB)
- `Job` — 1회 PPT 생성 시도, 단계별 상태 (queued / converting / strategizing / generating / exporting / done / failed)
- `Asset` — 중간 산출물 (markdown, spec_lock, svg, image, pptx)

DB (PostgreSQL 권장) 에 메타데이터, Object Storage (S3/MinIO) 에 바이너리.

### F3. 멀티 테넌트가 기본값

원본은 "내 컴퓨터, 내 디렉토리" 가정. edit2ppt 는 day 1부터:

- **API 키 기반 인증** — 모든 요청에 `Authorization: Bearer <key>`
- **테넌트 격리** — Project / Job / Asset 은 모두 `tenant_id` 로 스코프
- **레이트리밋** — per-tenant concurrency, per-tenant 일 사용량
- **격리된 작업 공간** — 한 사용자가 다른 사용자 파일을 볼 수 없음

### F4. BYOK (Bring Your Own Key) 가 디폴트

edit2ppt 가 LLM/이미지 비용을 부담하지 않습니다. 사용자가 자기 키를 가져오는 형태:

- 옵션 A: 요청마다 키 전달 — `X-Anthropic-API-Key: ...` 헤더 또는 body
- 옵션 B: 테넌트 등록 시 키 저장 (암호화) → 매 요청에 자동 적용
- 옵션 C: 우리가 키를 보유 (메이커 모드) — 비용 청구 필요. 초기에는 비권장

**권장**: 옵션 B 기본 + 옵션 A 오버라이드 가능.

### F5. MCP가 최우선 인터페이스

원본은 LLM (사용자의 IDE AI) 이 SKILL.md 를 읽고 bash로 스크립트 호출. edit2ppt 는 **MCP 도구로 노출** :

- 외부 AI Agent (Claude Desktop / Cursor / 자체 Agent) 가 우리 서버를 도구로 등록
- `mcp.json` 한 줄: `"edit2ppt": {"url": "https://edit2ppt.example.com/mcp"}`
- Agent가 자연어로 사용자 의도를 받음 → 우리 MCP 도구를 자율 호출 → PPT 받아옴
- HTTP REST API는 비-AI 클라이언트 (CLI, 다른 백엔드 서비스) 용으로 병행 노출

### F6. SKILL.md 의 사라짐

원본은 SKILL.md 27.8KB 프롬프트를 사용자의 LLM이 읽음. edit2ppt 서버에서는:

- **사용자의 LLM은 SKILL.md 를 읽지 않음**
- 서버 내부에서 우리 코드가 Anthropic SDK 등으로 LLM 호출
- Strategist/Executor 역할 프롬프트는 서버 안에 상주 (사용자에게 보이지 않음)
- 사용자가 보는 것은 고수준 MCP 도구 (`generate_deck`, `regenerate_page` 등)

이는 단점도 있음: 사용자가 워크플로를 커스터마이즈하기 어려움. 그러나 **AI Agent 자동화** 라는 목표에는 부합 — Agent는 복잡한 SKILL.md 를 읽고 7단계를 직접 오케스트레이션하기보다, 고수준 도구 한 번 호출하는 것을 선호.

(보조 모드로, 저수준 도구들도 함께 노출 — `convert_source`, `strategize`, `execute_page`, `export_pptx` 등. Agent 가 세밀하게 제어하고 싶으면 사용 가능.)

### F7. 이중 트랙 — 영문 파일 시스템 + 한국어 완벽 동작

원본 ppt-master 는 중국어 디렉토리/파일명을 자유롭게 사용 (`templates/layouts/重庆大学/`, `examples/ppt169_高端咨询风_...`). edit2ppt 는 **두 트랙으로 분리**:

- **트랙 A (기계)**: 파일/디렉토리 이름, 코드 식별자, DB 스키마, API path, 객체 스토리지 키 — **영문 ASCII만**
- **트랙 B (사람)**: UI 텍스트, 에러 메시지, MCP 도구 설명, API response message — **한국어 + 영어 병기**
- **트랙 C (콘텐츠)**: 사용자 입력, 생성된 슬라이드 텍스트, 발표자 노트 — **사용자 언어 그대로**

핵심: **"디렉토리 이름은 영문, 화면에 보이는 글자는 한국어, 슬라이드 안의 내용은 사용자 언어"**.

이 원칙으로:
- 자동화 도구가 안전하게 파일 시스템을 다룰 수 있음 (CJK 인코딩 이슈 없음)
- 사용자는 한국어로 자연스럽게 사용 (에러 메시지, 진행률 모두 한국어)
- 생성된 PPT 는 한국어로 완벽 동작 (폰트, lang 속성, 텍스트 폭 모두 정확)

자세한 컨벤션과 안티 패턴은 [06-bilingual-conventions.md](06-bilingual-conventions.md) 참조.

### F8. 진행 상황 스트리밍

PPT 한 개 생성에 수 분 ~ 수십 분. HTTP 요청 안에서 완료를 기다리는 것은 비현실적:

- **비동기 job 패턴**: POST → `job_id` 반환 → 폴링 또는 SSE/WebSocket 스트리밍
- MCP 의 경우: long-running tool 지원 (MCP spec 2025-03-26 이후 progress notification) 활용
- 진행률만이 아니라 **페이지별 미리보기 PNG** 도 스트리밍 → Agent 가 사용자에게 실시간 표시 가능

---

## 0.4 우리가 잃는 것 / 얻는 것

### 잃는 것
- **로컬 워크플로 커스터마이즈 자유도** — SKILL.md 를 사용자가 수정해서 자기 스타일을 만드는 것은 불가
- **오프라인 사용** — 인터넷 필수
- **로컬 파일 시스템 통합** — IDE 가 자기 프로젝트 폴더에서 직접 산출물을 보는 것이 불가능. 다운로드 단계 필요.
- **사용자가 직접 LLM 비용 모니터링 컨트롤** — 우리 서버를 거치므로 우리가 어느 정도 가시성을 가짐 (BYOK 라도)

### 얻는 것
- **외부 AI Agent 입장에서 마찰 제로** — MCP URL 한 줄로 끝
- **한국어 폰트/템플릿/아이콘 자산을 서버에 상주** — 사용자가 폰트 설치할 필요 없음
- **개선이 즉시 반영** — 서버 업데이트로 모든 사용자가 새 기능 사용
- **통계/관측** — 어떤 템플릿이 인기인지, 어떤 단계가 실패하는지 운영자 시야
- **확장성** — GPU 인스턴스에서 이미지 생성, 다중 워커로 동시 처리
- **보안** — 사용자 시스템 권한 요구 없음. LLM 이 임의 명령 실행 안 함.

---

## 0.5 비교 — 한 줄 요약

| 측면 | ppt-master | edit2ppt |
|------|-----------|----------|
| 설치 | `git clone + pip install` | 없음 |
| 사용 진입점 | AI IDE 안의 채팅 | MCP URL 또는 HTTP API |
| LLM 위치 | 사용자의 IDE | 서버 (BYOK 키로 호출) |
| 워크플로 정의 | SKILL.md (사용자 LLM이 읽음) | 서버 내부 (사용자에게 비공개) |
| 파일 입출력 | 로컬 디렉토리 | HTTP 업로드/다운로드, presigned URL |
| 상태 저장 | 로컬 디렉토리 트리 | PostgreSQL + Object Storage |
| 멀티 사용자 | N/A (1인) | 테넌트 격리, API 키 |
| AI Agent 통합 | LLM이 SKILL.md 읽음 | MCP 표준 도구 |
| 업데이트 | 사용자 `git pull` | 서버 배포 |
| 한국어 자산 | 없음 (사용자가 추가) | 서버 내장 |
| 파일/코드 이름 | 중국어 디렉토리 자유 사용 | **영문 ASCII 강제** (트랙 A) |
| UI/에러/도구 설명 | 영문/중문 | **한국어+영어 병기** (트랙 B) |

---

## 0.6 이 철학이 다른 문서들에 미치는 영향

| 문서 | 영향 |
|------|------|
| [01-architecture.md](01-architecture.md) | 변경 없음 — ppt-master 내부 구조 분석은 그대로 유효 |
| [02-pipeline.md](02-pipeline.md) | 변경 없음 — 결정적 vs LLM 분리는 그대로 유효 |
| [03-korean-gaps.md](03-korean-gaps.md) | 보강 — 중국어 디렉토리 리네임 항목 추가 (G13) |
| [04-integration-plan.md](04-integration-plan.md) | 대폭 개정 — MCP-first, HTTP I/O, 멀티테넌트, 객체 스토리지 |
| [05-roadmap.md](05-roadmap.md) | 대폭 개정 — 서버 인프라가 M0/M1로 앞당겨짐, "로컬 CLI" 마일스톤은 폐기 |
| [06-bilingual-conventions.md](06-bilingual-conventions.md) | **신규** — 영문 파일 시스템 + 한국어 UX 컨벤션 (이 철학의 구현체) |

---

이 철학을 받아들이면, edit2ppt 는 **"한국어판 ppt-master"** 가 아니라 **"AI Agent 시대의 PPT 생성 인프라"** 가 됩니다. 다음: [04-integration-plan.md](04-integration-plan.md) 를 이 철학에 맞춰 다시 읽어보세요.
