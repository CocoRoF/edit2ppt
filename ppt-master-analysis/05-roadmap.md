# 05. 단계별 로드맵 (서버형 edit2ppt)

[00-philosophy.md](00-philosophy.md) (호스팅 엔진 패러다임) + [04-integration-plan.md](04-integration-plan.md) (서버 아키텍처) 를 단계별 마일스톤으로 분해.

핵심 원칙:
- **각 마일스톤은 "동작하는 서버"** 산출 (로컬 CLI 단계는 없음 — 처음부터 서버)
- **인프라가 먼저** — DB, Object Storage, Job Queue 는 M0/M1 에 들어감
- **MCP 노출은 가능한 한 빠르게** — 우리의 정체성

## 마일스톤 한눈에 보기

| M | 이름 | 산출물 | 예상 |
|---|------|--------|------|
| M0 | 서버 골격 + 인프라 + **i18n 카탈로그** | FastAPI + PostgreSQL + MinIO + Redis + arq + i18n + 한글 파일명 안전 처리, 헬스체크 통과 | 1주 |
| M1 | 한국어 Critical 패치 + 핵심 엔진 임포트 + **영문 리네임** | 코어 엔진이 영문 파일 시스템으로 정리되고 G1/G2/G3 한국어 패치 통과 | 1.5-2주 |
| M2 | Layer 2 Tool 함수 + LLM 클라이언트 | Python 코드 1회 호출로 한국어 PPT 생성 (서버 내부, 외부 미노출) | 2주 |
| M3 | Layer 3 REST API + Job 비동기 | curl 로 PPT 생성, SSE 진행률 스트리밍 (한국어 메시지) | 1-2주 |
| M4 | Layer 4 MCP 서버 + 외부 노출 | Claude Desktop / Cursor에서 서버 URL만으로 도구 사용, 도구 설명 ko/en 병기 | 1-2주 |
| M5 | 한국어 High 패치 + 프롬프트/템플릿 | 한국어 결과물 품질 "쓸만함", `prompts/*.ko.md` 분리 | 1-2주 |
| M6 | 운영화 — 인증/멀티테넌트/관측 | 외부 사용자 가입 → API 키 발급 → 결제 (선택) | 2-3주 |
| M7 | 한국 컨텍스트 자산 + 정체성 | 한국식 레이아웃 3-5개, 한국어 문서, 브랜딩 | 2-3주 |

총합 약 **11-15주** (1인 풀타임 기준). 인프라/MCP/한국어/이중 트랙 컨벤션이 동등 우선순위.

---

## M0 — 서버 골격 + 인프라 (1주)

**목표**: 빈 FastAPI 서버가 docker-compose 로 뜨고, 헬스체크 + DB/스토리지 연결 확인 가능.

