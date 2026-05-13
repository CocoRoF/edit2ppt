# 04. 호스팅 서버 아키텍처 — MCP-first, HTTP I/O, Multi-tenant

이 문서는 [00-philosophy.md](00-philosophy.md) 의 패러다임 전환 ("로컬 스킬 → 호스팅 엔진") 을 구체 아키텍처로 풀어쓴 것입니다. 핵심: **edit2ppt 는 서버이며, 외부 AI Agent 는 MCP로 접근하고, 파일 입출력은 HTTP/HTTPS 위에서 이루어진다.**

---

## 4.1 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            외부 클라이언트                                  │
│                                                                          │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐   │
│  │ AI Agent          │   │ Web/CLI 클라이언트│   │ 다른 백엔드 서비스 │   │
│  │ (Claude Desktop,  │   │ (직접 호출)        │   │ (사용자 앱)        │   │
│  │  Cursor, 자체)    │   │                  │   │                  │   │
│  └──────┬──────────┘   └──────┬──────────┘   └──────┬──────────┘   │
└─────────┼────────────────────┼────────────────────┼─────────────────────┘
          │ MCP                │ REST               │ REST
          │ (HTTP+SSE)         │ (HTTPS)            │ (HTTPS)
          ▼                    ▼                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          edit2ppt 서버                                    │
│                                                                          │
│  ┌────────────────────┐   ┌────────────────────┐                       │
│  │ MCP Server         │   │ REST API           │   ← 두 인터페이스       │
│  │ (Layer 4)          │   │ (Layer 3)          │      동일 비즈니스 로직 │
│  └────────┬───────────┘   └────────┬───────────┘                       │
│           └──────────────┬─────────┘                                    │
│                          ▼                                              │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Application Service Layer                                       │    │
│  │  - 인증 (API 키), 테넌트 격리, 레이트리밋, 감사 로그              │    │
│  │  - Job 관리: 큐 enqueue, 상태 조회, 스트리밍 진행률              │    │
│  │  - Asset 관리: 업로드, presigned URL, TTL                       │    │
│  └────────────────────────────────┬───────────────────────────────┘    │
│                                   ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Worker (Job Executor)                                           │    │
│  │  - Tool 함수 호출 순서 결정                                       │    │
│  │  - LLM 호출 (Anthropic SDK, BYOK)                                │    │
│  │  - 진행률/미리보기 이벤트 발행                                     │    │
│  └────────────────────────────────┬───────────────────────────────┘    │
│                                   ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Layer 2: Tool Functions (내부, stateless)                       │    │
│  │  convert / strategize / images / execute / quality / export    │    │
│  └────────────────────────────────┬───────────────────────────────┘    │
│                                   ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │ Layer 1: 핵심 엔진 (ppt-master 포크 + 한국어 패치)               │    │
│  │  source_to_md, svg_to_pptx, svg_finalize, image_backends, ...  │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐         │
│  │ PostgreSQL     │   │ Object Storage │   │ Redis          │         │
│  │ - tenants      │   │ - 업로드된 소스  │   │ - job queue    │         │
│  │ - projects     │   │ - 중간 SVG      │   │ - 진행률 pub/sub│         │
│  │ - jobs         │   │ - 결과 PPTX     │   │ - 캐시          │         │
│  │ - api_keys     │   │ (S3/MinIO)     │   │                │         │
│  └────────────────┘   └────────────────┘   └────────────────┘         │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼ (BYOK)
┌──────────────────────────────────────────────────────────────────────────┐
│  외부 LLM / 이미지 / TTS Provider                                          │
│  Anthropic, OpenAI, Gemini, Pexels, Edge-TTS, ...                       │
└──────────────────────────────────────────────────────────────────────────┘
```

핵심:
- **사용자 머신에는 아무것도 설치되지 않음**
- 외부 인터페이스는 MCP (AI Agent용) 와 REST (그 외) **둘 다 동일한 백엔드 위에 얹힘**
- 파일은 **Object Storage** 에 보관, 메타데이터는 PostgreSQL
- 작업은 **비동기 Job** 으로 처리, 진행률은 SSE / WebSocket / MCP progress notification 으로 푸시
- LLM 키는 **BYOK** — 테넌트가 제공

---

## 4.2 Layer 1: 핵심 엔진 (서버 내부, 사용자 비공개)

ppt-master 포크 + 한국어 패치. [02-pipeline.md](02-pipeline.md) 의 결정적 스크립트들과 references/ 프롬프트 텍스트가 여기 상주.

차이점:
- 모든 함수가 **bytes 또는 stream** 입출력 (로컬 경로 X)
- 임시 작업 디렉토리는 컨테이너 안의 tmpfs — 외부 비노출
- references/ 의 프롬프트들은 **클라이언트에게 보이지 않음** (서버 내부 자산)

```
src/edit2ppt/core/
├── source_to_md/        # pdf, doc, ppt, excel, web → markdown (bytes in / str out)
├── svg_to_pptx/         # svg → pptx (str in / bytes out)
├── svg_finalize/
├── image_backends/      # 백엔드 API 호출 어댑터들
├── image_sources/
├── tts_backends/
├── prompts/             # references/ 의 프롬프트들 (언어 모드별 분리)
│   ├── strategist.en.md
│   ├── strategist.ko.md
│   ├── executor_base.en.md
│   ├── executor_base.ko.md
│   ├── image_generator.en.md
│   ├── image_generator.ko.md
│   └── ...
└── templates/           # 레이아웃 / 차트 / 아이콘 — 서버 자산 (모두 영문 키, G13)
    └── layouts/
        ├── academic_defense/
        ├── chongqing_university/   # ← 重庆大学 에서 리네임
        ├── cmb_bank/                # ← 招商银行 에서 리네임
        ├── caar_standard/           # ← 中汽研_常规 에서 리네임
        └── ...
