# 02. Preprocessing — LLM 이전에 할 일

## 목표

Strategist 의 spec_lock 이 Executor 로 넘어가기 전에 **결정적 검증 + 강화** 를 적용합니다. 이 단계가 잡으면 Executor 는 깨끗한 입력에서 시작하고, 그 결과 quality retry 횟수가 줄어듭니다.

## 단계

```
Strategist (LLM)
    ↓ design_spec + spec_lock
[2a] Spec validator            ← 신규
    ↓ (정정된) spec_lock + warnings
[2b] Layout brief generator    ← 신규
    ↓ per-page layout zones + 박스 + char budget
[2c] Image acquisition (이미 존재)
    ↓ 번들된 이미지 bytes
[3] Executor (LLM)
```

## 2a. Spec validator

### 입력
- `design_spec` (markdown)
- `spec_lock` (yaml 또는 markdown)

### 책임
1. **YAML / Markdown 파싱**. 두 형식 모두 수용 (기존 layered 파서 활용).
2. **필수 필드 존재** 확인. 누락 시 경고 (`spec_validation_missing_field`).
3. **Color hex** 형식 확인 (#RRGGBB).
4. **Icon inventory** 의 모든 icon name 이 번들 라이브러리 (`src/edit2ppt/core/templates/icons/<lib>/`) 에 존재하는지 확인. 없으면:
   - fuzzy 매칭 (이미 PR #28 에 use_expander 에 있음) 으로 가까운 이름 찾기.
   - 찾으면 spec_lock 의 inventory entry 를 자동 교체.
   - 못 찾으면 inventory 에서 제거 + warning. Strategist 가 plan 한 사용처 (`icons.usage`) 도 제거.
5. **Font stack** 의 마지막 family 가 Windows-safe 인지 확인 (`Malgun Gothic`, `Arial`, `Microsoft YaHei`, `SimSun`, `Times New Roman`, `Calibri`, `Consolas`). 아니면 자동으로 `Malgun Gothic` (Korean deck) / `Arial` (English) 부착.
6. **Page count alignment**: spec_lock 의 `page_rhythm` / `pages` / `slides` 페이지 수와 design_spec §IX 의 헤딩 수가 일치하는지 확인. 불일치 시 작은 값으로 truncate + warning.
7. **Image plan validation**:
   - `skip_images=True` 면 spec_lock 의 images 섹션 통째로 strip + Executor 한테 "no images" 힌트.
   - `skip_images=False` 면 plan 의 각 entry 가 `acquire_via` 를 명시했는지 확인.

### 출력
- `validated_spec_lock` (yaml/markdown 그대로, 보정된 내용)
- `warnings: list[ValidationWarning]`

### 코드 위치 (계획)
```
src/edit2ppt/tools/_spec_validator.py
    class SpecValidator
        def validate(spec_lock: str, *, image_libs: Path) -> SpecLockValidation:
            ...

src/edit2ppt/tools/strategize.py
    # 기존 함수 끝에 추가:
    validation = SpecValidator(...).validate(spec_lock)
    spec_lock = validation.spec_lock  # 보정본
    warnings.extend(validation.warnings)
```

### 검증 카탈로그

| 위반 | 자동 조치 | 못 자동 조치하는 경우 |
|---|---|---|
| 존재 안 하는 icon name | fuzzy match 로 교체 | warning + spec_lock 에서 제거 |
| 비 표준 hex color (`#fff`, `rgb(...)`) | normalize | 폐기 후 spec_lock palette 의 dominant 로 |
| Windows-safe fallback 누락 | `Malgun Gothic` 부착 | — |
| Page count mismatch | 작은 값으로 truncate | — |
| Image plan API key 누락 | acquire_via='placeholder' | — |

## 2b. Layout brief generator

### 입력
- `validated_spec_lock`
- `design_spec` (페이지별 outline)
- `canvas_format` (`ppt169` → 1280×720)

### 책임

각 페이지에 대해 다음을 생성:

```python
@dataclass
class PageLayoutBrief:
    page_index: int
    page_id: str                  # P01, P02...
    rhythm_tag: str               # anchor / dense / breathing
    canvas_w: int                 # 1280
    canvas_h: int                 # 720
    safe_area: tuple[int, int, int, int]  # x, y, w, h (1200×640 with 40px margin)

    # 권장 zone (모델한테 "여기에 이런 콘텐츠를 두라" 가이드)
    zones: list[Zone]
    
    # 박스 크기 제약 (모델의 좌표 추측을 줄이기 위해)
    title_box: BoxConstraint
    body_zone: BoxConstraint
    footer_box: BoxConstraint     # 페이지 번호 / 출처 / chapter label

    # 콘텐츠 길이 예측 (모델한테 "이만큼 charge 들어갈 거다" 알려주기)
    expected_chars:
        title: int               # design_spec §IX 의 P<NN> 항목에서 추출
        body: int
        bullets: list[int]

@dataclass
class Zone:
    role: Literal["title", "hero", "body", "footer", "image", "chart", "decoration"]
    bbox: tuple[int, int, int, int]  # x, y, w, h in px
    char_budget: int                  # 추정 폭/높이 기반
```

### Brief 의 결정 방식

- `rhythm_tag` 가 **anchor** 면 `title_box` 는 화면 상단 풀폭 (60-100, 1180-180), `body_zone` 은 중앙 안전영역.
- `rhythm_tag` 가 **dense** 면 grid 형 zones (2×2 또는 3×2) 자동 생성.
- `rhythm_tag` 가 **breathing** 면 hero zone 1개 중앙.
- **footer_box** 는 항상 (60, 680, 1180, 40) — 페이지 번호 + 출처 line 1개.
- **page-number sub-box** 항상 (1100, 685, 130, 30) — `01 / 10` 같은 7글자 길이 보장. 12pt floor 와 호환.

### Executor 한테 전달하는 방식

`ExecutePageRequest.layout_brief` 필드 추가. user_message 안에 yaml-블록으로 inject:

```yaml
# Layout brief — please respect these bounding boxes
canvas: { w: 1280, h: 720 }
safe_area: { x: 40, y: 40, w: 1200, h: 640 }
rhythm: anchor
zones:
  - role: title
    bbox: { x: 60, y: 60, w: 1180, h: 140 }
    char_budget: 30
  - role: body
    bbox: { x: 60, y: 240, w: 1180, h: 380 }
    char_budget: 500
footer_box: { x: 60, y: 680, w: 1180, h: 40 }
page_number_box: { x: 1100, y: 685, w: 130, h: 30 }
```

Executor 는 이 박스 안에 콘텐츠를 배치하라는 강제는 받지만, 그 안에서의 자유도는 유지.

### 코드 위치 (계획)
```
src/edit2ppt/tools/_layout_brief.py
    class LayoutBriefGenerator
        def build(spec_lock: ValidatedSpecLock, design_spec_pages: list[str]) -> list[PageLayoutBrief]:
            ...

src/edit2ppt/tools/execute.py
    class ExecutePageRequest(...):
        layout_brief: PageLayoutBrief | None = None
    
    def _build_user_message(req):
        ...
        if req.layout_brief:
            lines.append("# Layout brief")
            lines.append("```yaml")
            lines.append(yaml.safe_dump(asdict(req.layout_brief)))
            lines.append("```")
```

### Brief 가 잡는 문제

- **I.2 (footer 박스 overflow)**: `page_number_box: 130×30` 이 항상 지정되어 `01 / 10` 12pt 가 안전히 들어감.
- **I.3 (chapter label overflow)**: `footer_box` 가 1180px 폭이라 chapter label 도 들어감.
- **I.1 부분적 (hero overlap)**: zone 별 bbox 가 명시되어 hero zone 과 caption zone 이 비겹침. 단 모델이 caption 을 hero zone 안에 그릴 수 있어서 postprocessing 의 추가 검증이 필요.

## 2c. Image acquisition (기존)

변경 없음. 단 spec validator 가 `skip_images=True` 일 때 spec_lock 의 images 섹션을 미리 strip 해두면 acquisition 자체가 no-op 으로 빠집니다.

## 우선순위

1. **Layout brief generator** — 가장 임팩트 큼. 박스 크기 문제의 70% 차단.
2. **Spec validator (image + icon)** — placeholder slide 예방.
3. **Spec validator (font + color)** — 디자인 일관성.

## 검증 (어떻게 잘 작동하는지 확인)

- `tests/unit/test_spec_validator.py` (신규)
- `tests/unit/test_layout_brief.py` (신규)
- E2E: deck_2.pptx 와 같은 입력으로 새로 생성한 deck 의 footer 박스가 size>=120px 인지 확인.
