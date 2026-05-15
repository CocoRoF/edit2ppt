# 04. Postprocessing — LLM 이후 결정적 처리

본 문서가 본 plan 의 **중심** 입니다. 사용자가 가장 강조한 요구사항 ("전·후처리로 완벽하게 개선") 의 핵심.

## 목표

LLM 이 emit 한 SVG 를 받아서, **결정적 코드** 가 다음을 수행:

1. **정규화** (이미 일부 있음) — 이미 PR 들에서 부분 적용 (auto-id, image normalisation, weight strip).
2. **Layout repair** — overlap, 박스 fit, off-canvas 검출 + 자동 수정. **신규**.
3. **Quality cross-check** — converter parity (PR #27 의 기능 확장).
4. **Post-export 검증** — 최종 PPTX 의 구조적 통계 수집.

## 단계

```
Executor (LLM) 출력
    ↓ raw SVG
[6a] SVG 정규화 (기존)             ← PR #29 / #30 / #31 기반
    ↓ 정규화 SVG
[6b] Layout repair (신규)         ← 본 문서 핵심
    ↓ repair 완료 SVG + violations[]
[7] Quality (강화)               ← PR #27 확장 + violations 반영
    ↓ pass/fail/warnings
[8] (위반 시) 페이지 단위 재시도 (기존)
[9] Export
    ↓ deck.pptx
[10] Post-export 검증 (신규)       ← 통계 + warning surfacing
```

## 6a. SVG 정규화 (확장)

### 현재 위치
`src/edit2ppt/tools/execute.py` 의 `_parse_output` 직후. 이미 다음 변환을 수행:

| 변환 | PR | 코드 |
|---|---|---|
| Top-level `<g>` 에 auto-id | #29 | `_autoid_top_level_groups` |
| Image href → basename | #31 | `_normalise_image_refs` |
| `<image opacity>` 제거 | #31 | 동상 |
| Dangling image ref 제거 | #31 | 동상 |
| Font family 의 numeric weight 분리 | #30 | (converter 단, `_strip_weight_suffix`) |

### 추가할 변환

| 변환 | 정당화 |
|---|---|
| **Inline `style=` 의 font-size / color 추출** → 속성으로 normalize | 모델이 종종 `<text style="font-size:14px">` emit. 우리 converter 는 attribute 우선이라 inline style 누락. |
| **빈 dec 박스 제거** (no fill, no stroke, no children) | 시각 노이즈. |
| **`<text>` 안 다중 `<tspan>` 의 lineHeight 정규화** | 모델이 dy 를 줘서 dy-stack 으로 그리는 경우, 우리 flatten 이 충분치 않음. |
| **Color hex 정규화** (`#fff` → `#FFFFFF`, `rgb(...)` → `#RRGGBB`) | quality checker 가 rgba() 차단. 사전 normalize. |
| **Pretendard 가 inline `font-family` 에 있고 weight 가 없으면 weight=400 부여** | 일관성. |

## 6b. Layout repair (신규)

**가장 중요한 신규 모듈.** 본 plan 의 핵심.

### 책임

SVG 의 모든 시각 요소를 좌표 단위로 분석해 다음을 검출·수정:

1. **Overlap detection**: 두 텍스트 박스가 비-of-stack 으로 서로 위에 있는 경우.
2. **Box overflow**: `<text>` 박스의 width 가 텍스트 추정 폭보다 작은 경우.
3. **Off-canvas**: 좌표가 1280×720 밖.
4. **Footer cluster**: page-number / chapter label / source citation 의 표준 배치.

### 인터페이스

```python
# src/edit2ppt/core/svg_to_pptx/layout_repair.py

@dataclass
class LayoutViolation:
    kind: Literal[
        "overlap",
        "text_overflow_x",
        "text_overflow_y",
        "off_canvas",
        "empty_decoration",
    ]
    element_path: str        # /svg[2]/g[3]/text[1]
    actual: dict             # { "x":..., "w":..., ... }
    expected: dict           # { "w_min":..., ... }
    severity: Literal["error", "warning"]
    fix_applied: bool        # True 면 자동 수정함, False 면 발견만

@dataclass
class LayoutRepairResult:
    repaired_svg: str
    violations: list[LayoutViolation]

def repair_layout(svg: str, *, canvas_format: str = "ppt169") -> LayoutRepairResult:
    ...
```

### Detection 알고리즘 (텍스트 폭 추정)

```python
def estimate_text_width(text: str, font_size_px: float, font_weight: str = "400") -> float:
    """이미 src/edit2ppt/core/svg_to_pptx/drawingml_utils.py 에 존재. 재사용."""
    # 한글: char × font_size × 0.95
    # 영문/숫자: × 0.55
    # 슬래시·하이픈: × 0.40
    # 공백: × 0.30
    # weight >= 700 시 × 1.08
    ...
```

### Detection 알고리즘 (overlap)

```python
def detect_overlap(box_a, box_b, *, threshold=0.4):
    """두 박스의 교집합이 작은 박스의 40% 이상이면 overlap.
    
    한 박스가 다른 박스에 포함되는 경우 (배경 위 카드 등) 는
    intentional layering 으로 간주 — overlap 아님."""
    ...
```

### Repair 액션

| 위반 | 자동 조치 | 자동 조치 후 메모 |
|---|---|---|
| `text_overflow_x` (텍스트 폭 > 박스 폭) | 박스 width 를 확장 (1.15 × required) | warning 보존, retry trigger 안 함 |
| `overlap` (caption 이 hero 박스 안) | caption 박스를 hero 박스 bottom 아래로 y-shift | warning 보존 |
| `off_canvas` (x+w > 1280) | 박스를 canvas 안으로 shift (x = canvas_w - w) | warning 보존 |
| `empty_decoration` (visible 컨텐츠 없음) | 박스 제거 | info 만 |

자동 조치 후 retry 를 안 trigger 하는 이유: 두 번째 시도가 비결정적이라 안정성 떨어짐. 결정적 수정으로 충분하면 그걸로 끝냅니다.

조치 못 할 케이스 (예: 박스 확장 시 다른 박스와 새로 겹침) 는 violation 만 기록하고 quality 에 surface, retry 트리거.

### 통합

```python
# src/edit2ppt/tools/execute.py

svg = _autoid_top_level_groups(svg)
svg, image_basenames = _normalise_image_refs(svg, req.images)
repair_result = repair_layout(svg, canvas_format=req.canvas_format)
svg = repair_result.repaired_svg

# repair 의 violations 를 ExecutePageResponse.warnings 에 첨부
warnings.extend([_violation_to_warning(v) for v in repair_result.violations if v.severity == "warning"])
```

### 검증

```
tests/unit/test_layout_repair_overlap.py
    - hero 박스 + caption 박스 overlap → caption 이 shift 됨을 assert
tests/unit/test_layout_repair_overflow.py
    - 짧은 footer 박스 + 긴 텍스트 → 박스 확장 assert
tests/unit/test_layout_repair_off_canvas.py
    - x=1240, w=100 (totally outside) → x 조정 assert
tests/unit/test_layout_repair_empty_dec.py
    - 빈 box 제거 assert
```

## 7. Quality 강화 (PR #27 확장)

### 추가 검증

PR #27 에서 converter parity 위반 (`forbidden_use_*`) 을 surface. 추가:

| 신규 check | 위치 |
|---|---|
| `layout_overflow_x` — 박스 overflow 가 자동 수정 못 한 경우 | tools/quality.py |
| `layout_overlap_unresolved` — overlap 이 자동 수정 못 한 경우 | 동상 |
| `color_palette_violation` — spec_lock palette 밖의 hex | 신규 |
| `font_stack_violation` — spec_lock typography 외 폰트 | 신규 (warning) |

### Retry hint 변경

(03-ai-generation.md #7 참조) 위반 코드별 hint 가 layout 좌표를 포함하도록 _RETRY_HINTS 확장.

## 10. Post-export 검증 (신규)

### 책임

최종 deck.pptx 를 zip 으로 unpack 해 다음 통계 수집:

- 슬라이드 수
- 이미지 임베드 수 (`ppt/media/`)
- 슬라이드별 shape 수
- 슬라이드별 폰트 개수
- placeholder slide 개수
- 평균 text run / slide
- 색상 다양성
- canvas 안 모든 shape bounds 의 합산 비율 (whitespace 분석)

### 출력

`GenerateDeckResponse.export_metrics: ExportMetrics` 추가:

```python
@dataclass
class ExportMetrics:
    total_slides: int
    placeholder_slides: int
    embedded_images: int
    avg_shapes_per_slide: float
    color_palette_size: int
    fonts_used: list[str]
    canvas_fill_ratio: float        # 평균 슬라이드 콘텐츠 면적 / canvas 면적
```

UI 의 결과 페이지에 표시.

### 검증 + warning surfacing

| 통계 | 임계값 | 액션 |
|---|---|---|
| placeholder_slides > 0 | 1+ | warning |
| embedded_images < spec_lock 이미지 plan count | mismatch | warning |
| fonts_used 가 spec_lock typography 와 mismatch | 1+ unexpected | warning |
| canvas_fill_ratio < 0.3 | 너무 비었음 | info |
| canvas_fill_ratio > 0.9 | 너무 가득찼음 | info |

## 위반 정리표 (사전 발견 vs 사후 수정 vs Safety net)

| 문제 | 사전 발견 (validator) | 사후 수정 (repair) | Safety net |
|---|---|---|---|
| Icon 이름 mismatch | Spec validator | use_expander fuzzy | use_safety_net `<g/>` |
| Dangling image | Spec validator | execute.py normalise | pptx_builder placeholder slide |
| Hero overlap caption | Layout brief (제한적) | layout_repair shift | (없음) |
| Footer 박스 좁음 | Layout brief 고정 | layout_repair 확장 | (없음) |
| Off-canvas | (없음) | layout_repair shift | export safety net |
| 12pt 미만 텍스트 | (없음) | drawingml_elements floor | (없음) |
| `<use>` orphan | (없음) | use_safety_net | converter raise |

## 우선순위 (Phase 1 후보)

1. **`layout_repair.py` 의 overlap detector + caption shift** — 가장 임팩트 큼.
2. **box overflow detector + width 확장**.
3. **빈 dec 박스 제거**.
4. **Post-export 검증** — UI surfacing.

## 다음

- [05-roadmap.md](05-roadmap.md) — 위 작업의 phasing 과 의존성.