```

[06-bilingual-conventions.md](06-bilingual-conventions.md) 의 트랙 A (영문 파일 시스템) 가 모든 디렉토리 이름에 적용. 중국어 디렉토리/파일 리네임은 [03-korean-gaps.md G13](03-korean-gaps.md) 참조.

---

## 4.2.bis i18n 메시지 카탈로그 + Locale 서비스

사용자 노출 텍스트는 모두 카탈로그를 거침. 코드에 한국어/영어 하드코딩 금지.

```
src/edit2ppt/i18n/
├── __init__.py
├── catalog.py            # MessageCatalog 클래스
├── messages/
│   ├── en.yaml           # 영문 메시지
│   ├── ko.yaml           # 한국어 메시지
│   └── zh.yaml           # (선택) 중국어
└── locales.py            # 지원 locale 목록, fallback 체인
```

`catalog.py` API:
```python
class MessageCatalog:
    def get(self, key: str, locale: str = "ko-KR", **vars) -> str:
        """Render a message: catalog.get("errors.invalid_source_format", "ko-KR", format="rtf")
           → "지원하지 않는 소스 형식입니다: rtf" """

    def get_pair(self, key: str, **vars) -> dict:
        """Return both en and ko: {"en": "...", "ko": "..."}
           Used in API responses where both are needed."""
```

FastAPI 의존성으로 주입:
```python
def get_catalog() -> MessageCatalog: ...
def get_request_locale(accept_language: str = Header("ko-KR")) -> str: ...

@app.exception_handler(BusinessError)
async def biz_error_handler(req, exc, catalog=Depends(get_catalog), locale=Depends(get_request_locale)):
    return JSONResponse(status_code=exc.status_code, content={
        "error": {
            "code": exc.code,                                # 영문 코드 — 안정 식별자
            "message": catalog.get(exc.message_key, locale, **exc.vars),
            "message_en": catalog.get(exc.message_key, "en-US", **exc.vars),
            "details": exc.details,
        }
    })
