# 06. 이중 트랙 컨벤션 — 영문 파일 시스템 + 한국어 완벽 동작

이 문서는 **모든 파일 시스템 식별자는 영문 ASCII** 로 통일하고, **사용자 경험과 콘텐츠는 한국어 우선 다국어** 로 동작하도록 하는 컨벤션을 정의합니다. 모든 마일스톤에 이 원칙이 적용됩니다.

---

## 6.1 핵심 원칙

| 트랙 | 영역 | 언어 | 이유 |
|------|------|------|------|
| **A. 기계** | 파일/디렉토리 이름, 코드 식별자, DB 스키마, API path, 객체 스토리지 키, Git 브랜치/커밋 메시지, 환경변수 | **영문 ASCII만** | 도구 호환성, 인코딩 안전성, 검색 가능성, 자동화 친화성, OS/파일시스템 호환성 |
| **B. 사람** | UI 텍스트, 에러 메시지, MCP 도구 설명, API response message, 프롬프트 system text, 문서 | **한국어 + 영어 병기**, 그 외 추가 가능 | 사용자 경험 |
| **C. 콘텐츠** | 사용자 입력 (소스 문서), 생성된 슬라이드 텍스트, spec_lock 값, 발표자 노트, TTS 음성 | **사용자 언어 그대로** (한국어/영어/중국어/일본어 등) | 결과물의 정체성 |

요약: **"디렉토리 이름은 영문, 화면에 보이는 글자는 한국어, 슬라이드 안의 내용은 사용자 언어"**

---

## 6.2 트랙 A: 기계 식별자 (영문 ASCII)

### 6.2.1 일반 규칙

- ASCII 인쇄 가능 문자만 (0x20-0x7E)
- 공백 금지 → 구분자 사용 (`_` 또는 `-`)
- 길이 64자 이하 권장 (객체 스토리지 키 limit 고려)
- 대소문자 컨벤션은 컨텍스트별로 다름 (아래)

### 6.2.2 파일/디렉토리 이름

**Python 코드 파일**: `snake_case.py`
```
✓ source_to_md.py, drawingml_utils.py, generate_deck.py
✗ sourceToMd.py, drawing-ml-utils.py, generateDeck.py
```

**디렉토리**: `snake_case`
```
✓ src/edit2ppt/svg_to_pptx/
✓ templates/layouts/chongqing_university/
✗ 重庆大学/, src/edit2ppt/svg-to-pptx/
```

**자산 파일** (이미지, 폰트 등): `snake_case` 또는 `kebab-case`, 일관성 유지
```
✓ cqu_logo.png, hero_image_1.jpg
✗ 重庆大学logo.png, Hero Image 1.JPG
```

**문서 파일**: `kebab-case.md` (URL 으로 노출될 수 있어서)
```
✓ design-spec-reference.md, audio-narration.md
✗ design_spec_reference.md (Python 식별자가 아니므로), 设计规范.md
```

### 6.2.3 코드 식별자 (Python)

- 변수/함수: `snake_case`
- 클래스: `PascalCase`
- 상수: `UPPER_SNAKE_CASE`
- 모듈: `snake_case`
- 한글 식별자 금지 (변수명에 한국어 X)

```python
# ✓
DEFAULT_FONT_STACKS = {...}
class StrategizeRequest(BaseModel): ...
def detect_lang(text: str) -> str: ...

# ✗
기본_폰트_스택 = {...}
class 전략수립요청(BaseModel): ...
def 언어_감지(텍스트: str) -> str: ...
```

### 6.2.4 데이터베이스

**테이블/컬럼**: `snake_case`
```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,            -- 사용자 입력 한글 OK (값)
    lang VARCHAR(8) DEFAULT 'ko-KR',
    created_at TIMESTAMPTZ
);
```

값 컬럼에는 한국어/유니코드 자유롭게. 스키마는 영문.

### 6.2.5 객체 스토리지 키

```
✓ tenants/01H.../sources/01H...abc.pdf
✓ tenants/01H.../projects/01H.../pptx/01H...xyz.pptx
✗ tenants/01H.../sources/보고서.pdf  ← 키에 한글 X
```

