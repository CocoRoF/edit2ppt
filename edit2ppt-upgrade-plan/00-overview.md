# 00. Overview — 책임 분리 원칙과 의사결정 트리

## 문제 정의

deck.pptx → deck_new.pptx → deck_2.pptx 의 진화를 추적하면 우리가 풀고 있는 문제의 본질이 보입니다:

| 회차 | 주된 quality 증상 | 우리의 대응 |
|---|---|---|
| deck.pptx | `Pretendard 700` 가 typeface 로 박힘 (243건), `<g> no id` 경고 19건 | PR #28~#30: weight strip + auto-id |
| deck_new.pptx | `<image opacity>` 차단, `../images/X.png` resolve 실패 | PR #31: image 정규화 |
| deck_2.pptx | **레이아웃 정합성 붕괴** — hero 와 caption overlap, footer 박스 overflow, slide 10 변환 실패 | (본 plan) |

**증상은 다르지만 원인은 같습니다.** LLM 이 "이 슬라이드를 만들어라" 라는 너무 큰 단위의 책임을 받고 있어서, 텍스트 의미 (잘함) 와 픽셀 정합성 (못함) 을 동시에 처리해야 합니다.

## 책임 재배치 원칙

> **LLM 은 "무엇을, 어떻게 보이게 할지" 를 결정한다.
> 결정적 코드는 "그 결정이 수치적으로 맞는지" 를 검증·교정한다.**

이 원칙으로 모든 후속 결정이 따라옵니다.

### LLM 이 잘하는 영역 (계속 맡김)

- 콘텐츠 outline 작성 (Strategist §IX)
- 시각적 톤 / 색상 / 폰트 / 스타일 결정
- 텍스트 라이팅 (제목, 본문, 인용)
- 페이지별 narrative 구성
- 시각적 강조 패턴 선택 (KPI 카드 vs 비교 vs 타임라인 …)

### LLM 이 못하는 영역 (결정적 코드로 이전)

- **박스 width 가 텍스트 폭을 담을 수 있는지 계산**
- **두 박스가 시각적으로 겹치는지 검출**
- **이미지가 실제로 번들에 있는지 검증**
- **폰트 stack 이 Windows 에서 fallback 안 되는지 확인**
- **컬러가 spec_lock palette 안에 있는지 확인**
- **slide canvas (1280×720) 안에 모든 요소가 들어가는지 확인**

## 파이프라인 단계별 책임 매트릭스

```
입력:  user_intent + (선택) source documents
        ↓
[1] Strategist (LLM)
        ↓ 출력: design_spec + spec_lock
[2] Spec validator (결정적)        ← 신규
        ↓ 출력: 검증된 spec_lock + warnings
[3] Layout brief generator (결정적)  ← 신규
        ↓ 출력: per-page layout zones + 박스 + char budget
[4] Image acquisition (결정적 + 외부 API)
        ↓ 출력: 번들된 이미지 bytes
[5] Executor (LLM, 페이지 N개 병렬)
        ↓ 출력: SVG + speaker notes
[6] SVG 정규화 (결정적)             ← 일부 존재, 확장 필요
        ↓ 출력: 정규화된 SVG
[7] Layout repair (결정적)         ← 신규
        ↓ 출력: 박스 overlap 제거된 SVG
[8] Quality (결정적)
        ↓ 출력: 위반 리스트
[9] (위반 시) 페이지 단위 재시도   ← 이미 있음
        ↓ 출력: 정정된 페이지 SVG
[10] Export (결정적)
        ↓ 출력: deck.pptx
[11] Post-export 검증 (결정적)      ← 신규
        ↓ 통계 + warning surfacing
```

신규로 추가되는 단계는 4개: spec validator, layout brief, layout repair, post-export 검증. SVG 정규화는 일부 존재하지만 확장합니다.

## 의사결정 트리: 어떤 문제는 어디에서 잡는가

| 문제 카테고리 | 잡을 위치 | 이유 |
|---|---|---|
| 한국어 의미 어색 | LLM 시점 | 텍스트 의미는 LLM 만 평가 가능 |
| 폰트 family 에 weight 포함 (`Pretendard 700`) | SVG 정규화 | 결정적으로 substring 검출 가능 |
| 박스 width < 텍스트 length | Layout brief (사전) + Layout repair (사후) | 사전에 char budget 제공, 사후에 over-fit |
| 두 hero 박스 겹침 | Layout repair | 좌표·크기로 검출 가능 |
| 이미지가 spec_lock 에 있으나 실제 file 없음 | Spec validator (사전) + SVG 정규화 (사후) | 이중 방어 |
| 페이지 outline 이 §IX 헤딩 형식 아님 | Spec validator | 형식 검증으로 spec 단계에서 차단 |
| Color hex 가 6자리 아님 | Spec validator | regex 검증 |
| Slide 가 1280×720 canvas 벗어남 | Layout repair | bounds 계산 |
| Speaker note 빈 페이지 | Quality (warning) | 보수적 |

## 의사결정 원칙

1. **결정적으로 잡을 수 있다면, 결정적으로 잡는다.** LLM 한테 "이거 한 번 더 확인해줘" 보다 코드 한 줄이 더 신뢰할 수 있습니다.
2. **사전 차단 > 사후 수정 > Safety net.** 같은 위반을 잡을 수 있다면 가능한 한 빠른 단계에서 잡습니다.
3. **자동 정정 시 정정 사실을 surfaces.** 사용자가 "왜 이렇게 됐는지" 알 수 있어야 합니다. Warnings 에 모두 기록합니다.
4. **단일 슬라이드 실패가 deck 전체 실패로 번지지 않습니다.** Safety net 으로 placeholder 슬라이드 보장 (이미 PR #26 에 있음).
5. **결정적 단계의 회귀를 막기 위해 모든 단계는 단위 테스트와 함께 들어옵니다.**

## 비기능적 목표

| 항목 | 현재 | 목표 |
|---|---|---|
| Quality error / deck (10p) | 평균 4-5건 | 0건 |
| Quality warning / deck | 평균 19건 | < 3건 |
| Layout overlap / deck | 평균 12쌍 | 0쌍 |
| Placeholder slide / deck | ~1건 | 0건 |
| 12pt 미만 텍스트 / deck | 0 (12pt floor 도입) | 유지 |
| Hero 박스 안에 caption | 자주 발생 | 0건 |
| Page generation 시간 | 약 60-90초 (현재) | 유지 또는 미세 증가 |
| Retry 횟수 / deck | 1-2회 | 0-1회 (사전 검증 강화로) |

## 다음 단계

[05-roadmap.md](05-roadmap.md) — phase 1-3 구체 구현 계획.