```

MCP 도구 description 도 catalog 거쳐서 동적 노출 가능 ([06 §6.3.3](06-bilingual-conventions.md)).

---

## 4.3 Layer 2: Tool 함수 (서버 내부, 외부 비노출)

[04-integration-plan.md 이전 버전](#) 의 Tool 함수 설계를 유지하되, 모든 시그니처를 **bytes / stream / asset_id** 기반으로 통일:

```python
# convert.py
class ConvertRequest(BaseModel):
    source_type: Literal["pdf", "docx", "pptx", "xlsx", "url"]
    content: bytes | None = None       # 업로드 바이트
    url: str | None = None
    lang_hint: str = "ko-KR"

class ConvertResponse(BaseModel):
    markdown: str
    metadata: dict
```

```python
# strategize.py
class StrategizeRequest(BaseModel):
    sources_markdown: list[str]
    user_intent: str
    template_name: str | None = None
    target_pages: tuple[int, int] = (8, 12)
    lang: str = "ko-KR"
    model: str = "claude-opus-4-7"
    llm_api_key: str                   # BYOK
```

```python
# export.py
class ExportRequest(BaseModel):
    svgs: list[str]
    speaker_notes: list[str]
    images: dict[str, bytes]
    spec_lock_yaml: str
    animation: dict | None = None
    lang: str = "ko-KR"

class ExportResponse(BaseModel):
    pptx: bytes
    preview_png: list[bytes] | None
```

**원칙**: 파일 경로 인자 절대 없음. 모든 I/O 는 bytes/str. Object Storage 와의 상호작용은 Application Service Layer가 담당.

---

## 4.4 Layer 3: REST API

OpenAPI 3 스펙 자동 생성 (FastAPI 기본). 인증은 모든 엔드포인트에 적용.

### 4.4.1 인증

```
Authorization: Bearer ek_live_<api_key>
```

- API 키는 테넌트 등록 시 발급. 키 자체는 hash해서 DB 저장.
- 키 prefix 로 환경 구분 (`ek_live_`, `ek_test_`)

### 4.4.2 BYOK 키 전달

```
X-Anthropic-API-Key: sk-ant-...
X-OpenAI-API-Key: sk-...
X-Pexels-API-Key: ...
```

또는 테넌트 등록 시 한 번 저장 (암호화) → 요청 시 자동 적용. 헤더로 보내면 헤더가 우선.

### 4.4.3 엔드포인트

**파일 자산**

```
POST   /v1/assets                 # 파일 업로드 (multipart)
                                  # → 201 { asset_id, sha256, size, mime_type }
POST   /v1/assets/presigned       # presigned PUT URL 발급 (큰 파일용)
                                  # → 201 { asset_id, upload_url, expires_at }
GET    /v1/assets/{id}/download   # presigned GET URL 발급
                                  # → 200 { download_url, expires_at }
DELETE /v1/assets/{id}            # 삭제
```

**프로젝트**

```
POST   /v1/projects                          # 프로젝트 생성
POST   /v1/projects/{id}/sources             # 소스 자산 연결 (asset_id 배열)
GET    /v1/projects/{id}                     # 메타 조회
DELETE /v1/projects/{id}                     # 삭제 (자산도 삭제)
```

**Job (비동기 작업)**

```
POST /v1/jobs/generate-deck          # 1샷 PPT 생성 (Strategist + Executor + Export)
POST /v1/jobs/convert                # 소스 → markdown만
POST /v1/jobs/strategize             # Strategist만
POST /v1/jobs/execute                # Executor만 (페이지 병렬)
POST /v1/jobs/export                 # SVG → PPTX
POST /v1/jobs/narrate                # 내레이션
                                     # → 202 Accepted { job_id, status }