**사용자 업로드 파일명 처리** (중요):
1. 사용자가 `보고서.pdf` 업로드
2. 서버가 즉시 sanitize: UUID 생성 → 객체 키 `01H...xyz.pdf`
3. 원본 파일명은 `assets.original_filename` 컬럼에 한글 그대로 저장
4. 다운로드 시 `Content-Disposition: attachment; filename*=UTF-8''%EB%B3%B4...` 헤더로 원본 파일명 제공 (RFC 5987 인코딩)
5. 클라이언트는 다운로드 받을 때 원본 한글 파일명으로 저장됨

이 패턴으로 **저장소는 영문, 사용자 경험은 한국어** 가 양립.

### 6.2.6 API path

```
✓ POST /v1/jobs/generate-deck
✓ GET  /v1/templates/{template_id}
✗ POST /v1/작업/덱-생성
```

쿼리 파라미터도 영문:
```
✓ ?lang=ko-KR&page=1
✗ ?언어=한국어&페이지=1
```

JSON 키도 영문 snake_case:
```json
✓ {"user_intent": "Q3 보고", "target_pages": [8, 12], "lang": "ko-KR"}
✗ {"사용자_의도": "Q3 보고", "목표_페이지수": [8, 12]}
```

JSON 의 값은 한글 자유:
```json
{"user_intent": "Q3 영업 결과 임원 보고", "preferred_template_name": "한국 정부 톤"}
```

### 6.2.7 환경 변수

```
✓ DATABASE_URL, S3_BUCKET, ANTHROPIC_API_KEY, EDIT2PPT_DEFAULT_LANG
✗ 데이터베이스_URL, 한국어_기본값
```

### 6.2.8 Git

- 브랜치: `kebab-case`, 영문 (`feature/korean-font-fallback`, `fix/hangul-text-width`)
- 커밋 메시지: 영문 첫줄, 본문은 영문 또는 영문+한국어 병기
  ```
  fix: add Hangul to is_cjk_char range

  Korean characters were incorrectly width-estimated at 0.55× because
  is_cjk_char() omitted the Hangul Syllables range (U+AC00–U+D7A3).

  Refs: ppt-master-analysis/03-korean-gaps.md G1
  ```
- 태그: `v1.0.0`, `release-2026-05-13` (영문 + 숫자)

### 6.2.9 로그 / 메트릭 / 트레이스

- 로그 키/필드: 영문 snake_case (`tenant_id`, `job_id`, `stage`)
- 로그 값은 한국어 자유 (사용자 입력 echo 가능)
- 메트릭 이름: Prometheus 컨벤션 (`edit2ppt_jobs_total{status="done"}`)
- 트레이스 span 이름: 영문 (`generate_deck.strategize`, `executor.page`)

---

## 6.3 트랙 B: 사람용 텍스트 (한국어 + 영어 병기)

### 6.3.1 다국어 메시지 카탈로그

모든 사용자 노출 문자열은 **i18n 키** 로 식별되고, 카탈로그 파일에서 번역 매핑:

```yaml
# src/edit2ppt/i18n/messages.yaml
errors:
  invalid_source_format:
    en: "Unsupported source format: {format}"
    ko: "지원하지 않는 소스 형식입니다: {format}"
  source_too_large:
    en: "Source file exceeds the limit ({size_mb}MB > {limit_mb}MB)"
    ko: "소스 파일이 제한을 초과합니다 ({size_mb}MB > {limit_mb}MB)"
  llm_api_key_missing:
    en: "Anthropic API key is required. Pass it via X-Anthropic-API-Key header or register via /v1/tenants/{id}/byok."
    ko: "Anthropic API 키가 필요합니다. X-Anthropic-API-Key 헤더로 전달하거나 /v1/tenants/{id}/byok 로 등록해주세요."

stages:
  converting:
    en: "Converting sources to markdown"
    ko: "소스를 마크다운으로 변환 중"
  strategizing:
    en: "Designing presentation strategy"
    ko: "발표 전략 설계 중"
  generating_image:
    en: "Generating image: {prompt_short}"
    ko: "이미지 생성 중: {prompt_short}"
  executing_page:
    en: "Generating page {page}/{total}"
    ko: "{page}/{total} 페이지 생성 중"

tools:
  generate_deck:
    description:
      en: "Generate an editable PPTX from source documents. Returns download URL."
      ko: "소스 문서로부터 편집 가능한 PPTX를 생성합니다. 다운로드 URL을 반환합니다."
```

