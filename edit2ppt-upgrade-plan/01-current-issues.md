# 01. Current Issues — deck_2.pptx 기반 증거 카탈로그

본 문서는 `ppt-master-analysis/deck_2.pptx` 의 모든 슬라이드를 정밀 분석해 도출한 **증거 기반** 문제 목록입니다. 각 항목은 슬라이드 번호, 좌표 (EMU/px 환산), 측정치를 포함합니다.

## 분석 방법

```
.venv/bin/python -c "
import xml.etree.ElementTree as ET
# 각 slide<N>.xml 을 파싱하여
# - <p:sp> 의 좌표/크기
# - <a:rPr sz=>, <a:latin>, <a:t>
# - <p:grpSp>, <p:pic> count
# 를 추출하고 overflow / overlap / off-canvas / tiny-text 검출
"
```

스크립트 전문은 본 plan 디렉토리 외부에 보관하지 않습니다 (이미 분석 자취가 텍스트에 정착되어 있음).

## I. 구조적 layout 실패 (가장 임팩트 큼)

### I.1 Hero number 와 caption 이 동일 bounding box 안에 겹침

**Slide 3**, 페이지 P03 (수치 강조 페이지):

| 요소 | 좌표 (px) | 크기 (px) | 폰트 | 내용 |
|---|---|---|---|---|
| `41` (hero) | (42, 243) | 270 × **352** | 165pt | "41" |
| 부제 1 | (62, **461**) | 310 × 35 | 16.5pt | "GitHub 저장소 분석 기준," |
| 부제 2 | (62, **491**) | 417 × 35 | 16.5pt | "Copilot이 직접 작성한 코드의 비중" |

`41` 박스는 y=243 에서 시작해 y=595 (243+352) 까지 차지합니다. 그런데 부제 1, 2 가 y=461, y=491 — `41` 박스 **내부**에 위치합니다. 시각적으로는 "41" 글자 위로 부제가 겹쳐서 둘 다 못 읽힙니다.

**원인**: 모델이 hero number 의 박스 크기 (352px tall) 와 caption 의 y 좌표를 독립적으로 계산. caption 의 y 가 hero box 의 bottom (y+h) 보다 큰지 검증 안 함.

**잡을 위치**: Postprocessing → layout repair. 두 박스의 bounds intersection 검출 → caption 을 hero box 아래로 shift.

### I.2 Footer 박스가 텍스트 폭의 50% 만 차지

전 슬라이드 공통. 예시 (slide 1):

| 요소 | 좌표 (px) | 크기 (px) | 폰트 | 내용 | 예상 폭 | 박스 폭 | 비율 |
|---|---|---|---|---|---|---|---|
| 페이지 번호 | (1213, 685) | **42 × 18** | 12pt | "01 / 10" | ~106px | 42px | 박스의 250% |

모델은 페이지 번호 박스를 ~42px 로 잡았는데 실제 텍스트는 ~106px 필요. PowerPoint 가 텍스트를 wrap 하지 않으면 박스 밖으로 흘러나오고, wrap 하면 두 줄이 됩니다.

**원인**: 모델이 박스 폭을 단순히 "글자 수 × 폰트크기 × 0.5" 같은 어림으로 잡음. 한글 / 슬래시 / 공백을 다르게 처리하지 않음. 게다가 우리 12pt floor 가 원래 의도된 9pt 박스를 12pt 로 키워서 더 좁아짐.

**잡을 위치**: Preprocessing → layout brief 에서 footer 박스 표준 크기 지정. Postprocessing → estimate_text_width 결과로 box 자동 확장.

### I.3 Chapter label 이 박스 폭의 2배 이상 (6/10 슬라이드)

| Slide | 텍스트 | 박스 폭 | 예상 폭 |
|---|---|---|---|
| 3 | "CHAPTER 01 · THE NUMBERS" | 170px | 365px |
| 8 | "CHAPTER 05 · CALL TO ACTION" | 202px | 410px |
| 4 | "SECTION 02 — 현실 진단" | (좁음) | (넘침) |

위와 동일한 원인.

### I.4 따옴표 글자가 100pt 단위로 떠 있는 박스

**Slide 4**: `(50, 171) size=(121 × 224) max_sz=105pt "" `

105pt 짜리 따옴표 한 글자가 121×224 박스에 있습니다. 의도적 dec 일 수 있지만 실제 슬라이드를 보면 본문 텍스트 옆에 100pt 따옴표가 떠 있어 시선이 분산됩니다.

**원인**: 모델이 spec_lock 스타일 (consultant 등) 에 따라 dec 요소를 추가하는데 사이즈 균형이 안 맞음.

**잡을 위치**: Strategist prompt 보강 + postprocessing 의 "dec 요소가 본문보다 3배 이상 크면 경고" 정도.

## II. 변환 단계 실패

### II.1 Slide 10 placeholder (실제 변환 실패)

```
'슬라이드 10 렌더링 실패'
'Slide 10 could not be rendered.'
'Failed to convert <image>: External image not found: 10_closing.png'
```

PR #26 의 safety net 이 발동해서 deck 은 완성됐지만 슬라이드 10 은 placeholder 입니다.

**원인**: Executor 가 `<image href="10_closing.png">` 를 emit. Image acquisition 은 `10_closing` placeholder 로 실행되었으나 실제 file 이 bundle 에 없음 (acquisition 실패 또는 placeholder 이름 mismatch). PR #31 의 image normalisation 이 이 케이스를 dropping 으로 처리하는데, deck_2.pptx 는 그 PR 이전에 생성됐을 가능성.