GET  /v1/jobs/{id}                   # 상태 조회 (폴링)
GET  /v1/jobs/{id}/events            # SSE 스트리밍 (진행률, 페이지별 미리보기)
GET  /v1/jobs/{id}/result            # 완료된 산출물 메타 (asset_id 들)
DELETE /v1/jobs/{id}                 # 취소
```

**리소스 목록**

```
GET /v1/templates                    # 사용 가능한 레이아웃 템플릿 목록
GET /v1/templates/{name}             # 템플릿 상세 (썸네일 URL 포함)
GET /v1/image-backends               # 이미지 백엔드 목록 + 필요 키 정보
GET /v1/tts-voices?lang=ko-KR        # TTS 음성 목록
```

**관리 (테넌트)**

```
POST /v1/tenants                     # 테넌트 등록 (관리자 키 필요)
POST /v1/tenants/{id}/api-keys       # API 키 발급
GET  /v1/tenants/{id}/usage          # 사용량/비용 집계
PUT  /v1/tenants/{id}/byok           # BYOK 키들 저장 (암호화)
```

### 4.4.4 비동기 작업 흐름

```
클라이언트                      서버                         Worker
    │                            │                            │
    ├─ POST /v1/jobs/generate-deck (sources=[asset_id...])     │
    │                            ├─ Job 생성, DB 저장          │
    │                            ├─ Redis 큐에 enqueue         │
    │  202 + { job_id } ◄────────┤                            │
    │                            │            ◄───────dequeue──┤
    │                            │                            ├─ convert
    │  GET /v1/jobs/{id}/events  │                            │
    ├─ (SSE 연결)               ►│                            │
    │                            │   ◄────event: converting ──┤
    │  ◄─ {"stage":"converting"} ┤                            │
    │                            │   ◄────event: strategizing─┤
    │                            │                            ├─ strategize
    │  ◄─ {"stage":"strategizing",│                           │
    │      "design_spec": ...}   │                            │
    │                            │   ◄─event: page_done idx=1─┤
    │  ◄─ {"stage":"executing",  │                           │
    │      "page":1,"preview_png":"https://.../preview1.png"} │
    │                            │                       ... │
    │                            │   ◄────event: done ────────┤
    │  ◄─ {"stage":"done",       │                            │
    │      "pptx_asset_id":"..."}│                            │
    └─                          ─┴─                          ─┘
```

미리보기 PNG 같은 큰 자산은 **이벤트 본문에 base64 X**, presigned URL 만 포함. 클라이언트가 별도 GET.

---

## 4.5 Layer 4: MCP Server (외부 AI Agent 의 주 진입점)

[Model Context Protocol](https://modelcontextprotocol.io) 위에서 도구들을 노출. **HTTP+SSE 트랜스포트** (원격 MCP 표준) 사용.

### 4.5.1 클라이언트 연결

AI Agent (Claude Desktop, Cursor, 자체 Agent) 가 한 줄 설정:

```json
{
  "mcpServers": {
    "edit2ppt": {
      "url": "https://edit2ppt.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ek_live_..."
      }
    }
  }
}
```

또는 `npx @modelcontextprotocol/inspector` 로 검증 가능. **사용자가 다운로드/설치할 것 없음.**

### 4.5.2 노출 도구

고수준 도구 (대부분의 Agent 가 이것만 사용):

| 도구 | 설명 |
|------|------|
| `edit2ppt.generate_deck` | 소스 파일들 + 의도 → PPTX. 진행률은 progress notification. |
| `edit2ppt.regenerate_page` | 기존 프로젝트의 특정 페이지만 다시 생성 |
| `edit2ppt.list_templates` | 사용 가능한 한국어/영어/중국어 레이아웃 |
| `edit2ppt.list_voices` | TTS 음성 (locale 필터) |

저수준 도구 (세밀 제어용, 선택 사용):

| 도구 | 설명 |
|------|------|
| `edit2ppt.upload_source` | 파일 업로드 → asset_id |
| `edit2ppt.convert_source` | 소스 → markdown |
| `edit2ppt.strategize` | 마크다운 → design_spec + spec_lock |
| `edit2ppt.search_image` / `edit2ppt.generate_image` | 이미지 자산 확보 |
| `edit2ppt.execute_page` | spec_lock + 페이지 콘텐츠 → SVG |
| `edit2ppt.check_quality` | SVG 검증 |
| `edit2ppt.export_pptx` | SVG → PPTX |
| `edit2ppt.narrate` | 내레이션 |

### 4.5.3 파일 입출력 패턴 (MCP 위에서)

MCP 메시지 안에 큰 바이너리를 넣지 않습니다. 패턴:

1. **클라이언트가 우리 서버에 파일 업로드**:
   - Agent 가 사용자 파일을 본 다음, MCP 도구 `edit2ppt.upload_source` 호출
   - 인자: `{ filename, content_base64 }` (작은 파일) 또는 사전에 받은 presigned URL 사용
   - 반환: `{ asset_id, sha256 }`

2. **이후 도구 호출은 `asset_id` 만 전달**:
   - `edit2ppt.generate_deck({ sources: [asset_id_1, asset_id_2], intent: "..." })`

3. **결과물 다운로드**:
   - 완료 시 도구 반환에 `{ pptx_download_url: "https://...", expires_at: "..." }`
   - Agent 가 이 URL 을 사용자에게 전달하거나 직접 다운로드

### 4.5.4 Progress Notification

MCP spec 2025-03-26+ 의 progress notification 활용:

```
tools/call: edit2ppt.generate_deck
  → 즉시 응답하지 않고 progress 보냄:
     notifications/progress: { progressToken, progress: 0.1, total: 1.0, message: "Converting PDF..." }
     notifications/progress: { progressToken, progress: 0.3, message: "Strategizing..." }
     notifications/progress: { progressToken, progress: 0.6, message: "Executing page 3/10..." }
     ...
  → 최종 응답:
     { content: [{ type: "text", text: "Generated 10-page PPT." },
                 { type: "resource", uri: "https://.../jobs/.../result.pptx" }] }