### 6.3.2 API 응답에서 다국어 처리

`Accept-Language` 헤더 또는 명시적 `lang` 쿼리 파라미터 존중:

```http
GET /v1/jobs/abc123 HTTP/1.1
Accept-Language: ko-KR

HTTP/1.1 200 OK
{
  "job_id": "abc123",
  "status": "running",
  "stage": "executing_page",
  "stage_message": "5/10 페이지 생성 중",     ← Accept-Language 따라 ko
  "stage_message_en": "Generating page 5/10", ← 항상 영문 병기 (디버깅/로그용)
  "progress": 0.55
}
```

에러 응답도 동일:
```json
{
  "error": {
    "code": "INVALID_SOURCE_FORMAT",
    "message": "지원하지 않는 소스 형식입니다: rtf",
    "message_en": "Unsupported source format: rtf",
    "details": {"format": "rtf", "allowed": ["pdf", "docx", "pptx", "xlsx"]}
  }
}
```

에러 `code` 는 항상 영문 — 자동화/모니터링에서 안정적인 식별자.

### 6.3.3 MCP 도구 설명

MCP 도구는 단일 description 만 가지지만, 영문 + 한국어 병기 가능:

```python
@mcp_server.tool()
async def generate_deck(...):
    """
    Generate an editable PPTX from source documents.

    소스 문서로부터 편집 가능한 PPTX를 생성합니다.

    Sources must be uploaded first via `edit2ppt.upload_source`.
    소스 파일은 `edit2ppt.upload_source` 를 통해 먼저 업로드해야 합니다.

    Supports Korean (ko-KR), English (en-US), Chinese (zh-CN), Japanese (ja-JP).
    한국어/영어/중국어/일본어를 지원합니다.

    Args:
        sources: Asset IDs from upload_source.
        user_intent: What the deck is for (any language).
        target_pages: [min, max] page count. Default [8, 12].
        lang: BCP-47 locale code. Default "ko-KR".
        ...
    """
```

또는 `lang` 파라미터로 MCP 서버가 동적 description 제공 (MCP spec 진화 따라):

```python
@mcp_server.list_tools()
async def list_tools(ctx) -> list[Tool]:
    locale = ctx.request_locale or "ko-KR"
    catalog = load_messages(locale)
    return [
        Tool(name="generate_deck", description=catalog["tools.generate_deck.description"], ...)
    ]
```

### 6.3.4 시스템 프롬프트 (LLM 호출 시)

LLM 의 system prompt 는 **언어 모드별로 다른 파일** 로 분리:

```
src/edit2ppt/core/prompts/
├── strategist.en.md       # 영어 메인
├── strategist.ko.md       # 한국어 메인
├── strategist.zh.md       # (선택) 중국어 메인
├── executor_base.en.md
├── executor_base.ko.md
├── image_generator.en.md
├── image_generator.ko.md
├── ...
```

`job.params.lang` 에 따라 우선순위로 로드:
1. `prompts/{role}.{lang_2letter}.md` (예: `strategist.ko.md`)
2. fallback: `prompts/{role}.en.md`

system prompt 안에서는 "Output language: ko-KR" 같은 명시적 지시 + 사용자 콘텐츠는 원어 그대로 사용. 영문 system + 한국어 콘텐츠 조합도 가능 (Anthropic 모델은 영문 system 으로도 한국어 잘 생성).

### 6.3.5 문서 파일

- `README.md` — **영문 primary** (해외 사용자 진입점)
- `README.ko.md` — 한국어 (한국 사용자 진입점)
- API/MCP 레퍼런스: 영문 primary + 한국어 번역
- 내부 설계 문서 (`ppt-master-analysis/`): 한국어 (개발팀 작업 언어, 의도된 선택)

---

## 6.4 트랙 C: 콘텐츠 (사용자 언어 그대로)

