# edit2ppt 고도화 계획 (Upgrade Plan)

## 한 줄 요약

**현재 파이프라인은 LLM 한 번에 모든 것 (콘텐츠 + 레이아웃 + 픽셀 좌표 + 폰트 사이즈) 을 맡깁니다. 그 결과 콘텐츠 품질은 합격이지만 레이아웃 품질이 들쭉날쭉합니다.** 본 문서는 **전처리 (preprocessing) + 결정적 후처리 (deterministic postprocessing)** 를 추가해 LLM 이 잘하는 영역과 못하는 영역을 분리하는 단계적 업그레이드 안을 정의합니다.

## 왜 이 작업이 필요한가

deck_2.pptx 정밀 분석 결과 (실측치):

| 증상 | 빈도 | 원인 카테고리 |
|---|---|---|
| Hero number 와 caption 이 같은 bounding box 안에 겹침 | slide 3 등 | 모델의 레이아웃 좌표 계산 오류 |
| Footer `01 / 10` 박스가 텍스트 폭의 50% 만 차지 | 전 슬라이드 | 박스 width 와 텍스트 length 정합성 |
| Chapter 레이블이 박스 폭의 2배 이상 | 6/10 슬라이드 | 위와 동일 |
| Slide 10 자체가 placeholder (변환 실패) | 1/10 | image 참조 unresolvable (PR#31 이전) |
| 박스는 있는데 시각적 내용 0 | slide 1 background overlap 등 | 빈 dec 박스 다수 |
| Image plan 은 4개, 실제 embed 0개 | 전 슬라이드 | 이미지 acquisition fallback 부재 |

이런 문제들은 **LLM 을 더 잘 prompt 한다** 로는 한계가 있습니다. 모델은 매번 다른 좌표를 emit 하고, 폰트 폭 추정을 잘못 하고, 박스를 너무 작게 잡습니다. **결정적인 후처리** 로 잡아야 합니다.

## 핵심 패러다임

```
[기존]   Strategist  →  Executor (자유 SVG)  →  Quality  →  Export
              ↑                    ↑                 ↑
              모든 책임이           모든 책임이         단순 검증만
              한 곳에               한 곳에            (정정 못 함)

[목표]   Strategist  →  spec validator  →  layout brief  →  Executor (자유 SVG)
                                                                    ↓
                                                            SVG 정규화기 (결정적)
                                                                    ↓
                                                            Layout repair (결정적)
                                                                    ↓
                                                            Quality (parity) →  Export
                                                                                  ↓
                                                                              Post-export
                                                                              검증/통계
```

LLM 은 **의도·내용·미감** 에 집중. 결정적 단계가 **수치·정합성** 을 책임집니다.

## 문서 목차

| # | 문서 | 내용 |
|---|------|------|
| 00 | [overview.md](00-overview.md) | 전체 철학 + decision tree (어디서 무엇을 책임지는가) |
| 01 | [current-issues.md](01-current-issues.md) | deck_2.pptx 기반 **증거가 있는** 문제 카탈로그 |
| 02 | [preprocessing.md](02-preprocessing.md) | spec_lock validation, layout brief 생성 (LLM 이전) |
| 03 | [ai-generation.md](03-ai-generation.md) | Executor prompt 강화, 자가 검증, structured output |
| 04 | [postprocessing.md](04-postprocessing.md) | **결정적 SVG repair** — overlap, 박스 fit, 빈 shape 제거, image fallback |
| 05 | [roadmap.md](05-roadmap.md) | Phase 1-3 implementation 순서 + dependencies |

## TL;DR — 어디서 무엇을 책임지는가

| 단계 | LLM 의 역할 | 결정적 코드의 역할 |
|---|---|---|
| Strategist | **무엇을** 만들지 결정 (콘텐츠 outline, 색상, 폰트, 페이지 수) | spec_lock schema validation, icon/image/font 존재 검증 |
| Layout brief | — | 각 페이지의 layout zone, 박스, 예상 char 수를 spec_lock 으로부터 결정 |
| Executor | **어떻게 보이게** 할지 (텍스트 톤, 시각적 강조, 미감) | 박스 좌표·폭은 brief 따름 |
| SVG 정규화 | — | id 자동 부여, image href basename, 폰트 weight 분리, `<use>` 처리 |
| Layout repair | — | **overlap 검출 + shift, 박스 폭 over-fit, 빈 shape 제거, empty image fallback** |
| Quality | — | converter parity 검증 |
| Export | — | DrawingML 변환 + placeholder safety net |

## 현재 단계

- 완료된 인프라: 강건한 page-plan 파싱 (PR #22~#25), use-href / safety-net (#21, #26), font weight 분리 + 12pt floor (#30~#31), image normalisation (#31)
- **다음 단계**: 본 plan 의 Phase 1 (이 디렉토리의 [roadmap.md](05-roadmap.md))

## 누가 무엇을 읽어야 하는가

- **인프라 엔지니어**: 02 + 04 (결정적 코드 들어갈 곳)
- **프롬프트 엔지니어**: 03
- **PM**: 00 + 01 + 05
- **신규 합류자**: README → 00 → 05 순으로