```

페이지별 미리보기는 별도 도구 (`edit2ppt.get_page_preview`) 또는 SSE 이벤트로 노출.

### 4.5.5 도구 정의 예시

```python
@mcp_server.tool()
async def generate_deck(
    sources: list[str],            # asset_ids
    user_intent: str,
    target_pages: tuple[int, int] = (8, 12),
    template: str | None = None,
    style: Literal["general", "consultant", "consultant-top"] = "general",
    lang: str = "ko-KR",
    narrate: bool = False,
    *,
    ctx: Context,                  # MCP context (progress, etc.)
) -> dict:
    """
    Generate an editable PPTX from source documents.

    Sources must be uploaded first via `edit2ppt.upload_source` and referenced by asset_id.
    Returns a download URL for the resulting PPTX, plus per-page preview URLs.
    Supports Korean (ko-KR), English (en-US), Chinese (zh-CN).
    """
    job = await job_service.create_generate_deck_job(
        tenant=ctx.tenant,
        sources=sources,
        intent=user_intent,
        target_pages=target_pages,
        template=template,
        style=style,
        lang=lang,
        narrate=narrate,
    )

    # 비동기 작업 진행하며 progress notification 발행
    async for event in job_service.stream_events(job.id):
        if event.type == "progress":
            await ctx.report_progress(event.progress, event.total, event.message)
        elif event.type == "page_done":
            await ctx.log(f"Page {event.page_index} done: {event.preview_url}")

    result = await job_service.get_result(job.id)
    return {
        "pptx_url": result.pptx_url,        # presigned download URL
        "expires_at": result.expires_at,
        "page_count": result.page_count,
        "previews": result.preview_urls,    # 페이지별 PNG presigned URLs
        "spec_lock": result.spec_lock,      # 재현용
    }
```

---

## 4.6 데이터 모델 (PostgreSQL)

```
tenants
  id (uuid pk)
  name
  email
  status (active/suspended)
  created_at
  byok_encrypted_jsonb            # 암호화된 BYOK 키들 {anthropic, openai, pexels, ...}

api_keys
  id (uuid pk)
  tenant_id (fk)
  key_prefix (예: "ek_live_abc123")
  key_hash (bcrypt of full key)
  name (사용자 지정)
  created_at
  last_used_at
  revoked_at

projects
  id (uuid pk)
  tenant_id (fk)
  name
  lang (default "ko-KR")
  template_name (nullable)
  style
  created_at
  expires_at (TTL)