생성되는 PPT 의 슬라이드 텍스트, 발표자 노트, 차트 라벨 등은 사용자 입력의 언어를 그대로 반영. 한국어 입력 → 한국어 슬라이드, 영어 입력 → 영어 슬라이드, 혼용 입력 → 혼용 슬라이드.

처리 규칙:
- LLM 이 spec_lock / SVG / 발표자 노트 작성 시 **사용자 콘텐츠 언어 보존**
- OOXML `lang` 속성은 [03-korean-gaps.md G2](03-korean-gaps.md) 의 동적 결정으로 처리
- 폰트는 lang 별 기본 스택 + 텍스트에 한국어/중국어/일본어 문자 감지 시 해당 EA font 우선

---

## 6.5 ppt-master 에서 가져와야 할 리네임 작업

### 6.5.1 디렉토리 리네임 (Critical)

`templates/layouts/` 안 중국어 디렉토리 → 영문 키:

| 현재 (중국어) | 영문 키 | 의미 |
|--------------|---------|------|
| `中国电建_常规` | `china_power_construction_standard` | 중국전력건설집단 표준 톤 |
| `中国电建_现代` | `china_power_construction_modern` | 중국전력건설집단 모던 톤 |
| `中汽研_常规` | `caar_standard` | 중국자동차기술연구센터 표준 |
| `中汽研_现代` | `caar_modern` | 중국자동차기술연구센터 모던 |
| `中汽研_商务` | `caar_business` | 중국자동차기술연구센터 비즈니스 |
| `招商银行` | `cmb_bank` | 초상은행 (China Merchants Bank) |
| `重庆大学` | `chongqing_university` | 충칭대학교 |

(edit2ppt 의 1차 사용자는 한국 사용자이므로 이 중국 기관 레이아웃들은 우선순위 낮음. 일단 리네임만 하여 보존하고, M7 에서 한국 컨텍스트 레이아웃으로 대체 검토.)

### 6.5.2 파일 이름 리네임 (Critical)

각 디렉토리 안 중국어 자산:

| 현재 | 영문 |
|------|------|
| `重庆大学logo.png` | `cqu_logo.png` |
| `重庆大学logo2.png` | `cqu_logo_alt.png` |
| `水电三局logo.png` | `hydropower_bureau3_logo.png` |
| `电建logo.png` | `power_construction_logo.png` |
| `中国水务logo.png` | `china_water_logo.png` |
| `华东院logo.png` | `east_china_institute_logo.png` |

스크립트로 일괄 처리 가능 (M1 작업).

### 6.5.3 `layouts_index.json` 키 + 설명 영문화

```json
{
  "cmb_bank": {
    "summary_en": "Transaction banking product intros, sales reports, client case studies, branch training.",
    "summary_ko": "트랜잭션 뱅킹 제품 소개, 세일즈 보고, 고객 사례 분석, 지점 교육 자료.",
    "keywords": ["banking", "transactional", "enterprise", "trust-building"]
  },
  "china_telecom_template": {
    "summary_en": "China Telecom related briefings, government-enterprise digital transformation plans.",
    "summary_ko": "차이나텔레콤 관련 브리핑, 정부-기업 디지털 전환 방안.",
    "keywords": ["authoritative", "structured", "restrained", "enterprise-government"]
  }
}
```

설명 안의 중국어 문구 (`政企数字化方案`) 도 영문/한국어로 번역.

### 6.5.4 examples/ 디렉토리 — 대부분 제거

ppt-master 의 `examples/` 안 14개 디렉토리는 모두 중국어 이름 (`ppt169_高端咨询风_...`). edit2ppt 는 서버 product 이므로 examples/ 자체가 불필요. **모두 제거**.

대신 server-side 에 "데모 프로젝트" 1-2 개를 빌트인하여 API 로 노출 (`GET /v1/demos`) 하는 옵션 검토.

### 6.5.5 ppt-master 의 워크플로/스킬 메타데이터 제거

`SKILL.md`, `AGENTS.md`, `CLAUDE.md`, `.claude-plugin/`, `index.html`, `viewer.html`, `README_CN.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md` 등 — edit2ppt 의 정체성과 무관. M1 에서 정리.