작업:
- [ ] `pyproject.toml` (uv 권장 — 빠르고 lock 깔끔). 의존성: `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `asyncpg`, `alembic`, `aiobotocore` 또는 `boto3`, `arq`, `redis`, `python-multipart`, `httpx`, `structlog`
- [ ] 디렉토리 구조 ([04-integration-plan.md §4.1](04-integration-plan.md) 참조):
  ```
  src/edit2ppt/
    api/        # FastAPI 라우터
    mcp/        # MCP 서버 (M4 까지 비어있음)
    workers/    # Job executor
    services/   # Application service layer (auth, jobs, assets)
    core/       # Layer 1 - 핵심 엔진 (M1 에 임포트)
    tools/      # Layer 2 - tool 함수들 (M2)
    llm/        # LLM 클라이언트 (M2)
    storage/    # Object Storage 어댑터
    db/         # SQLAlchemy 모델, Alembic 마이그레이션
    config.py
    main.py
  ```
- [ ] **PostgreSQL** 스키마 + Alembic 마이그레이션: tenants, api_keys, projects, assets, jobs, job_events ([04-integration-plan.md §4.6](04-integration-plan.md)) — 모든 컬럼 영문 snake_case ([06 §6.2.4](06-bilingual-conventions.md))
- [ ] **Object Storage 어댑터**: S3 호환 (boto3). 로컬은 MinIO 컨테이너. 인터페이스: `put_bytes`, `get_bytes`, `presigned_put_url`, `presigned_get_url`, `delete`. 키는 영문 ASCII만 ([06 §6.2.5](06-bilingual-conventions.md))
- [ ] **Redis** 연결 + arq 워커 골격 (실제 job은 빈 함수)
- [ ] **docker-compose.yml**: api / worker / postgres / redis / minio
- [ ] **인증 미들웨어**: `Authorization: Bearer ek_...` 검증. 임시로 하드코딩된 키 1개로 시작 (M6에서 본격화)
- [ ] **i18n 카탈로그 인프라** ([06-bilingual-conventions.md §6.3](06-bilingual-conventions.md)):
  - `src/edit2ppt/i18n/messages/en.yaml`, `ko.yaml` 초기 골격
  - `MessageCatalog` 클래스 + FastAPI 의존성 (`get_catalog`, `get_request_locale`)
  - `Accept-Language` 헤더 처리, 기본 `ko-KR`
  - 에러 핸들러에서 `message` (locale 따라) + `message_en` (항상 영문) + `code` (영문 안정 식별자) 응답
- [ ] **한글 파일명 안전 처리** ([06-bilingual-conventions.md §6.6.1](06-bilingual-conventions.md)):
  - 업로드 multipart 의 filename UTF-8 디코딩 확인
  - `assets.original_filename` 컬럼에 한글 그대로, `assets.storage_key` 는 영문 ASCII
  - 다운로드 시 RFC 5987 인코딩 Content-Disposition 헤더 생성 헬퍼
  - 통합 테스트: 한글 파일명 업로드 → 다운로드 시 원본 파일명 보존
- [ ] 헬스체크 엔드포인트: `GET /health` → DB / Redis / Storage 핑
- [ ] `tests/` 골격 + pytest + smoke test
- [ ] **ASCII 식별자 lint 룰**: pre-commit hook 또는 CI 단계에서 `src/` 및 신규 추가 디렉토리/파일에 비-ASCII 문자 0개 검증 (`find src -name '*[^[:ascii:]]*'`)
- [ ] GitHub Actions CI: lint + test + ascii-check

**완료 기준**:
```bash
docker compose up -d
curl http://localhost:8000/health
# {"status":"ok","db":"ok","redis":"ok","storage":"ok"}
```

---

## M1 — 한국어 Critical 패치 + 핵심 엔진 임포트 + 영문 리네임 (1-2주)

**목표**: ppt-master 의 결정적 스크립트들을 `src/edit2ppt/core/` 로 가져오고, [03-korean-gaps.md](03-korean-gaps.md) 의 Critical 항목 (G1/G2/G3/G13) 해결.

### M1.1 임포트 + 정리

- [ ] **임포트**: 원본 `skills/ppt-master/scripts/` → `src/edit2ppt/core/`
- [ ] **임포트**: 원본 `skills/ppt-master/templates/` → `src/edit2ppt/core/templates/`
- [ ] **임포트**: 원본 `skills/ppt-master/references/` → `src/edit2ppt/core/prompts/` (확장자 `.en.md` 로 변경, 한국어 보강은 M5)
- [ ] **제거** (정체성 무관): `SKILL.md`, `AGENTS.md`, `CLAUDE.md`, `.claude-plugin/`, `examples/`, `projects/`, `index.html`, `viewer.html`, `README_CN.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/`, `docs/zh/`
- [ ] **유지**: `LICENSE` (MIT attribution), `requirements.txt` (참고용, 실제는 pyproject.toml로 이전)

### M1.2 G13 — 중국어 디렉토리/파일 영문 리네임 ([03-korean-gaps.md G13](03-korean-gaps.md))

- [ ] **리네임 매핑 dict** 작성: `scripts/migration/rename_chinese_assets.py`
  ```python
  DIR_RENAMES = {
      "中国电建_常规": "china_power_construction_standard",
      "中国电建_现代": "china_power_construction_modern",
      "中汽研_常规": "caar_standard",
      "中汽研_现代": "caar_modern",
      "中汽研_商务": "caar_business",
      "招商银行": "cmb_bank",
      "重庆大学": "chongqing_university",
  }
  FILE_RENAMES = {
      "重庆大学logo.png": "cqu_logo.png",
      "重庆大学logo2.png": "cqu_logo_alt.png",
      "水电三局logo.png": "hydropower_bureau3_logo.png",
      "电建logo.png": "power_construction_logo.png",
      "中国水务logo.png": "china_water_logo.png",
      "华东院logo.png": "east_china_institute_logo.png",
  }
  ```
- [ ] `git mv` 로 디렉토리/파일 리네임 (히스토리 보존)
- [ ] 같은 스크립트가 `layouts_index.json` 의 키도 업데이트, 설명에 `summary_en` + `summary_ko` 병기로 변환
- [ ] 디렉토리/파일 내부에서 자산을 참조하는 SVG / MD / Python 코드의 경로 일괄 갱신 (sed 또는 텍스트 치환 스크립트)
- [ ] 검증: `grep -rIn "中国电建\|中汽研\|招商银行\|重庆大学\|水电三局\|华东院\|电建logo\|中国水务" src/` → 0건
- [ ] CI 의 ascii-check 통과 (`find src -name '*[^[:ascii:]]*'` → 0건)

### M1.3 G1/G2/G3 — 한국어 코어 패치

- [ ] **G1 패치**: `core/svg_to_pptx/drawingml_utils.py:427-433` `is_cjk_char()` 에 Hangul 범위 추가 (U+AC00-U+D7A3, U+1100-U+11FF, U+3130-U+318F, U+A960-U+A97F, U+D7B0-U+D7FF)
- [ ] **G2 패치**: `core/svg_to_pptx/pptx_notes.py:75,80,85` + `drawingml_elements.py:1002` 의 `lang="zh-CN"` → 호출자가 lang 인자 전달 (기본 동적: `detect_lang(text)`)
  - `detect_lang()` 함수 신설: 텍스트 내 Hangul → "ko-KR", CJK 한자 → "zh-CN", Hiragana/Katakana → "ja-JP", 그 외 → "en-US"
  - 명시적 lang 파라미터가 있으면 우선
- [ ] **G3 패치**:
  - `EA_FONTS` 셋 확인 — Malgun Gothic, Gulim, Dotum, Batang 외에 Apple SD Gothic Neo, Pretendard, Noto Sans KR, Noto Serif KR 추가
  - `FONT_FALLBACK_WIN` 매핑에 한국어 폰트 추가 (Apple SD Gothic Neo → Malgun Gothic 등)
  - `core/config.py` 에 `DEFAULT_FONT_STACKS = {"ko-KR": "Pretendard, ...", "en-US": "...", "zh-CN": "...", "ja-JP": "..."}` dict

### M1.4 테스트 + 검증

- [ ] **단위 테스트**:
  - `is_cjk_char("가")` == True, `is_cjk_char("中")` == True, `is_cjk_char("あ")` == True, `is_cjk_char("A")` == False
  - `estimate_text_width("안녕하세요", 24, "400")` ≈ 120 (한국어 5글자 × 24)
  - `detect_lang("안녕하세요")` == "ko-KR", `detect_lang("Hello")` == "en-US", `detect_lang("你好")` == "zh-CN"
  - 한국어 텍스트 → SVG → PPTX 생성 → `lxml.etree` 로 `<a:rPr lang="ko-KR">` 검증
  - 한국어 폰트 fallback: `core/config.py.DEFAULT_FONT_STACKS["ko-KR"]` 가 `Pretendard` 로 시작
- [ ] **회귀 테스트 fixture**: 한국어 마크다운 1개 + 사전 작성된 SVG → svg_to_pptx 직접 호출 → PPTX 산출 → 텍스트 추출 → 깨짐 0건

### 완료 기준

- 핵심 엔진이 `from edit2ppt.core.svg_to_pptx import export_pptx` 로 임포트됨
- 한국어 SVG → PPTX 변환 시 텍스트 폭/lang 속성 모두 정상

---

## M2 — Layer 2 Tool 함수 + LLM 클라이언트 (2주)

**목표**: Worker 안에서 Python 함수 1회 호출로 한국어 PPT 생성. 아직 외부 노출은 없음.

작업:
- [ ] **`tools/convert.py`**: pdf/docx/web → markdown (bytes in/out)
- [ ] **`tools/strategize.py`**: Anthropic SDK 호출, `core/prompts/strategist.md` 적재, prompt caching 적용
- [ ] **`tools/images.py`**: image_gen / image_search 래핑
- [ ] **`tools/execute.py`**: 페이지별 LLM 호출, 병렬 처리, prompt caching
- [ ] **`tools/quality.py`**: svg_quality_checker 래핑
- [ ] **`tools/export.py`**: finalize_svg + svg_to_pptx 래핑 (lang 인자 명시)
- [ ] **`tools/audio.py`**: TTS 래핑
- [ ] **`tools/generate_deck.py`**: 1샷 함수, 모든 단계 통합
- [ ] **`llm/anthropic_client.py`**: BYOK 키 받아서 호출, 프롬프트 캐싱, 오류/재시도
- [ ] **`workers/generate_deck.py`**: arq job — Object Storage에서 소스 읽고 → tools 호출 → 결과 Storage 저장 → job_events 발행
- [ ] **단위 테스트**: 각 tool 함수에 mock LLM
- [ ] **통합 테스트**: 실제 LLM 호출, 작은 마크다운 1개로 E2E (한국어)

**완료 기준**: 다음 파이썬 코드가 docker compose 환경에서 동작.
```python
async with AsyncSession(engine) as db:
    tenant = await register_test_tenant(db, byok={"anthropic": os.environ["ANTHROPIC_API_KEY"]})
    asset = await asset_service.upload(tenant, filename="test.md", content=b"# Test\n...")
    job = await job_service.create_generate_deck_job(
        tenant=tenant, sources=[asset.id],
        user_intent="간단한 테스트 자료", lang="ko-KR", target_pages=(3, 5),
    )
    result = await job_service.wait_for_done(job.id)
    pptx_bytes = await storage.get_bytes(result.pptx_path)
    assert pptx_bytes[:4] == b"PK\x03\x04"  # PPTX는 ZIP
