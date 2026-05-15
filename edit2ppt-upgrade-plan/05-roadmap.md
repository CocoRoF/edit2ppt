# 05. Roadmap — 구현 phasing

본 문서는 [00-overview.md](00-overview.md) ~ [04-postprocessing.md](04-postprocessing.md) 의 모든 작업을 **순서** 와 **의존성** 기준으로 정렬합니다.

## 큰 그림

3 phase 로 구성:

| Phase | 기간 추정 | 임팩트 | 핵심 결과물 |
|---|---|---|---|
| Phase 1 — 즉시 가시적 개선 | 5-7 PR | 매우 큼 | deck_2.pptx 의 P0 문제 모두 해소 |
| Phase 2 — Strategist · Executor 협업 강화 | 3-5 PR | 큼 | retry 횟수 감소, layout 정합성 |
| Phase 3 — Structured output 실험 | 2-3 PR | 중 | 결정적 layout 100% |

각 PR 은 단위 테스트 + E2E 검증 + 해당 단계의 deck 회귀 비교 (deck_2.pptx → deck_phase1.pptx 등) 동반.

## Phase 1 — Postprocessing 결정적 보강

> **원칙**: 모델 동작 변경 없이, 모델 출력 직후의 결정적 처리만으로 deck_2.pptx 의 P0 문제 해소.

### P1.1 — `layout_repair.py` 신규 모듈

**목표**: overlap, box overflow, off-canvas, 빈 dec 박스 검출 + 자동 수정.

**코드**:
- 신규: `src/edit2ppt/core/svg_to_pptx/layout_repair.py`
- 통합: `src/edit2ppt/tools/execute.py` 에서 `_normalise_image_refs` 후 호출.
- 모델: `LayoutViolation`, `LayoutRepairResult` dataclass.

**Detector**:
1. `_detect_overlap` (bounding box intersection / containment 구분)
2. `_detect_text_overflow_x` (estimate_text_width 비교)
3. `_detect_off_canvas` (canvas bounds)
4. `_detect_empty_decoration` (no fill, no stroke, no children)

**Auto-fix**:
1. Overlap caption-in-hero → caption y-shift.
2. Text overflow → 박스 width 확장.
3. Off-canvas → x/y clamp.
4. Empty dec → 요소 제거.

**테스트**: 4개 detector × 2-3 케이스 = 12+ unit tests.

**기대 효과**: deck_2.pptx I.1 (hero overlap), I.2 (footer overflow), I.3 (chapter overflow) 모두 해소.

### P1.2 — Post-export ExportMetrics

**목표**: 최종 PPTX 통계 수집, UI surfacing.

**코드**:
- 신규: `src/edit2ppt/tools/_export_metrics.py`
- 통합: `src/edit2ppt/tools/generate_deck.py` 의 export 단계 후.
- API: `GenerateDeckResponse.export_metrics`.
- Web: `/jobs/<id>` 페이지에 통계 카드.

**테스트**: 통계 산출 단위 테스트.

### P1.3 — Spec validator 신규 모듈 (image + icon)

**목표**: placeholder slide 예방. Image plan ↔ acquisition 결과 cross-check.

**코드**:
- 신규: `src/edit2ppt/tools/_spec_validator.py`
- 통합: `src/edit2ppt/tools/strategize.py` 의 끝.
- Validator: icon name fuzzy resolve, image bundle existence, hex normalize.

**기대 효과**: deck_2.pptx II.1 (slide 10 실패) 와 같은 변환 단계 진입 전 차단.

### P1.4 — `<text style="...">` 의 inline style 추출 정규화

**목표**: 모델이 attribute 가 아닌 inline `style="font-size:14px"` 로 emit 한 case 도 잡기.

**코드**:
- `tools/execute.py` 의 정규화 단계에 `_normalise_inline_style` 추가.

### P1.5 — Color palette / font stack quality checks

**목표**: spec_lock 위반 surface (warning, retry 트리거 안 함).

**코드**:
- `tools/quality.py` 의 `_converter_parity_issues` 확장.

### Phase 1 완료 기준

- deck_2.pptx 와 동일 입력으로 재생성 시:
  - placeholder slide = 0
  - overflow > 1.8x = 0
  - overlap > 40% = 0
  - quality error = 0
  - quality warning < 3

## Phase 2 — Strategist · Executor 협업

### P2.1 — Layout brief generator

**목표**: spec_lock 으로부터 per-page layout brief 결정적 생성, Executor 입력의 첫 부분.

**코드**:
- 신규: `src/edit2ppt/tools/_layout_brief.py`
- 통합: `generate_deck.py` 의 Stage 4 (executor 호출) 직전.
- `ExecutePageRequest.layout_brief: PageLayoutBrief | None = None`
- `_build_user_message` 의 맨 앞에 yaml 블록 inject.

