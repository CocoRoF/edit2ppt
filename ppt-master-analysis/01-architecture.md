# 01. ppt-master 아키텍처

## 1.1 한 줄 요약

**Markdown/문서 → SVG 중간 표현 → OOXML 네이티브 PPTX** 로 변환하는 파이프라인. 변환의 "예술적" 부분(레이아웃, 디자인 선택, 텍스트 작성)은 LLM이 담당하고, "기계적" 부분(파일 파싱, SVG 생성 후 OOXML 변환)은 결정적 Python 스크립트가 담당합니다.

## 1.2 디렉토리 구조

ppt-master 원본 (`/home/prj-doc/ppt-master/`):

```
ppt-master/
├── CLAUDE.md                # AI IDE 진입점 (skills/ppt-master/SKILL.md를 읽으라고 지시)
├── AGENTS.md                # 동일 (Cursor/Codex/기타 AI 에이전트용)
├── README.md / README_CN.md
├── requirements.txt
├── index.html / viewer.html # GitHub Pages 데모
├── .claude-plugin/          # Claude Code 플러그인 마켓플레이스 메타데이터
├── docs/                    # 사용자 문서 (faq, technical-design, templates-guide 등)
├── examples/                # 22개 예시 프로젝트, 309페이지
├── projects/                # 사용자 작업 공간 (비어있음)
└── skills/
    └── ppt-master/          # ← 실제 스킬 패키지
        ├── SKILL.md         # 오케스트레이션의 최종 권위 문서 (27.8 KB)
        ├── requirements.txt
        ├── workflows/       # 7개 독립 워크플로 (.md)
        ├── references/      # 13개 역할 정의 + 기술 사양 (.md)
        ├── scripts/         # 78개 Python 스크립트
        └── templates/       # 레이아웃 19개, 차트 70+, 아이콘 5종
```

`skills/ppt-master/` 안이 실제 본체. 나머지는 마케팅/문서/예시.

## 1.3 오케스트레이션 모델: "LLM이 워크플로를 읽고 스크립트를 호출"

기존 모델은 다음과 같이 동작합니다.

```
사용자: "이 PDF로 PPT 만들어줘" (Claude Code 채팅창)
   ↓
Claude(LLM) ← CLAUDE.md → "skills/ppt-master/SKILL.md를 읽어라"
   ↓
Claude가 SKILL.md(27.8 KB 프롬프트)를 컨텍스트에 적재
   ↓
SKILL.md의 Step 1 지시대로 bash 도구로 pdf_to_md.py 호출
   ↓
SKILL.md의 Step 2~7 따라 project_manager.py, image_gen.py, ...
중간에 Strategist 역할 프롬프트(references/strategist.md) 적재 → 디자인 결정
   ↓
최종적으로 svg_to_pptx.py 호출 → .pptx 파일 산출
```

**중요한 함의**:
- **LLM은 외부 의존성이 아니라 "사용자의 IDE"** — ppt-master 자체에 LLM SDK가 없음
- **상태 저장소도 LLM 메모리** — 작업 상태는 채팅 컨텍스트에 들어있고, 채팅이 새로 열리면 `resume-execute.md` 워크플로로 파일 시스템에서 상태 복원
- **API/MCP가 없는 이유**: LLM이 이미 오케스트레이터이고, 사용자는 채팅으로 직접 LLM과 대화함. 외부 시스템이 호출할 필요가 없는 구조였음.

edit2ppt에서는 이 모델을 깨고, "외부 AI Agent가 우리 시스템을 호출"하는 모델로 바꿔야 합니다 ([04-integration-plan.md](04-integration-plan.md) 참조).

## 1.4 LLM 역할 3종 (references/ 에 정의)

| 역할 | 정의 파일 | 입력 | 출력 | 비고 |
|------|-----------|------|------|------|
| **Strategist** | `references/strategist.md` (472줄) | 사용자 의도 + 소스 문서(Markdown) | 디자인 스펙 (`design_spec.md`) + 실행 계약 (`spec_lock.md`) — 색상/폰트/페이지 구성/아이콘 계획 | "Eight Confirmations" 체크리스트 통과 필요 |
| **Image_Generator** | `references/image-generator.md` (400줄) | spec_lock + 페이지별 이미지 요구 | 생성된 이미지 파일들 (`images/`) | image_gen.py 또는 image_search.py 호출 결정 |
| **Executor** | `references/executor-base.md` (729줄) + 스타일별 변종 4개 | spec_lock + 페이지별 콘텐츠 + 이미지 | 페이지별 SVG (`svg_output/page_*.svg`) | 스타일: consultant / consultant-top / general |

