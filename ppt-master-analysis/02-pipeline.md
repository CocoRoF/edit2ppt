# 02. 파이프라인 — Atomic vs LLM 분리

`SKILL.md` 의 7단계 파이프라인을 "결정적 스크립트 (Atomic)" 와 "LLM 역할 (Stochastic)" 로 분해합니다. 이 분류가 [04-integration-plan.md](04-integration-plan.md) 의 Tool/API/MCP 래핑 설계의 기반이 됩니다.

> **A** = Atomic (결정적, LLM 불필요, 그대로 함수/엔드포인트로 래핑 가능)
> **L** = LLM 필요 (프롬프트 + 모델 호출)
> **A→L** = LLM 결정 후 결정적 스크립트 호출

## 2.1 단계별 요약

| Step | 이름 | 유형 | 입력 | 출력 | 주요 스크립트 |
|------|------|------|------|------|---------------|
| 1 | Source → Markdown | **A** | PDF/DOCX/PPTX/Excel/URL | `sources/*.md` | `source_to_md/{pdf,doc,ppt,excel,web}_to_md.py` |
| 2 | Project Init | **A** | 프로젝트명 + 소스 파일 | 프로젝트 디렉토리 트리 | `project_manager.py` |
| 3 | Template Selection | **L** (선택) | 사용자 의도 + 소스 요약 | 템플릿 선택 또는 "Free design" | (LLM 결정만, 스크립트 없음) |
| 4 | Strategist | **L** | 소스 Markdown + 의도 + 템플릿 | `design_spec.md` + `spec_lock.md` | (순수 LLM, 스크립트 호출 없음) |
| 5 | Image Acquisition | **A→L** | spec_lock 의 이미지 계획 | `images/*.png` + `image_sources.json` | `image_gen.py` / `image_search.py` |
| 6 | Executor | **L** | spec_lock + 페이지 콘텐츠 + 이미지 | `svg_output/page_*.svg` | (LLM이 SVG 직접 작성) |
| 7 | Quality + Export | **A** | `svg_output/` | `.pptx` 파일들 | `svg_quality_checker.py` → `finalize_svg.py` → `svg_to_pptx.py` |

## 2.2 Atomic 스크립트 카탈로그 (래핑 대상)

### 2.2.1 Source → Markdown (`skills/ppt-master/scripts/source_to_md/`)

| 스크립트 | 입력 | 출력 | CLI 예시 |
|----------|------|------|----------|
| `pdf_to_md.py` | PDF 파일 | Markdown (heading 자동 추출) | `python3 pdf_to_md.py report.pdf` |
| `doc_to_md.py` | DOCX/EPUB/HTML/IPYNB (native), DOC/ODT/RTF/TEX (pandoc) | Markdown | `python3 doc_to_md.py doc.docx` |
| `ppt_to_md.py` | PPTX | Markdown (텍스트 + 구조) | `python3 ppt_to_md.py deck.pptx` |
| `excel_to_md.py` | XLSX/XLSM | Markdown 테이블 | `python3 excel_to_md.py data.xlsx` |
| `web_to_md.py` | URL | Markdown (위챗 mp.weixin.qq.com 특수 처리) | `python3 web_to_md.py https://...` |

전부 stateless. 단일 파일 입력 → 단일 파일 출력. **함수 시그니처로 추상화 쉬움**:
```python
def pdf_to_markdown(pdf_bytes: bytes) -> str: ...
def web_to_markdown(url: str) -> str: ...
```

### 2.2.2 Project Management (`scripts/project_manager.py`)

| 서브커맨드 | 목적 |
|-----------|------|
| `init <name> [--format ppt169]` | 프로젝트 디렉토리 트리 생성 |
| `import-sources <path> <files...> [--move]` | 소스 파일을 `sources/` 로 이동 |
| `validate <path>` | 프로젝트 구조 검증 |
| `info <path>` | 메타 정보 출력 |