```

---

## M3 — Layer 3 REST API + Job 비동기 (1-2주)

**목표**: HTTP로 외부 노출. curl 로 PPT 생성 가능.

작업:
- [ ] **자산 엔드포인트**: `POST /v1/assets` (multipart), `POST /v1/assets/presigned`, `GET /v1/assets/{id}/download`
- [ ] **프로젝트 엔드포인트**: `POST /v1/projects` + 소스 연결
- [ ] **Job 엔드포인트**:
  - `POST /v1/jobs/generate-deck` → 202 + job_id
  - `GET /v1/jobs/{id}` (폴링)
  - `GET /v1/jobs/{id}/events` (SSE 스트리밍, sse-starlette)
  - `GET /v1/jobs/{id}/result`
- [ ] **세부 단계 엔드포인트**: `convert`, `strategize`, `execute`, `export`, `narrate` (Agent 가 세밀 제어할 때)
- [ ] **리소스 목록**: `GET /v1/templates`, `GET /v1/image-backends`, `GET /v1/tts-voices`
- [ ] **OpenAPI 문서**: FastAPI 자동 생성 + Swagger UI
- [ ] **BYOK 헤더**: `X-Anthropic-API-Key` 등 — 헤더가 우선, 없으면 tenant.byok 사용
- [ ] **에러 처리**: 일관된 에러 응답 (RFC 7807 problem+json)
- [ ] **레이트리밋**: 임시로 in-memory token bucket (M6 에 Redis 기반으로 교체)
- [ ] **클라이언트 SDK 골격**: `httpx` 래퍼, `pip install edit2ppt-client` (M4 후 패키징)
- [ ] **통합 테스트**: pytest-httpx, 실제 LLM E2E 1회

**완료 기준**:
```bash
# 1. 업로드
curl -X POST https://edit2ppt.local/v1/assets \
  -H "Authorization: Bearer ek_test_..." \
  -F "file=@report.pdf"