assets
  id (uuid pk)
  tenant_id (fk)
  project_id (fk, nullable)
  kind (source / markdown / spec_lock / svg / image / pptx / audio / preview)
  original_filename                # 사용자 업로드 시의 원본 파일명 (한글 OK, 예: "보고서.pdf")
  storage_key                      # 객체 스토리지 키 (영문 ASCII만, 예: "tenants/<tid>/sources/<ulid>.pdf")
  mime_type
  size
  sha256
  created_at
  expires_at
  # 다운로드 시 Content-Disposition 으로 original_filename 을 RFC 5987 인코딩하여 제공
  # → 사용자는 한글 파일명으로 다운받음, 서버 내부는 영문 키로 운용

jobs
  id (uuid pk)
  tenant_id (fk)
  project_id (fk)
  kind (generate_deck / convert / strategize / execute / export / narrate)
  status (queued / running / done / failed / cancelled)
  params_jsonb
  result_jsonb
  cost_jsonb                      # 토큰, 이미지, TTS 비용 추적
  created_at
  started_at
  finished_at
  error_message (nullable)

job_events
  id (uuid pk)
  job_id (fk)
  type (progress / page_done / log / error)
  payload_jsonb
  created_at
```

---

## 4.7 Object Storage 레이아웃

S3 호환 (AWS S3, MinIO, Cloudflare R2 등). **모든 키는 영문 ASCII** ([06-bilingual-conventions.md §6.2.5](06-bilingual-conventions.md)):

```
bucket: edit2ppt-prod
  tenants/<tenant_id>/
    sources/
      <asset_id>.<ext>             # 객체 키는 ULID/UUID, 한글 X
                                    # 원본 한글 파일명은 DB.assets.original_filename 에만 저장
    projects/<project_id>/
      markdown/<asset_id>.md
      spec_lock/<asset_id>.yaml
      svg/<asset_id>.svg
      images/<asset_id>.<ext>
      previews/<asset_id>.png
      audio/<asset_id>.mp3
      pptx/<asset_id>.pptx
```

**한글 파일명 다운로드 처리**:
```python
@app.get("/v1/assets/{asset_id}/download")
async def download(asset_id: str, expires_in: int = 300):
    asset = await get_asset(asset_id)
    presigned_url = storage.presigned_get_url(
        key=asset.storage_key,
        expires_in=expires_in,
        # S3 ResponseContentDisposition 으로 다운로드 시 헤더 주입
        response_content_disposition=format_content_disposition(asset.original_filename),
    )
    return {"download_url": presigned_url, "expires_at": ..., "filename": asset.original_filename}