상태가 파일시스템에 있음. API 래핑 시 "프로젝트" 개념을 어떻게 다룰지 결정 필요 (워크스페이스 디렉토리 vs DB).

### 2.2.3 Image Generation / Search (`scripts/image_gen.py`, `image_search.py`)

이미지 생성 백엔드 16종 (`image_backends/`):
Gemini, OpenAI, MiniMax, Stability, FLUX/BFL, Ideogram, Qwen, Zhipu, Volcengine, ModelScope, SiliconFlow, FAL, Replicate, OpenRouter 등

```bash
python3 image_gen.py "prompt" --aspect_ratio 16:9 --image_size 1K -o images/ --provider openai
python3 image_search.py "query" --provider pexels --num_results 5
```

이것도 stateless. 단 **백엔드별 API 키**가 필요 (환경 변수 또는 `.env`).

부가 스크립트:
- `analyze_images.py` — 업로드된 이미지의 width/height/aspect_ratio 추출 (JSON)
- `rotate_images.py` — EXIF 기반 자동 회전
- `gemini_watermark_remover.py` — Gemini 생성 이미지 워터마크 제거

### 2.2.4 SVG Quality / Conversion (`scripts/svg_*.py`, `finalize_svg.py`)

**가장 중요한 결정적 모듈** — LLM이 만든 SVG를 진짜 PPTX로 변환:

| 스크립트 | 목적 |
|----------|------|
| `svg_quality_checker.py` | viewBox / 폰트 / spec drift / 애니메이션 검증. 에러 있으면 export 차단 |
| `update_spec.py` | spec_lock 변경 시 모든 SVG 의 색상/폰트 일괄 갱신 |
| `total_md_split.py` | `notes/total.md` 를 슬라이드별 `notes/slide_*.md` 로 분할 |
| `finalize_svg.py` | `svg_output/` → `svg_final/` 후처리 (아이콘 임베드, 이미지 정렬, tspan 평탄화, rect→path 변환) |
| `svg_to_pptx.py` | `svg_final/` 또는 `svg_output/` → `.pptx` (DrawingML 네이티브) |

내부 구조 (`scripts/svg_to_pptx/` 18개 서브모듈):
- `drawingml_converter.py` — 메인 SVG → DrawingML 변환 엔진
- `drawingml_elements.py` — DrawingML XML 빌더들 (**여기 `lang="zh-CN"` 하드코딩 있음, [03-korean-gaps.md](03-korean-gaps.md))**
- `drawingml_utils.py` — 폰트 파싱, CJK 감지 (**`is_cjk_char()` Hangul 미포함**)
- `drawingml_paths.py`, `drawingml_styles.py` — Path / 색상 처리
- `pptx_builder.py` — PPTX ZIP 패키징
- `pptx_notes.py` — Speaker notes XML (**여기도 `lang="zh-CN"` 하드코딩**)
- `animation_config.py` — 애니메이션 상태 머신
- 등...

후처리 (`scripts/svg_finalize/` 7개 서브모듈):
- `embed_icons.py`, `embed_images.py`, `crop_images.py`, `align_embed_images.py`
- `flatten_tspan.py`, `svg_rect_to_path.py`, `fix_image_aspect.py`

역방향 (`scripts/pptx_to_svg/` 14개 서브모듈) — PPTX를 SVG로 역변환. 템플릿 임포트 시 사용.

### 2.2.5 Audio Narration (`scripts/notes_to_audio.py`)

TTS 백엔드 5종 (`tts_backends/`):
- `backend_edge.py` — Microsoft Edge TTS (무료, 90+ locale)
- `backend_elevenlabs.py` — ElevenLabs (프리미엄, 음성 복제)
- `backend_minimax.py` — MiniMax (`language_boost` 파라미터)
- `backend_qwen.py` — Alibaba Qwen (`language_type`)
- `backend_cosyvoice.py` — Meituan CosyVoice (`language_hint`, 중/일)