# → {"asset_id":"asst_abc","sha256":"...","size":...}

# 2. PPT 생성 작업 시작
curl -X POST https://edit2ppt.local/v1/jobs/generate-deck \
  -H "Authorization: Bearer ek_test_..." \
  -H "X-Anthropic-API-Key: sk-ant-..." \
  -H "Content-Type: application/json" \
  -d '{"sources":["asst_abc"],"user_intent":"Q3 보고","lang":"ko-KR"}'
# → {"job_id":"job_xyz","status":"queued"}

# 3. SSE 진행률 스트리밍
curl -N https://edit2ppt.local/v1/jobs/job_xyz/events \
  -H "Authorization: Bearer ek_test_..."
# event: progress
# data: {"stage":"converting","progress":0.05}
# event: page_done
# data: {"page":1,"preview_url":"https://..."}
# ...
# event: done
# data: {"pptx_asset_id":"asst_xyz"}

# 4. 다운로드
curl https://edit2ppt.local/v1/assets/asst_xyz/download
# → {"download_url":"https://...presigned...","expires_at":"..."}
```

---

## M4 — Layer 4 MCP 서버 + 외부 노출 (1-2주)

**목표**: AI Agent 가 MCP URL 한 줄 설정만으로 우리 서버 도구 사용.

작업:
- [ ] **MCP SDK 도입**: `mcp` 공식 Python SDK (`pip install mcp`)
- [ ] **트랜스포트 결정**: HTTP+SSE (원격 MCP 표준). FastAPI 라우트로 노출.
- [ ] **고수준 도구 구현**:
  - `edit2ppt.generate_deck` — Layer 3 의 generate-deck job 을 progress notification 으로 감쌈
  - `edit2ppt.regenerate_page`
  - `edit2ppt.list_templates`
  - `edit2ppt.list_voices`
- [ ] **저수준 도구 구현** (세밀 제어용):
  - `edit2ppt.upload_source` (base64 또는 presigned PUT URL 발급)
  - `edit2ppt.convert_source`, `strategize`, `execute_page`, `export_pptx`, `search_image`, `generate_image`, `narrate`
- [ ] **파일 입출력 패턴**:
  - 작은 파일은 base64 inline
  - 큰 파일은 도구가 presigned upload URL 발급 → Agent 가 별도 PUT → 도구가 asset_id 반환
  - 결과물은 도구 반환에 `resource_link` (presigned GET URL)
- [ ] **Progress notification 적용**: 모든 long-running 도구에서 진행률/페이지별 완료 푸시
- [ ] **MCP Inspector 로 검증**: `npx @modelcontextprotocol/inspector` → 모든 도구 정상 호출
- [ ] **Claude Desktop 실제 등록 테스트**:
  ```json
  {
    "mcpServers": {
      "edit2ppt": {
        "url": "https://edit2ppt.local/mcp",
        "headers": {"Authorization": "Bearer ek_test_..."}
      }
    }
  }
  ```
- [ ] **Cursor / 자체 Agent 도 검증**
- [ ] **도구 설명 한국어 버전 추가** — Agent 가 한국어 사용자 의도를 더 잘 매칭하도록

**완료 기준**:
- Claude Desktop 에 우리 MCP URL 등록 → "이 PDF로 한국어 PPT 만들어줘" → Agent가 자율적으로 우리 도구 호출 → PPTX 다운로드 URL 받음
- MCP Inspector로 모든 도구 정상 응답

---

## M5 — 한국어 High 패치 + 프롬프트/템플릿 (1-2주)

[03-korean-gaps.md](03-korean-gaps.md) 의 G4-G7 (프롬프트 보강) + G9-G11 (TTS 한국어 / 자산).

작업:
- [ ] **`core/prompts/strategist.md`** 한국어 섹션 — 한국어 폰트 추천, 한국 컨텍스트 예시, 한국식 디자인 톤
- [ ] **`core/prompts/strategist_ko.md`** 분리 옵션 — lang="ko-KR" 일 때 우선 로드
- [ ] **`core/prompts/executor_base.md`** 한국어 텍스트 페이지 리듬 / 자간 가이드
- [ ] **`core/prompts/executor_consultant*.md`** 한국 컨설팅 톤 추가
- [ ] **`core/prompts/image_generator.md`** 한국 시각 자산 예시 (한국 사진, 한국 직장 풍경 등) + 백엔드별 한국어/영어 권장사항
- [ ] **`core/prompts/image_searcher.md`** 한글 쿼리 → 자동 영문 번역 가이드
- [ ] **`core/templates/{design_spec,spec_lock}_reference.md`** 한국어 폰트 스택 예시
- [ ] **TTS 음성**: `core/tts_backends/backend_edge.py` 의 추천 voice 리스트에 ko-KR 추가 (ko-KR-SunHiNeural, ko-KR-InJoonNeural)
- [ ] **`/v1/tts-voices?lang=ko-KR`** 응답에 한국어 음성 포함 확인
- [ ] **web_to_md 헤더** 한국어 사이트 친화: `Accept-Language` 동적 결정
- [ ] **결과 품질 평가**: 한국어 입력 5종 (Q3 보고서, 학회 발표, 스타트업 IR, 한국 정부 보고, 컨설팅 자료) 으로 E2E 테스트, 육안 평가
- [ ] **결과 회귀 테스트 fixture 구축** — 향후 프롬프트 수정 시 품질 모니터링

**완료 기준**: 한국어 입력 5종 모두 "이 정도면 쓸만함" 수준의 PPTX 산출.

---

## M6 — 운영화 (2-3주)

**목표**: 외부 사용자 받을 수 있는 상태.

작업:
- [ ] **테넌트 관리**:
  - `POST /v1/tenants` (관리자 키 필요)
  - `POST /v1/tenants/{id}/api-keys`
  - `PUT /v1/tenants/{id}/byok` (암호화 저장)
  - `GET /v1/tenants/{id}/usage` (집계)
  - 임시 자체 가입 페이지 (또는 OAuth 로 GitHub 로그인)
- [ ] **BYOK 암호화**: KMS 또는 AES-GCM with master key in secrets store
- [ ] **레이트리밋**: per-tenant concurrency, per-tenant 일 사용량. Redis 기반.
- [ ] **사용량/비용 추적**: 토큰, 이미지, TTS, 스토리지 사용량을 jobs.cost_jsonb 에 누적
- [ ] **결제** (선택): Stripe 통합. 또는 BYOK 만 지원하면 결제 없이 운영 가능
- [ ] **관측**: OpenTelemetry, Prometheus 메트릭, structlog JSON 로그
- [ ] **알림**: 단계별 실패율, LLM 에러율, 큐 적체 (예: Slack webhook)
- [ ] **보안 감사 체크리스트**:
  - 입력 검증 (파일 크기/형식, URL SSRF, prompt injection)
  - 멀티 테넌트 격리 검증 (다른 테넌트 자산 접근 시도 → 403)
  - 비밀 로깅 X (구조화 로그 필터)
  - HTTPS 강제, HSTS
  - 결과물 외부 링크/매크로 검사
- [ ] **부하 테스트**: 동시 10 jobs 안정성
- [ ] **백업**: PostgreSQL 자동 백업, S3 versioning
- [ ] **상태 페이지** (선택): UptimeRobot / Statuspage
- [ ] **이용 약관 / 개인정보처리방침** (한국 시장 진출 시 필수)

**완료 기준**: 외부 사용자가 가입 → API 키 발급 → BYOK 등록 → MCP 또는 curl 로 PPT 생성 → 결과 다운로드 완전 자동.

---

## M7 — 한국 컨텍스트 자산 + 정체성 (2-3주)

[03-korean-gaps.md](03-korean-gaps.md) 의 G8 (한국 레이아웃) + G12 (한국어 문서) + 브랜딩.

작업:
- [ ] **한국 정부/공공 톤 레이아웃 1개** — 푸른색 계열, 정자체, 정부24/청와대 발표 톤
- [ ] **한국 대학/학술 발표 톤 1개** — 학회 슬라이드 톤
- [ ] **한국 스타트업/IT 톤 1개** — Pretendard 폰트, 모던, 토스/배민/카카오 톤
- [ ] **한국 기업 보고서 톤 1개** — 분기/연간 보고서, 삼성/현대 IR 자료 톤
- [ ] **한국 컨설팅 톤 1개** — 빨강/네이비, 베인&컴퍼니 한국, 삼정KPMG 톤
- [ ] 각 레이아웃마다 4-6개 페이지 패턴 (커버, 목차, 콘텐츠, 차트, 클로징)
- [ ] **`templates_index` API**: 템플릿 썸네일 + 메타데이터 노출
- [ ] **공개 README.md** (영어 + 한국어 README_KO.md) — edit2ppt 정체성:
  - "ppt-master 기반의 한국어 네이티브 PPT 생성 인프라"
  - MCP 한 줄 설정 사용법
  - REST API 예시
  - BYOK 정책
- [ ] **개발자 문서** — 자체 사이트 또는 GitHub Pages: API 레퍼런스, MCP 도구 카탈로그, 한국어 가이드
- [ ] **공개 도메인 등록** + TLS
- [ ] **랜딩 페이지** (선택) — 가입 유도

**완료 기준**: 외부에서 https://edit2ppt.kr (또는 도메인) 로 접근, 가입 → 사용 → 한국식 PPT 1분 안에 받음.

---

## 위험과 의사결정 포인트

### R1. LLM 비용 — BYOK 정책으로 완전 회피

**위험**: PPT 한 개 생성에 $0.5 ~ $5 토큰 비용. 우리가 부담 시 마진 적자 가능.

**대응 (강력 권장)**: **100% BYOK 정책** — 사용자가 자기 Anthropic / OpenAI 키를 등록. 우리는 인프라 비용만 부담. 비용 청구 시스템 없이 운영 가능.

대안: 우리가 LLM 키 보유 + 사용량 기반 과금. 운영 복잡도 ↑.

### R2. SVG → PPTX 변환 불완전성

**위험**: 원본 ppt-master 가 모든 SVG 케이스를 완벽 변환하지 않음. filter, gradient, 복잡한 path 등 누락 가능.

**대응**:
- 회귀 테스트 fixture 누적 (M5 부터)
- 변환 실패 시 cairosvg fallback (PNG embed, 편집 불가지만 시각 보존) 옵션
- shared-standards.md 의 SVG 제약을 Executor 프롬프트에 강하게 명시

### R3. 한국어 폰트 라이선스

**위험**: Pretendard (OFL), Noto Sans KR (OFL) 은 OK. Apple SD Gothic Neo는 Apple 시스템 폰트, Malgun Gothic은 Windows 시스템 폰트.

**대응**:
- 기본은 폰트 임베드 X — PPTX 의 `<font>` 태그에 이름만. 사용자 PowerPoint 가 시스템 폰트 사용.
- embed-fonts 옵션 노출 시 OFL 폰트 (Pretendard, Noto Sans KR) 만 자동 임베드
- 시스템 폰트 임베드는 사용자 책임 명시 (이용 약관)

### R4. 원본 ppt-master 업스트림 변경 따라가기

**위험**: 원본이 활발히 업데이트 (최근 커밋 2026-05-13 직전: "fix(ppt): preserve round2SameRect SVG round-trip"). 우리 패치와 충돌 가능.

**대응**:
- 한국어 패치는 가능한 한 **별도 모듈** 로 분리. 원본 파일은 최소 수정.
- `git remote add upstream` + 주기적 cherry-pick 또는 merge
- 우리 정체성이 갈라지면 fork-divergence accept

### R5. 라이선스/저작자 표시 (MIT)

**위험**: MIT 는 attribution 필수.

**대응**:
- LICENSE 파일에 ppt-master 의 copyright + 우리 copyright 병기
- README 및 우리 서비스 푸터에 "Built on ppt-master by Hugo He (MIT licensed)" 명시
- ppt-master 가 사용한 SVG Repo, Tabler Icons 등 acknowledgments 도 유지

### R6. MCP 트랜스포트 안정성

**위험**: MCP spec 진화 중 (2024-11-05 → 2025-03-26 → ?). 트랜스포트, progress notification 등이 변경될 수 있음.

**대응**:
- 공식 SDK 추종 (mcp Python SDK)
- 트랜스포트는 stdio 도 보조로 지원 (로컬 테스트용)
- 클라이언트 호환성 매트릭스 문서화 (어느 Agent에서 검증되었는지)

### R7. 멀티 사용자 동시성

**위험**: 한 워커가 1 PPT 생성에 수 분 점유. 동시 사용자 많아지면 큐 적체.

**대응**:
- 워커 KEDA 스케일링 (큐 길이 기반)
- 페이지별 병렬 실행으로 단일 job 시간 단축
- LLM provider 의 동시성 제한이 결국 병목. 사용자가 자기 키 사용 (BYOK) 하면 자기 quota 만 소진 → 우리 quota 와 분리.

### R8. 결과 자산 보관 비용

**위험**: 한 PPT = 수십 MB (PPTX) + 페이지별 SVG/PNG + 이미지. 사용자 많아지면 스토리지 비용.

**대응**:
- 기본 TTL 30일, 사용자가 다운로드 후 자동 삭제 옵션
- 콜드 스토리지 티어 (S3 Glacier, R2 archive) 활용
- 사용자 플랜별 스토리지 한도

---

## 즉시 결정이 필요한 사항

다음 결정들이 정해지면 M0 부터 시작 가능합니다:

| # | 결정 항목 | 추천 |
|---|----------|------|
| D1 | 패키지 매니저 | `uv` — lock 빠르고 깔끔 |
| D2 | DB | PostgreSQL (managed: Supabase 또는 RDS) |
| D3 | Object Storage | S3 호환 (R2 / S3 / MinIO 로컬) |
| D4 | Job 큐 | arq (asyncio + Redis) |
| D5 | MCP SDK | 공식 `mcp` Python SDK |
| D6 | LLM Provider | Anthropic 우선, 추후 LiteLLM 으로 확장 |
| D7 | BYOK 정책 | 100% BYOK — 우리는 LLM 비용 안 부담 |
| D8 | 호스팅 | 초기: Fly.io / Railway / Cloud Run. 운영: K8s (GKE/EKS) |
| D9 | 도메인 | edit2ppt.kr 또는 edit2ppt.com — TBD |
| D10 | 라이선스 | MIT (원본과 동일, attribution 유지) |
| D11 | 정체성 | "AI Agent 시대의 PPT 생성 인프라, 한국어 네이티브" |
| D12 | 1차 사용자 | 자체 Dogfooding → 베타 사용자 → 공개 |

이 결정들이 정해지면 M0 (1주) 으로 본격 시작합니다.