이 역할들은 **순차 실행** (병렬 X). 각 역할 전환 시 LLM은 이전 컨텍스트를 비우고 해당 references 프롬프트를 새로 적재합니다. SKILL.md가 이 전환을 지시합니다.

## 1.5 파이프라인 7단계 (SKILL.md 요약)

```
Step 1: Source → Markdown    [결정적 스크립트, source_to_md/*.py]
Step 2: Project Init         [결정적, project_manager.py]
Step 3: Template (선택)       [LLM 또는 사용자 선택]
Step 4: Strategist           [LLM, Eight Confirmations 게이트]
Step 5: Image_Generator      [LLM 결정 → 결정적 스크립트 image_gen/search]
Step 6: Executor             [LLM, 페이지당 SVG 생성]
Step 7: Quality + Export     [결정적, svg_quality_checker → finalize_svg → svg_to_pptx]
```

자세한 단계별 입출력은 [02-pipeline.md](02-pipeline.md) 참조.

## 1.6 중간 표현: 왜 SVG 인가

LLM이 직접 OOXML 을 쓰는 것은 거의 불가능합니다 (XML 네임스페이스, EMU 단위, DrawingML 좌표계 등이 너무 복잡). 따라서 ppt-master는 **SVG를 중간 표현**으로 사용합니다.

- LLM은 SVG를 씁니다 (`svg_output/page_*.svg`). 이는 사람이 읽기 쉽고 LLM이 잘 다루는 포맷.
- `svg_to_pptx.py` 가 SVG → DrawingML 변환. SVG의 `<rect>`, `<text>`, `<path>` 등을 OOXML의 `<p:sp>`, `<p:txBody>` 등으로 1:1 매핑.
- 결과 PPTX는 **이미지가 아닌 네이티브 도형**으로 구성됨 → PowerPoint에서 모든 요소 클릭/편집 가능.

이 설계 덕분에 LLM이 자유롭게 디자인을 만들면서도, 결과물은 진짜 편집 가능한 PPTX 가 됩니다.

## 1.7 의존성 ([Bash로 확인됨](../requirements.txt))

핵심:
- `python-pptx>=0.6.21` — PPTX 빌더
- `PyMuPDF (fitz)>=1.23.0` — PDF 파싱
- `mammoth>=1.6.0` — DOCX 파싱
- `openpyxl>=3.1.0` — Excel 파싱
- `nbconvert>=7.0.0` — Jupyter notebook
- `beautifulsoup4>=4.12.0` + `requests>=2.31.0` — 웹 스크래핑
- `curl_cffi>=0.7.0` — TLS fingerprint (위챗 등 차단 우회)
- `Pillow>=9.0.0` — 이미지 처리
- `cairosvg` 또는 `svglib+reportlab` — SVG → PNG fallback (구버전 Office 호환)
- `edge-tts>=7.2.8` — 무료 TTS

이미지/오디오 백엔드 (선택):
- `google-genai>=1.0.0` — Gemini
- `openai>=1.0.0` — DALL-E
- 기타 백엔드(MiniMax, Qwen, CosyVoice, Volcengine 등)는 HTTP REST 로 직접 호출, SDK 없음

서버:
- `flask>=3.0.0` — `svg_editor/server.py` 로컬 SVG 편집기에서만 사용. 외부 API 아님.

**없는 것**:
- LLM 클라이언트 SDK (anthropic, openai-chat) — LLM은 외부 IDE가 담당
- FastAPI / Starlette
- MCP SDK
- 테스트 프레임워크 (pytest 등)
- Docker / 배포 스크립트

## 1.8 라이선스

MIT. 우리는 자유롭게 포크하고 변경 가능. 단 "MIT licensed — attribution required" (README:323), 즉 LICENSE 파일과 저작권 표시를 유지해야 함.

원본 LICENSE 파일은 이미 edit2ppt에 복사되어 있음. edit2ppt 자체의 라이선스도 MIT로 둘지, 다른 라이선스로 갈지 결정 필요 (대부분의 유사 포크는 MIT 유지).