```bash
python3 notes_to_audio.py <project_path> --provider edge --voice ko-KR-SunHiNeural --locale ko-KR
```

`speaker notes` → MP3 → 선택적으로 PPTX에 임베드.

## 2.3 LLM 역할 카탈로그 (정의만 제공, 코드 호출은 없음)

### 2.3.1 Strategist (`references/strategist.md`, 472줄)

- 입력: 소스 Markdown + 사용자 의도
- 출력: `design_spec.md` (사람이 읽기 쉬운 디자인 설명) + `spec_lock.md` (기계 판독용 실행 계약)
- 산출물 구조 11개 섹션 (page list, color tokens, typography, icons, image plan, animation, ...)
- **Eight Confirmations** 게이트: 페이지수/포맷/스타일/스토리/시각 자산/타이포그래피/애니메이션/속도 등 8가지 사용자 확인 후 다음 단계로 진행
- 프롬프트는 중국어/영어 예시 위주. **한국어 예시 없음**.

### 2.3.2 Image_Generator (`references/image-generator.md`, 400줄)

- 입력: spec_lock 의 이미지 계획
- 출력: `image_gen.py` / `image_search.py` 호출
- 결정 로직: AI 생성 vs 웹 검색, 어떤 백엔드, 어떤 프롬프트
- **프롬프트 예시는 영어 위주**. CJK 프롬프트 가이드 없음.

### 2.3.3 Executor (`references/executor-{base,consultant,consultant-top,general}.md`)

- 입력: spec_lock + 이미지 + 페이지별 콘텐츠 요약
- 출력: 페이지당 SVG 파일 1개 (`svg_output/page_N.svg`)
- 페이지 리듬 (anchor / dense / breathing), 그리드, 여백, 폰트 위계 규칙
- 스타일 변종 4개: base (공통) + consultant (McKinsey 스타일) + consultant-top (BCG/Bain) + general

## 2.4 워크플로 7종 (`workflows/*.md`)

LLM 오케스트레이션의 "메뉴"입니다. SKILL.md 가 메인 파이프라인이고, 아래는 부가 시나리오.

| 워크플로 | 트리거 | 역할 |
|----------|--------|------|
| `topic-research.md` | 소스 파일 없이 주제만 제공 | Step 1 앞단에서 웹 자료 수집 |
| `create-template.md` | 사용자 PPTX → 템플릿화 요청 | template-designer 역할로 레이아웃 SVG 라이브러리 생성 |
| `resume-execute.md` | 새 채팅에서 "继续生成 projects/<x>" | Phase B (Step 6+) 만 재실행 |
| `verify-charts.md` | 데이터 차트 포함 덱 | Step 6과 Step 7 사이 차트 좌표 검증 |
| `customize-animations.md` | 사용자 애니메이션 커스터마이즈 요청 | `animations.json` sidecar 편집 |
| `visual-edit.md` | 결과물 미세 수정 ("저기 좀 이상해") | `svg_editor/server.py` Flask 편집기 가동 |
| `generate-audio.md` | 내레이션/비디오 export 요청 | Step 7 후 `notes_to_audio.py` 호출 |

## 2.5 결정적 vs LLM 비율

전체 78개 Python 스크립트는 **거의 모두 결정적**. LLM 의존 부분은 100% 텍스트 파일 (workflows/, references/) 안에 있으며, 외부 LLM(사용자의 IDE AI)이 이를 읽고 행동.

이는 edit2ppt에 매우 유리합니다:
- **결정적 스크립트들은 그대로 Python 함수로 래핑** 가능 — Tool / API / MCP 모두 동일한 함수 위에 얹을 수 있음
- **LLM 역할 부분만 별도 처리** — 우리가 직접 Anthropic SDK 등으로 호출하거나, 외부 AI Agent 가 호출하도록 위임

다음: [03-korean-gaps.md](03-korean-gaps.md) 에서 한국어 지원 누락 지점을 정확한 파일:라인 인용과 함께 정리.