def format_content_disposition(filename: str) -> str:
    """RFC 5987 인코딩으로 한글 파일명 안전 전달."""
    import urllib.parse
    quoted = urllib.parse.quote(filename, safe="")
    # ASCII fallback + UTF-8 정식
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'
```

브라우저/curl/Agent 가 다운로드 받을 때 한글 파일명으로 저장됨.

수명 정책:
- 업로드 소스: 기본 7일 TTL (테넌트 설정 가능, 최대 90일)
- 중간 산출물 (svg/markdown/spec_lock): 30일
- 최종 PPTX: 90일 (또는 다운로드 후 자동 삭제 옵션)
- preview PNG: 30일

---

## 4.8 Worker / Job Queue

선택지:
| 옵션 | 장점 | 단점 |
|------|------|------|
| **arq** (asyncio, Redis) | 가볍고 FastAPI와 잘 맞음 | 운영 도구 적음 |
| **Celery** (Redis/RabbitMQ) | 검증된 운영성, 모니터링 풍부 | sync 기반, asyncio와 마찰 |
| **Dramatiq** | 미들웨어 구조 좋음 | 한국어 자료 적음 |
| **Temporal** | 워크플로 엔진, 복잡한 의존성 처리 | 인프라 무거움 |

**권장 시작**: `arq` — FastAPI/asyncio 친화적, 우리 작업 수준에는 충분.

워커 컨테이너는 API 서버와 분리:
- API 서버: 가벼움, 인증/큐잉/SSE 만 담당. 수십 ~ 수백 동시 연결.
- 워커: LLM/이미지 생성 호출. CPU/메모리 많이 씀. 동시 처리 수는 LLM 동시성 제한이 결정.

스케일링:
- API 서버: HPA 로 트래픽 따라
- 워커: 큐 길이 따라

---

## 4.9 LLM 오케스트레이션 (Worker 안에서)

원본 ppt-master 는 사용자의 LLM이 SKILL.md 를 따라가며 7단계 직접 실행. edit2ppt 서버는 **이 7단계를 우리 워커가 코드로 오케스트레이션**:

```python
# worker/generate_deck.py
async def execute_generate_deck_job(job: Job, byok: BYOKKeys):
    # Stage 1: Convert
    await emit_progress(job, 0.05, "Converting sources to markdown")
    markdowns = []
    for source_asset in job.source_assets:
        content = await storage.get_bytes(source_asset.storage_path)
        md = await convert_to_markdown(ConvertRequest(
            source_type=source_asset.kind,
            content=content,
            lang_hint=job.params.lang,
        ))
        markdowns.append(md.markdown)

    # Stage 2: Strategize (LLM 호출, BYOK)
    await emit_progress(job, 0.15, "Strategizing design")
    spec = await llm.strategize(StrategizeRequest(
        sources_markdown=markdowns,
        user_intent=job.params.user_intent,
        template_name=job.params.template,
        lang=job.params.lang,
        llm_api_key=byok.anthropic,
    ))
    spec_lock_asset = await storage.put_yaml(spec.spec_lock_yaml, ...)

    # Stage 3: Images (병렬)
    await emit_progress(job, 0.30, "Acquiring images")
    image_assets = await asyncio.gather(*[
        acquire_image(plan, byok) for plan in spec.image_plan
    ])

    # Stage 4: Executor (페이지 병렬, LLM 호출)
    await emit_progress(job, 0.45, "Executing pages")
    pages = []
    async def exec_page(idx, page_plan):
        result = await llm.execute_page(ExecutePageRequest(
            spec_lock_yaml=spec.spec_lock_yaml,
            page_index=idx,
            page_summary=page_plan,
            images_b64={...},
            style=job.params.style,
            lang=job.params.lang,
            llm_api_key=byok.anthropic,
        ))
        # 페이지 미리보기 PNG 생성 후 storage 저장 + 이벤트
        preview_asset = await render_preview(result.svg)
        await emit_event(job, "page_done", page=idx, preview_url=preview_asset.url)
        return result

    pages = await asyncio.gather(*[exec_page(i, p) for i, p in enumerate(spec.page_plan)])

    # Stage 5: Quality check
    await emit_progress(job, 0.85, "Checking quality")
    issues = check_svg_quality(...)
    if any(i.severity == "error" for i in issues):
        raise JobFailed(...)

    # Stage 6: Export
    await emit_progress(job, 0.95, "Exporting PPTX")
    export = export_pptx(ExportRequest(
        svgs=[p.svg for p in pages],
        speaker_notes=[p.speaker_notes for p in pages],
        images=images,
        spec_lock_yaml=spec.spec_lock_yaml,
        lang=job.params.lang,
    ))
    pptx_asset = await storage.put_bytes(export.pptx, kind="pptx", ...)

    # Stage 7: (선택) Narration
    if job.params.narrate:
        await narrate_and_embed(...)

    await emit_done(job, pptx_asset_id=pptx_asset.id)