- LICENSE 는 유지 (MIT attribution)
- README.md 는 edit2ppt 용으로 새로 작성

### 6.5.6 docs/ 디렉토리

`docs/zh/` 등 중국어 문서들. 우리 제품 문서는 새로 작성하므로 원본 docs/ 는 **모두 제거**.

---

## 6.6 한국어 입력 처리 (실제 동작)

### 6.6.1 파일 업로드 (한글 파일명)

```http
POST /v1/assets HTTP/1.1
Content-Type: multipart/form-data; boundary=----xxx

------xxx
Content-Disposition: form-data; name="file"; filename="Q3 영업보고서.pdf"
Content-Type: application/pdf

[binary]
```

서버 처리:
1. 파일명을 UTF-8 로 디코딩 (FastAPI 기본 동작 확인)
2. `original_filename = "Q3 영업보고서.pdf"` 그대로 저장 (DB)
3. 안전한 객체 키 생성: `tenants/{tid}/sources/{ulid}.pdf`
4. sha256 계산, 사이즈/MIME 검증

응답:
```json
{
  "asset_id": "asst_01H...",
  "original_filename": "Q3 영업보고서.pdf",
  "size": 1234567,
  "sha256": "...",
  "mime_type": "application/pdf"
}
```

### 6.6.2 한국어 콘텐츠 처리

- PDF → markdown 시 한글 텍스트 보존 (PyMuPDF UTF-8 기본)
- markdown → strategist LLM 호출 시 system은 영문, user message는 한국어 그대로
- 생성된 spec_lock YAML 의 값에 한국어 그대로 (key 는 영문 — `title:`, `subtitle:`)
- SVG `<text>` 노드 안에 한국어 그대로
- PPTX OOXML 의 `<a:t>` 안에 한국어 그대로, `<a:rPr lang="ko-KR">` 동적 설정

### 6.6.3 한국어 결과물 다운로드

```http
GET /v1/assets/asst_xyz/download?expires_in=300
```

응답:
```json
{
  "download_url": "https://storage.edit2ppt.com/.../01H...xyz.pptx?X-Amz-...",
  "expires_at": "2026-05-13T13:00:00Z",
  "filename": "Q3_영업보고서_프레젠테이션.pptx"  ← LLM이 작명한 한글 파일명 (옵션)
}
```

클라이언트가 download_url 로 직접 다운로드 시 Content-Disposition 헤더:
```
Content-Disposition: attachment; filename*=UTF-8''Q3_%EC%98%81%EC%97%85%EB%B3%B4%EA%B3%A0%EC%84%9C_%ED%94%84%EB%A0%88%EC%A0%A0%ED%85%8C%EC%9D%B4%EC%85%98.pptx
```

브라우저/curl 이 한글 파일명으로 저장.

---

## 6.7 폰트 처리 — 시스템 폰트 vs 임베드

원칙 ([03-korean-gaps.md G3](03-korean-gaps.md) 와 연결):

| 시나리오 | 처리 |
|----------|------|
| **기본 (시스템 폰트 참조)** | PPTX 의 `<font>` 태그에 폰트 이름만. 사용자 PC 의 폰트 사용 (Pretendard / Apple SD Gothic Neo / Malgun Gothic 가운데 사용 가능한 것) |
| **OFL 폰트 임베드 (안전)** | Pretendard, Noto Sans KR 등 OFL 라이선스 폰트는 PPTX 안에 임베드 가능 (사용자가 폰트 미설치여도 동작) |
| **시스템 폰트 임베드 (사용자 책임)** | Apple SD Gothic Neo (Apple 소유), Malgun Gothic (Microsoft 소유) 임베드는 사용자가 권한 보유 시에만. 기본 비활성. 옵션으로 노출 + 약관에 책임 명시 |

서버는 `assets/fonts/` 에 OFL 폰트 번들 (Pretendard, Noto Sans KR, Noto Serif KR) 을 호스팅 → 임베드 시 사용.

---

## 6.8 정체성 / 브랜딩에서의 적용