### P2.2 — Strategist prompt: page_layouts 강제

**목표**: Strategist 가 emit 하는 spec_lock 의 `page_layouts` 섹션에 zone bbox 포함.

**코드**:
- `src/edit2ppt/core/prompts/strategist.en.md` 의 §V 수정.
- Spec validator 에서 zone bbox 형식 검증 추가.

### P2.3 — Strategist · Executor self-check 섹션

**목표**: prompt 끝에 자체 검증 체크리스트.

**코드**:
- prompt 파일 둘 다 끝에 self-check 섹션 부착.
- raw_output 의 첫 1줄에 "Self-check OK" 또는 "Self-check: <fix>" 가 있는지 quality 가 확인 (warning 만).

### P2.4 — Retry hint 에 layout 위반 좌표 첨부

**목표**: PR #27 의 retry hint 확장 — layout violation 코드와 좌표.

**코드**:
- `generate_deck.py` 의 `_build_retry_hint` 와 `_RETRY_HINTS` 에 `layout_overflow_x`, `layout_overlap` 추가.

### P2.5 — Page-별 retry budget 분리

**목표**: 한 페이지에 retry 가 몰리는 케이스 방지.

**코드**:
- `GenerateDeckRequest.retry_pages_on_quality_error` → `RetryBudget(per_page: int, total: int)`.
- Pydantic backwards-compat shim.

### P2.6 — Executor 모델 다운그레이드 검토 (A/B)

**목표**: Opus 4.7 → Sonnet 4.6 비용 절감 + 동일 품질.

**코드**:
- `GenerateDeckRequest.executor_model` 추가 (옵션, 미지정 시 strategist 모델 사용).
- A/B 통계 비교.

### Phase 2 완료 기준

- 평균 retry round / deck < 0.5
- spec_lock validation 위반 = 0
- 동일 입력에 대한 deck 결과 변동성 (페이지 layout 의 sd) 감소
- Executor 비용 50% 절감 (Sonnet 다운그레이드 시)

## Phase 3 — Structured output 실험 (선택)

### P3.1 — Element JSON 스키마 정의

```python
class SlideElement(BaseModel):
    role: Literal[...]
    text: str | None
    x: int; y: int; w: int; h: int
    font_size: int | None
    weight: int | None
    color: str | None     # palette token
```

### P3.2 — JSON → SVG renderer

결정적 코드로 SlideElement list → SVG.

### P3.3 — Anthropic tool_use 활용

Executor 의 LLM 호출을 `tool_use` 모드로, output schema 강제.

### Phase 3 완료 기준

- LLM 의 좌표 산수 의존도 0%.
- Layout repair 의 fix 횟수 평균 < 1.

## 의존성 그래프

```
P1.1 (layout_repair) — 독립
P1.2 (export_metrics) — 독립
P1.3 (spec_validator) — 독립
P1.4 (inline style) — 독립
P1.5 (quality 강화) — P1.3 의존 (palette 정보 필요)

P2.1 (layout_brief) — P1.3 의존 (validated spec_lock)
P2.2 (strategist prompt) — P2.1 의 spec 필요
P2.3 (self-check) — 독립
P2.4 (retry hint 좌표) — P1.1 의존 (violation 데이터)
P2.5 (retry budget) — 독립
P2.6 (model A/B) — Phase 1 완료 후

P3.1-3 (structured output) — Phase 2 완료 후
```

## PR 작성 가이드

각 PR 의 표준 구조:

```
title: <phase>.<n>: <one-line>
body:
  ## Summary
  ## What this changes (코드 위치)
  ## Tests (unit + e2e)
  ## Regression check (deck_2.pptx 와 비교)
  ## Rollback plan
```

## 측정 + 회귀

각 Phase 끝에 같은 user_intent ("개발자의 종말: AI 시대") 로 deck 재생성, 새 deck.pptx 를 `ppt-master-analysis/` 에 `deck_phaseN.pptx` 로 보관. 다음 plan 분석은 이걸 baseline.

## 시간 추정 (대략)

- Phase 1: ~7개 PR, 2-3 days
- Phase 2: ~5개 PR, 1-2 days
- Phase 3: ~3개 PR, 1-2 days (옵션)

총 약 1주일 작업. Phase 1 만 끝나도 사용자 체감 품질은 dramatic 하게 개선됩니다.

## Done 정의 (전체)

- [ ] Phase 1 완료, deck regression 위 기준 통과
- [ ] Phase 2 완료, retry round 평균 < 0.5
- [ ] Phase 3 결정 (해도 / 안 해도)
- [ ] 모든 PR merged, 새 deck baseline 보관
- [ ] 본 plan 폴더의 모든 문서가 실제 구현과 동기화됨 (구현 후 backport)