```

페이지 병렬 처리, 프롬프트 캐싱 (Anthropic SDK `cache_control`), BYOK 키 격리, 모든 단계의 이벤트 발행이 worker 안에서 일어남.

---

## 4.10 보안

### 4.10.1 BYOK 키 보호

- 테넌트가 저장한 키는 KMS 또는 application-level AES-GCM 으로 암호화
- 메모리에 평문으로 들어가는 시점 최소화 (사용 직전 복호화, 사용 후 즉시 소거)
- 로그에 절대 출력 X (logging filter)

### 4.10.2 멀티 테넌트 격리

- 모든 DB 쿼리는 `tenant_id` 필수 필터
- Object Storage 경로는 `tenants/<id>/` prefix 강제
- 임시 작업 디렉토리는 컨테이너 안에 격리, 작업 후 즉시 삭제
- worker 컨테이너는 테넌트별 격리하지 않음 (비용) — 코드 레벨로 격리. 고보안 고객은 dedicated 인스턴스 옵션 별도.

### 4.10.3 입력 검증

- 업로드 파일 크기 제한 (테넌트 플랜별)
- 파일 형식 화이트리스트 (PDF/DOCX/PPTX/XLSX/이미지 외 거부)
- URL 입력 시 SSRF 방어 (internal IP, metadata endpoints 차단)
- 사용자 LLM 프롬프트에 들어가는 소스 내용은 instruction injection 격리 (구분자, 시스템 프롬프트와 분리)

### 4.10.4 결과물 검증

- 생성된 PPTX 안에 외부 링크 / 매크로 검사
- 생성된 이미지 NSFW 검사 (선택, 백엔드가 이미 처리)

---

## 4.11 관측 (Observability)

- **메트릭**: Prometheus — job latency p50/p99, 단계별 시간, LLM 토큰 사용량, 이미지 생성 수
- **로그**: 구조화 JSON, 모든 요청에 `tenant_id`, `job_id`, `request_id` 태깅. 프롬프트 내용은 hash 만.
- **트레이싱**: OpenTelemetry — API → worker → LLM 호출 전체 흐름
- **알림**: 단계별 실패율 임계치, LLM API 에러율, 큐 적체

---

## 4.12 배포

### 4.12.1 컨테이너 구성

```
docker-compose.yaml (개발)
  - api       (FastAPI + MCP)
  - worker    (Job executor)
  - postgres
  - redis
  - minio     (S3 호환 로컬)
```

운영:
- API + MCP: K8s Deployment (HPA, 3+ replica)
- Worker: K8s Deployment (큐 길이 기반 KEDA scaling)
- PostgreSQL: managed (RDS, Cloud SQL, Supabase 등)
- Redis: managed (ElastiCache, Upstash 등)
- Object Storage: 관리형 S3 / R2

### 4.12.2 도메인 / TLS

- `https://edit2ppt.example.com/v1/...` — REST API
- `https://edit2ppt.example.com/mcp` — MCP HTTP+SSE
- Let's Encrypt 또는 managed TLS

### 4.12.3 환경

- staging / production 분리
- API 키 prefix (`ek_test_` / `ek_live_`) 로 환경 식별
- DB / Storage / Redis 환경별 격리

---

## 4.13 비-AI Agent 사용 시나리오 (보조)

서버는 MCP-first 지만, 다음도 지원:

- **Web UI**: 자체 대시보드 (선택) — 테넌트 가입, API 키 관리, 사용량 확인. PPT 생성 자체는 API 호출.
- **CLI**: `edit2ppt-cli` (Python pip 설치 가능) — REST API를 감싼 얇은 클라이언트. CI 파이프라인에서 PPT 자동 생성 시 사용.
- **Python SDK**: `pip install edit2ppt-client` — 다른 백엔드 서비스가 우리 서버를 라이브러리처럼 호출.

이 셋 다 **로컬에 핵심 엔진은 없음**. 서버 호출 클라이언트일 뿐.

---

## 4.14 ppt-master 와의 호환성

원본 ppt-master 의 로컬 CLI 도 우리 코드 안에서 살아있긴 함 (`src/edit2ppt/core/scripts/*`). 그러나:

- **외부에 노출하지 않음** — 서버 내부 도구로만 사용
- 원본 ppt-master 의 SKILL.md, AGENTS.md, .claude-plugin/ 은 우리 product 정체성과 다르므로 **제거 또는 별도 보존**
- 원본의 `projects/`, `examples/` 같은 디렉토리도 제거 (서버는 디렉토리 기반이 아님)

ppt-master 의 업스트림 업데이트 따라가기 — [05-roadmap.md §R4](05-roadmap.md#위험과-의사결정-포인트) 참조.

---

다음: [05-roadmap.md](05-roadmap.md) — 이 아키텍처를 어떻게 단계별로 만들 것인가.