| 항목 | 영문 (primary) | 한국어 |
|------|---------------|--------|
| 제품명 | edit2ppt | 에딧투피피티 (사용자 노출 X, 발음만 참고) |
| 도메인 | edit2ppt.com / edit2ppt.dev / edit2ppt.kr | (동일) |
| 슬로건 | "AI presentation generation, fully editable, Korean-native" | "AI 기반 프레젠테이션 생성, 완전 편집 가능, 한국어 네이티브" |
| MCP 도구 namespace | `edit2ppt.*` | (동일 — 코드 식별자) |
| 환경 변수 prefix | `EDIT2PPT_*` | (동일) |
| Python 패키지명 | `edit2ppt` | (동일) |
| API 키 prefix | `ek_live_*`, `ek_test_*` (edit2ppt key) | (동일) |

---

## 6.9 검증 체크리스트 (M1 작업 항목으로 편입)

- [ ] 코드 디렉토리/파일에 비-ASCII 문자 없음 — `find src -name '*[^[:ascii:]]*'` 결과 0
- [ ] `templates/layouts/` 안 디렉토리 모두 영문
- [ ] `templates/layouts/*/[!.]*.png` 등 자산 파일 모두 영문
- [ ] `layouts_index.json` 키 모두 영문, 설명에 `*_en` + `*_ko` 병기
- [ ] DB 마이그레이션 SQL 검토 — 테이블/컬럼 모두 영문 snake_case
- [ ] API OpenAPI 스펙 검토 — path/query/json key 모두 영문
- [ ] 한글 파일명 업로드 → 원본 파일명 보존 + 안전 다운로드 통합 테스트
- [ ] 한국어 콘텐츠로 generate_deck → 결과 PPTX 의 OOXML 검사 (lang="ko-KR" 확인)
- [ ] 에러 메시지 한국어/영어 병기 응답 확인
- [ ] LLM system prompt 영문/한국어 분리 로드 확인
- [ ] Pretendard 임베드 옵션 → 결과 PPTX 의 fontTable 검사

---

## 6.10 안티 패턴 (하지 말 것)

❌ **코드/파일명 한국어**
```python
def 덱_생성_요청(소스: list[bytes], 의도: str) -> bytes: ...
```

❌ **객체 스토리지 키에 한글**
```
s3://bucket/사용자/abc/소스/보고서.pdf
```

❌ **API JSON 키에 한글**
```json
{"사용자_의도": "...", "목표_페이지수": [8, 12]}
```

❌ **DB 컬럼에 한글**
```sql
CREATE TABLE 프로젝트 (
  아이디 UUID, 이름 TEXT, 생성시각 TIMESTAMPTZ
);
```

❌ **에러 코드에 한글**
```json
{"error": {"code": "잘못된_소스_형식", "message": "..."}}
```

❌ **하드코딩된 한국어 메시지** (i18n 카탈로그 거치지 않음)
```python
raise HTTPException(400, "지원하지 않는 형식입니다")  # message_en 누락, i18n key 없음
```

❌ **OOXML 의 lang 속성 "ko-KR" 하드코딩** (사용자 콘텐츠가 영어/중국어인데 ko-KR 마킹)
```python
xml = f'<a:rPr lang="ko-KR" .../>'  # 동적 결정 필요
```

---

## 6.11 다른 문서와의 연결

| 문서 | 이 컨벤션과의 관계 |
|------|------------------|
| [00-philosophy.md](00-philosophy.md) | "F2 상태 저장소 명시화" 와 정합 — 모든 식별자 영문 |
| [03-korean-gaps.md](03-korean-gaps.md) | G1/G2/G3 패치는 트랙 A/C 정확화의 일부, 트랙 B (i18n 카탈로그) 는 새로 추가 |
| [04-integration-plan.md](04-integration-plan.md) | DB 스키마, API path, 객체 스토리지 키 모두 이 컨벤션 따름 |
| [05-roadmap.md](05-roadmap.md) | M0 에 i18n 카탈로그 인프라, M1 에 디렉토리 리네임 + 한글 파일명 처리 |

---

이 컨벤션을 받아들이면, **모든 자동화 도구가 안전하게 우리 코드/파일을 다룰 수 있고**, **사용자는 한국어로 자연스럽게 사용할 수 있고**, **결과물은 한국어로 완벽하게 동작합니다.**
