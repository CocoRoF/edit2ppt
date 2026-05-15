# 03. AI Generation — LLM 호출 자체의 개선

## 목표

Strategist 와 Executor 의 LLM 호출에 들어가는 system prompt, user message, 그리고 retry 정책을 개선합니다. **결정적 처리 (전·후처리) 가 잡을 수 없는 것** 만 LLM 으로 잡습니다.

## 적용 영역

```
Strategist (LLM)
   ↑ ↓
   prompt 개선 #1: 박스 폭 계산 가이드 추가
   prompt 개선 #2: rhythm tag 와 zone 정의 강제
   prompt 개선 #3: 자가 검증 (self-check) 섹션

Executor (LLM, 페이지 N개 병렬)
   ↑ ↓
   prompt 개선 #4: layout brief 를 첫 번째 강제 입력
   prompt 개선 #5: 박스 좌표 자가 검증 (output 직전)
   prompt 개선 #6: structured 출력 옵션 (실험적)

재시도 정책
   ↑ ↓
   개선 #7: retry hint 가 위반된 박스 좌표를 picosly 지목
   개선 #8: retry budget 페이지별 분리 (현재 deck 전체 공유)
```

## 3.1 Strategist prompt 개선

### #1. Layout zone 정의 강제

현재 Strategist 는 `page_rhythm` 정도만 emit. 각 페이지의 zone 정의는 모델 자유. 결과적으로 페이지마다 layout 이 다릅니다. **spec_lock 의 `page_layouts` 섹션을 강제** 합니다:

```yaml
page_layouts:
  P01:
    template: cover_centered
    title_zone: { x: 60, y: 280, w: 1180, h: 160 }
    sub_zone:   { x: 60, y: 460, w: 1180, h: 80 }
    footer_zone: { x: 60, y: 680, w: 1180, h: 40 }
  P02:
    template: hero_with_caption
    hero_zone: { x: 60, y: 100, w: 600, h: 500 }
    caption_zone: { x: 700, y: 200, w: 540, h: 300 }
    footer_zone: { x: 60, y: 680, w: 1180, h: 40 }
```

Strategist prompt 의 §V (Layout Principles) 에 위 형식을 출력하라고 명시. 5-6개 표준 template (`cover_centered`, `hero_with_caption`, `kpi_grid_2x2`, `kpi_grid_3x1`, `timeline_horizontal`, `comparison_side_by_side`) 만 허용.

### #2. 박스 폭 계산 가이드

현재 prompt 는 "텍스트가 박스 안에 들어가도록 하라" 정도만 언급. 구체적 공식 부재. 추가:

```
## 박스 폭 계산 (HARD rule)

박스를 그릴 때마다 다음 공식으로 폭을 확인:

  required_width_px = char_count × font_size_px × char_ratio
  char_ratio:
    - 한글: 0.95
    - 영문/숫자: 0.55
    - 슬래시/하이픈: 0.40
    - 공백: 0.30

box_width_px MUST be >= required_width_px × 1.15 (15% padding).

예: "01 / 10" at 12pt
  = 2×0.55 + 1×0.30 + 1×0.40 + 1×0.30 + 2×0.55 
  = ~2.8 char-widths × 12pt × 1px/pt 
  = 34px
  × 1.15 padding = 39px 필요. 박스 최소 40px.

페이지 번호 박스는 항상 width >= 130px (12pt × 7chars 안전).
```

이를 §X (Tech Constraints) 끝에 부착.

### #3. 자가 검증 섹션 (Strategist)

prompt 끝에 추가:

```
## Self-check (필수)

design_spec 과 spec_lock 작성 완료 후, 출력 전 다음을 자체 확인:

[ ] 모든 page_layouts 의 zone 합이 canvas (1280×720) 내부에 있는가
[ ] page_rhythm 의 P-id 개수가 design_spec §IX 슬라이드 개수와 같은가
[ ] icons.inventory 의 모든 이름이 chunk-filled/tabler-* 등 명시된 라이브러리에서 실재하는가 (위 prompt 의 inventory 참조)
[ ] colors palette 가 4-6개 사이인가 (광택/그라데이션은 별도 카운트 안 함)
[ ] fonts.stacks 의 모든 마지막 family 가 Malgun Gothic / Arial / Times New Roman 중 하나로 끝나는가
[ ] images.list 의 모든 entry 가 acquire_via (ai|web|placeholder) 를 명시했는가

위반이 있으면 출력 전 수정.
```

자가 검증은 결정적 검증 (preprocessing) 의 대체가 아니라 첫 라인 방어. 모델이 검증을 통과시켰다고 해도 spec validator 가 다시 검사합니다.

## 3.2 Executor prompt 개선

### #4. Layout brief 우선 처리

`ExecutePageRequest` 에 `layout_brief: PageLayoutBrief` 추가 (02-preprocessing.md 참조). user_message 의 **맨 앞** 에 위치:

```
# Layout brief (HARD constraint)
```yaml
canvas: { w: 1280, h: 720 }
zones:
  - role: title
    bbox: { x: 60, y: 60, w: 1180, h: 140 }
    char_budget: 30
...
page_number_box: { x: 1100, y: 685, w: 130, h: 30 }
```

# Page outline
...

# spec_lock
...
```

system prompt 는 이 brief 가 항상 첫 번째라고 명시 + zone 외부에 콘텐츠 배치 금지.

### #5. 박스 좌표 자가 검증 (Executor)

system prompt 끝:

```
## Self-check before emitting

생성한 SVG 의 모든 <text>, <rect>, <g> 의 좌표를 layout_brief 와 대조:

[ ] title 텍스트가 zones[role=title].bbox 안에 있는가
[ ] 페이지 번호 텍스트가 page_number_box 안에 있는가
[ ] 어떤 두 <text> 박스의 y-범위가 겹치면, x-범위가 분리되어야 함
[ ] x, y, width, height 모두 1280×720 canvas 안

위반이 있으면 좌표를 수정해 재출력. SVG 출력 전 self-check 결과를 짧게 적시 (3줄 이내):
  - "Self-check OK" 또는
  - "Self-check: <위반 항목>; 수정 적용함"
```

이 self-check 결과는 raw_output 에 머무르고 SVG 추출에는 영향 없음 (`_parse_output` 이 SVG 블록만 골라냄).

### #6. Structured output (실험적, Phase 3)

LLM 한테 SVG 가 아닌 **JSON layout** 을 emit 하게 하고, JSON → SVG 변환을 결정적 코드가 처리:

```json
{
  "page_id": "P03",
  "elements": [
    {"role": "title", "text": "풍경은 바뀌었다", "x": 60, "y": 100, "w": 1180, "h": 120, "font_size": 60, "weight": 900},
    {"role": "hero", "kind": "number", "value": "41%", "x": 60, "y": 240, "w": 400, "h": 280, "color": "primary"},
    {"role": "caption", "text": "GitHub Octoverse 2024", "x": 60, "y": 540, "w": 400, "h": 40, "font_size": 14}
  ]
}
```

이 방식의 장점:
- 결정적 layout repair (overlap 검출, 박스 fit) 가 쉬워짐.
- 좌표 산수 책임을 모델에서 우리 코드로 이전.
- Structured output 모드 (Anthropic API 의 `tool_use`) 와 호환.

단점:
- 모델의 미감 자유도 축소 (decorative shape 등).
- SVG 렌더 코드를 우리가 만들어야 함.

Phase 3 에서 검토 — Phase 1-2 가 충분히 좋다면 안 해도 됨.

## 3.3 재시도 정책

### #7. 위반 좌표 구체 지목 (현재 미진)

PR #27 에서 quality 위반 코드별 hint 를 만들었지만, **좌표 / 폭 / 박스** 같은 layout 위반은 아직 일반 메시지. 추가:

```
> Retry hint: layout repair detected box overflow at element index 4.
> Element role: page_number
> Expected box width >= 130px (12pt × 7 chars × 1.15 padding)
> Actual box width: 42px (3x too narrow)
> Action: resize the page_number box to at least 130px wide,
> or place its text in a wider container.
```

이를 위해 postprocessing 의 layout repair 가 위반 정보를 quality response 에 첨부해야 함 (04-postprocessing.md 참조).

### #8. Retry budget 페이지별 분리

현재 `retry_pages_on_quality_error: int = 2` — deck 전체 retry round 횟수. 동일 페이지가 2번 retry 됐을 수도 있고, 페이지마다 1번씩 retry 됐을 수도 있어 비결정적.

페이지별로 독립 budget 으로 분리:

```python
@dataclass
class RetryBudget:
    per_page: int = 2     # 페이지마다 최대 N회
    total: int = 6        # deck 전체 합산 cap (cost ceiling)
```

Page A 가 첫 시도에 통과하면 그 1 회는 다른 페이지로 transfer 되지 않습니다. 한 페이지에 retry 가 몰리는 (다른 페이지는 죽었지만) 케이스 방지.

## 3.4 Strategist / Executor 모델 선택

현재 둘 다 `claude-opus-4-7`. 가능한 옵션:

- **Strategist**: opus 유지 (복잡한 outline 생성에 필요).
- **Executor**: opus 가 페이지마다 비쌈. **sonnet 으로 다운그레이드** 검토. 페이지 단위 작업이라 복잡도 낮음.

비용 추정 (10p deck):
- 현재: Strategist ~1 call × opus + Executor ~10 calls × opus = ~$5
- 변경 후: Strategist 1 × opus + Executor 10 × sonnet = ~$2

retry 가 줄면 추가 절감. Phase 2 에서 A/B 비교.

## 적용 순서

| Phase | 항목 | 비용 | 임팩트 |
|---|---|---|---|
| 1 | #4 (layout brief 우선 처리) | 중 | 매우 큼 |
| 1 | #7 (위반 좌표 구체 지목) | 소 | 큼 |
| 1 | #3 (Strategist self-check) | 소 | 중 |
| 1 | #5 (Executor self-check) | 소 | 중 |
| 2 | #1 (page_layouts 강제) | 중 | 큼 |
| 2 | #2 (박스 폭 계산 가이드) | 소 | 중 |
| 2 | #8 (페이지별 retry budget) | 중 | 운영 안정 |
| 2 | 모델 다운그레이드 (Executor → Sonnet) | 소 | 비용 |
| 3 | #6 (structured output) | 큼 | 결정적 |

## 검증

각 prompt 변경마다 회귀 테스트 추가:
- `tests/unit/test_strategist_prompt_self_check.py`: prompt 안에 self-check 문구 있는지.
- `tests/unit/test_executor_layout_brief_in_message.py`: user_message 의 첫 줄이 layout brief 인지.
- `tests/unit/test_retry_hint_layout_violation.py`: layout 위반 시 hint 가 좌표 포함하는지.

E2E:
- deck 생성 → quality error 0, warning < 3 → 통과.

## 다음

- [02-preprocessing.md](02-preprocessing.md) — 위 #4 의 입력이 되는 layout brief 가 어디서 만들어지는가
- [04-postprocessing.md](04-postprocessing.md) — 위 #7 의 위반 검출이 어디서 일어나는가