**잡을 위치**:
1. 이미 적용됨 (PR #31): bundle 에 없는 image ref 자동 drop.
2. 추가 (Spec validator): Strategist 의 spec_lock images 섹션과 실제 acquisition 결과를 cross-check, 실패한 image 는 spec_lock 에서도 제거.
3. 추가 (Postprocessing): image placeholder rect 생성 — 빈 자리 대신 "Image: <description>" 카드.

### II.2 이미지 4개 plan, 실제 embed 0개

deck_2.pptx 의 `ppt/media/` 디렉토리가 없습니다. Strategist 의 spec_lock 에서 4개 이미지를 plan 했는데 실제로는 0개가 embed 됐습니다.

**원인**: 이미지 acquisition 이 모두 실패했거나 (API key 없음 / 정책 위반), 사용자가 `skip_images=True` 옵션을 줬을 가능성. 후자라면 spec_lock 의 image 섹션 자체를 제거해서 Executor 가 image 를 안 referencing 하게 만들어야 합니다.

**잡을 위치**: Preprocessing → `skip_images=True` 일 때 spec_lock 에서 images 섹션 strip, Executor 에는 "no images available" hint.

## III. 미시적 layout 노이즈

### III.1 빈 dec 박스 다수

**Slide 8** 의 overlap 분석: 빈 텍스트 박스 쌍이 60.3% 겹침. 이건 두 개의 dec rect 가 서로 위에 있는데 텍스트가 없어서 분간이 안 갑니다.

**원인**: 모델이 layer 구성을 위해 background rect 위에 또 다른 rect 를 깔다 보면 결국 시각적으로 의미 없는 박스가 늘어납니다.

**잡을 위치**: Postprocessing → 텍스트 없음 + fill 가 동일 + size 겹침 → 한 쪽 제거.

### III.2 색상 8-10개 사용 (rule 위반)

Strategist prompt 의 "4 colors per page" 룰을 모델이 자주 위반 — 평균 8개 색상.

**원인**: 모델이 gradient / shadow 등 dec 효과를 위해 alpha variant 들을 따로 만듦. 의도적이지만 spec_lock 의 룰과는 충돌.

**잡을 위치**: Quality (warning), Strategist prompt 강화.

### III.3 폰트 family 다양 — `Nanum Myeongjo`, `Times New Roman`, `D2 Coding`

| Slide | 폰트 |
|---|---|
| 1 | Malgun Gothic, D2 Coding, Nanum Myeongjo |
| 2 | Malgun Gothic, D2 Coding, Times New Roman |
| 7-8 | Malgun Gothic, Nanum Myeongjo |

spec_lock 의 typography stack 은 보통 1-2개 family. 모델이 페이지마다 다른 폰트를 골랐습니다. Times New Roman 은 영문 dec 에 쓰이는 듯하지만 spec_lock 에서 명시 안 됐을 가능성.

**원인**: Executor 가 spec_lock 의 typography stack 을 페이지마다 일관 안 함.

**잡을 위치**: Postprocessing → page 의 모든 latin/ea 를 spec_lock 의 first stack 으로 강제 (옵션), 또는 Quality warning.

## IV. 안전망이 이미 처리하고 있는 문제 (점검 차원)

| 문제 | 처리 위치 | 상태 |
|---|---|---|
| `Pretendard 700` typeface | drawingml_utils.py `_strip_weight_suffix` (PR #30) | OK |
| `<g> no id` 경고 | execute.py `_autoid_top_level_groups` (PR #29) | OK |
| `<image opacity>` | execute.py `_normalise_image_refs` (PR #31) | OK |
| Dangling image ref | execute.py `_normalise_image_refs` (PR #31) | OK |
| 9pt 미만 텍스트 | drawingml_elements.py 12pt floor (PR #30~#31) | OK |
| Orphan `<use>` | use_safety_net.py (PR #26) | OK |
| Per-slide convert fail | pptx_builder.py placeholder (PR #26) | OK |
| Inline SSE bus | api/main.py FakeJobBus install (PR #20) | OK |

## 통계 요약 (deck_2.pptx)

| 측정치 | 값 |
|---|---|
| Slide 수 | 10 |
| 평균 shape / slide | 36 |
| 평균 text run / slide | 22 |
| 평균 색상 / slide | 8 |
| Placeholder slide | 1 (slide 10) |
| Overflow risk (>1.8x) | 21건 across 8 slides |
| Tiny text < 15pt (12pt floor 후) | 144건 across 9 slides |
| Layout overlap pair (>40%) | 13쌍 across 6 slides |
| Image embed | 0 |
| Quality 검사 결과 (사용자 report) | 오류 2, 경고 0 |

## 우선순위 정리

본 plan 의 후속 문서들은 다음 순서로 문제를 잡습니다:

| Priority | 문제 ID | 잡을 단계 | 영향도 |
|---|---|---|---|
| P0 | I.1 (hero overlap caption) | Postprocessing layout repair | 시각적 결정타 |
| P0 | I.2 (footer 박스 overflow) | Preprocessing layout brief + Postprocessing over-fit | 전 슬라이드 |
| P0 | II.1 (변환 실패 → placeholder) | Spec validator + 이미 PR#31 | 1슬라이드 |
| P1 | I.3 (chapter label overflow) | 위와 동일 | 6/10 |
| P1 | III.1 (빈 dec 박스) | Postprocessing pruning | 시각 노이즈 |
| P2 | II.2 (이미지 0개) | Preprocessing skip_images 처리 | UX |
| P2 | III.2 (색상 8개) | Strategist prompt + Quality | 디자인 일관성 |
| P3 | III.3 (폰트 mix) | Postprocessing 강제 또는 Quality | 디자인 일관성 |
| P3 | I.4 (오버사이즈 따옴표) | Strategist prompt | 미감 |

다음: [02-preprocessing.md](02-preprocessing.md), [04-postprocessing.md](04-postprocessing.md) 에서 위 문제들의 구체 해법.
