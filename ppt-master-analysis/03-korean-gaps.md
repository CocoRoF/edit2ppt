# 03. 한국어 지원 누락 지점

ppt-master 의 코드/문서에서 한국어가 깨지거나 누락되는 지점을 정확한 파일:라인 인용과 함께 정리. 우선순위는 **Critical → High → Medium → Low** 순.

> 모든 경로는 `ppt-master/skills/ppt-master/` 기준 상대 경로.
> edit2ppt 에서는 동일한 위치를 수정.

---

## 3.1 Critical — 결과물이 깨지는 버그성 누락

### G1. Hangul이 `is_cjk_char()` 에 포함되지 않음 — 텍스트 폭 계산 오류

**파일**: `scripts/svg_to_pptx/drawingml_utils.py:427-433`

```python
def is_cjk_char(ch: str) -> bool:
    """Check if a character is CJK (Chinese/Japanese/Korean)."""  # ← 주석은 "Korean" 포함
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x2E80 <= cp <= 0x2EFF or 0x3000 <= cp <= 0x303F or
            0xFF00 <= cp <= 0xFFEF or 0xF900 <= cp <= 0xFAFF or
            0x20000 <= cp <= 0x2A6DF)
```

**문제**:
- 함수 주석은 "Korean"을 포함한다고 적혀있지만, **실제 검사 범위에 Hangul 영역이 모두 빠짐**.
- 누락 범위: `U+AC00–U+D7A3` (Hangul Syllables, 가-힣 11,172자), `U+1100–U+11FF` (Hangul Jamo), `U+3130–U+318F` (Hangul Compatibility Jamo), `U+A960–U+A97F`, `U+D7B0–U+D7FF`.

**영향**: `estimate_text_width()` (같은 파일 436-454행) 가 한국어 글자를 `else` 가지(0.55 × font_size)로 처리. 실제로는 한국어 글자는 거의 정사각(1.0 × font_size)에 가까움. **결과적으로 텍스트 폭이 거의 절반으로 추정되어 레이아웃이 비좁게 잡히고 글자가 도형 밖으로 넘침**.

**수정**:
```python
def is_cjk_char(ch: str) -> bool:
    """Check if a character is CJK (Chinese/Japanese/Korean)."""
    cp = ord(ch)
    return (
        # Chinese/Japanese ideographs
        0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
        0x2E80 <= cp <= 0x2EFF or 0x3000 <= cp <= 0x303F or
        0xFF00 <= cp <= 0xFFEF or 0xF900 <= cp <= 0xFAFF or
        0x20000 <= cp <= 0x2A6DF or
        # Korean Hangul
        0xAC00 <= cp <= 0xD7A3 or       # Hangul Syllables
        0x1100 <= cp <= 0x11FF or       # Hangul Jamo
        0x3130 <= cp <= 0x318F or       # Hangul Compatibility Jamo
        0xA960 <= cp <= 0xA97F or       # Hangul Jamo Extended-A
        0xD7B0 <= cp <= 0xD7FF          # Hangul Jamo Extended-B
    )
```

우선순위: **Critical**

---

### G2. OOXML `lang="zh-CN"` 하드코딩 4곳 — PPT 교정 언어 오작동

**파일 1**: `scripts/svg_to_pptx/pptx_notes.py:75,80,85` (Speaker notes XML)

```python
# 75:  <a:rPr lang="zh-CN" dirty="0"/>
# 80:  paragraphs.append('<a:p><a:endParaRPr lang="zh-CN" dirty="0"/></a:p>')
# 85:  else '<a:p><a:endParaRPr lang="zh-CN" dirty="0"/></a:p>'
```

**파일 2**: `scripts/svg_to_pptx/drawingml_elements.py:1002` (본문 텍스트 run)

```python
# <a:rPr lang="zh-CN" sz="{sz}"{b_attr}{i_attr}{u_attr}{strike_attr} dirty="0">
```

**영향**: 모든 슬라이드 텍스트와 발표자 노트가 OOXML 레벨에서 "Simplified Chinese" 로 마킹됨. PowerPoint 의 맞춤법 검사, 교정, 사전이 중국어로 동작. **한국어 텍스트가 들어가도 zh-CN으로 마킹되므로 한국어 맞춤법 검사 안 됨.**

**수정 방향**: 텍스트 내용을 분석해 동적으로 결정.
```python
def detect_lang(text: str) -> str:
    """Return OOXML lang code: ko-KR / zh-CN / ja-JP / en-US."""
    if any(0xAC00 <= ord(c) <= 0xD7A3 for c in text):
        return "ko-KR"
    if any(0x4E00 <= ord(c) <= 0x9FFF for c in text):
        return "zh-CN"
    if any(0x3040 <= ord(c) <= 0x309F or 0x30A0 <= ord(c) <= 0x30FF for c in text):
        return "ja-JP"
    return "en-US"
```

또는 더 간단히 — 사용자가 프로젝트 생성 시 기본 언어를 지정하고 (`project.config.lang`), 그 값을 변수로 전달.

우선순위: **Critical**

---

### G3. `EA_FONTS` 셋에 한국어 폰트 있으나, **폰트 매핑/추천 체계에 한국어 가이드 없음**

**파일**: `scripts/svg_to_pptx/drawingml_utils.py:31-58`

```python
EA_FONTS = {
    'Microsoft YaHei', '微软雅黑', 'SimSun', '宋体', 'SimHei', '黑体',
    'PingFang SC', 'PingFang TC', 'Source Han Sans', '思源黑体',
    ...
    'Malgun Gothic', 'Gulim', 'Dotum', 'Batang',          # ← 한국어 폰트 있음 (line 52)
    'Noto Sans KR', 'Noto Serif KR',                       # ← 라인 53 추정
    ...
}
```

**상태**:
- 한국어 폰트가 `EA_FONTS` 셋에 포함되어 있어 "동아시아 폰트" 로 인식은 됨 (`drawingml_utils.py:409` 의 `if font in EA_FONTS`).
- **그러나** `FONT_FALLBACK_WIN` 매핑 (59-88행) 에 macOS → Windows 한국어 폰트 변환이 없음. 예: macOS 의 `Apple SD Gothic Neo` → Windows 의 `Malgun Gothic` 변환 누락.
- **그리고** 기본 폰트 스택 (config.py:409) 이 중국어 우선: `"'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif"`. 한국어 콘텐츠 시 PingFang SC 가 한국어 글자에 적용되면 폰트 누락으로 .notdef 또는 박스로 표시될 수 있음.

**수정**:
1. `FONT_FALLBACK_WIN` 에 한국어 폰트 매핑 추가:
   ```python
   FONT_FALLBACK_WIN = {
       ...
       'Apple SD Gothic Neo': 'Malgun Gothic',
       'Apple SD산돌고딕 Neo': 'Malgun Gothic',
       'Noto Sans CJK KR': 'Malgun Gothic',
       'Pretendard': 'Malgun Gothic',
       'Spoqa Han Sans Neo': 'Malgun Gothic',
       'Nanum Gothic': 'Malgun Gothic',
   }
   ```
2. `config.py` 에 언어별 기본 폰트 스택 분기:
   ```python
   DEFAULT_FONT_STACKS = {
       "ko-KR": "'Pretendard', 'Apple SD Gothic Neo', 'Malgun Gothic', 'Noto Sans KR', sans-serif",
       "zh-CN": "'PingFang SC', 'Microsoft YaHei', sans-serif",
       "ja-JP": "'Hiragino Sans', 'Yu Gothic', sans-serif",
       "en-US": "'Helvetica Neue', Arial, sans-serif",
   }
   ```

우선순위: **High**

---

## 3.2 High — LLM 출력 품질 저하

### G4. `references/strategist.md` — 한국어 디자인 예시 없음

**파일**: `references/strategist.md` (472줄)

**상태**:
- 폰트 추천 섹션이 중국어/영어 폰트 위주: "Microsoft YaHei, SimSun, FangSong, KaiTi" 등.
- 산업별 디자인 예시 (`186` 줄 부근 "政务" 등) 가 중국 정부/기업 컨텍스트.
- "Eight Confirmations" 체크리스트 자체는 언어 중립적이지만, 예시 응답이 중국어.

**영향**: LLM 이 이 프롬프트를 그대로 적재하면 한국어 입력에 대해서도 중국어 폰트를 제안하거나, 중국식 색감/레이아웃을 유도.

**수정 방향**:
- `references/strategist-ko.md` 작성 또는
- `strategist.md` 에 한국어 섹션 추가 ("If user's primary language is Korean: use Pretendard / Apple SD Gothic Neo / Malgun Gothic ...")
- 산업별 색감 가이드에 한국 사례 추가 (삼성/현대/네이버/카카오 등 익숙한 브랜드 톤)

우선순위: **High**

---

### G5. `references/executor-*.md` — 한국어 텍스트 폭/줄바꿈 가이드 없음

**파일들**: `references/executor-base.md` (729줄), `executor-consultant.md`, `executor-consultant-top.md`, `executor-general.md`

**상태**: 페이지 리듬 (anchor / dense / breathing), 그리드, 글자 위계 규칙이 모두 중/영 텍스트 길이를 가정.

**영향**: 한국어 글자는 평균적으로 영어 단어보다 짧고 중국어 글자보다 가로폭이 약간 작음. 같은 화면 공간에 들어가는 글자 수가 달라서 그대로 적용하면 여백이 어색해짐.

**수정 방향**: 언어 모드별 권장 페이지 글자 수 가이드 추가. 폰트 위계 (제목 64-72pt, 부제 36-44pt, 본문 18-24pt) 는 한국어에도 거의 그대로 사용 가능하나, 한국어는 자간 (-2 ~ -5%) 살짝 좁히는 것이 일반적임을 명시.

우선순위: **High**

---

### G6. `references/image-generator.md` — 영어 프롬프트 위주

**파일**: `references/image-generator.md` (400줄)

**상태**: 스타일 키워드 ("modern flat design", "cinematic", "professional photography" 등) 가 영어로만 제시. 한국어 컨텍스트 (한복, 한옥, 김치, K-pop, 한국 직장 풍경 등) 에 대한 가이드 없음.

**영향**:
- LLM 이 한국어 입력을 받아도 영어 프롬프트만 생성 → 일부 백엔드 (Qwen 등 중국 모델) 에서 결과 품질 저하 가능.
- 한국적 시각 자산이 필요한 슬라이드에서 생성 결과가 일반 동양인/일본/중국 톤으로 나옴.

**수정 방향**: 한국 맥락 예시 추가, 그리고 백엔드별 한국어/영어 프롬프트 처리 권장사항 정리.

우선순위: **Medium**

---

### G7. `references/image-searcher.md` — 한글 쿼리 가이드 없음

**파일**: `references/image-searcher.md` (290줄)

**상태**: Pexels / Pixabay / Openverse / Wikimedia 검색이 영어 쿼리 가정. 한글 쿼리는 결과가 거의 없음.

**수정 방향**: "한국 콘텐츠는 쿼리를 영어로 자동 번역해서 검색" 같은 가이드. 또는 한국 사진 서비스 (언스플래시 한국 컬렉션 등) 추가 검토.

우선순위: **Medium**

---

## 3.3 Medium — 템플릿/디자인 자산

### G8. 한국어 레이아웃 템플릿 없음

**경로**: `templates/layouts/`

**상태**: 19개 레이아웃 중 한국 컨텍스트는 0개. 기존: 중국 정부/은행 (中国电建, 招商银行, 重庆大学), 미국 기업 (anthropic, google_style), 학술 (academic_defense, medical_university), 범용 (pixel_retro, psychology_attachment).

**수정 방향 (선택)**:
- 한국 컨텍스트 레이아웃 추가: 한국 정부/공공기관 톤, 한국 대학 발표 톤, 한국 스타트업 톤
- 또는 기존 레이아웃에서 폰트만 한국어로 바꿔서 재사용 가능하게 만드는 "locale override" 메커니즘

우선순위: **Medium**

---

### G9. `templates/design_spec_reference.md`, `spec_lock_reference.md` — 한국어 폰트 예시 없음

**파일들**: `templates/design_spec_reference.md` (16.9 KB), `templates/spec_lock_reference.md` (10.4 KB)

**상태**: 폰트 스택 예시가 모두 중국어/영어 폰트.

**수정**: 한국어 폰트 스택 예시 추가 (Pretendard, Apple SD Gothic Neo, Malgun Gothic).

우선순위: **Medium**

---

## 3.4 Low — 부가 자산/문서

### G10. TTS — Edge TTS Korean 음성은 사용 가능 (코드 변경 거의 불필요)

**파일**: `scripts/tts_backends/backend_edge.py:10-15`

**상태**: 기본 voice 리스트가 모두 `zh-CN`. 그러나 edge-tts 라이브러리는 한국어 음성 (`ko-KR-SunHiNeural`, `ko-KR-InJoonNeural`) 을 이미 지원함. CLI 에서 `--voice ko-KR-SunHiNeural --locale ko-KR` 로 호출 가능.

**수정**: `notes_to_audio.py` 의 기본 voice/locale 결정 로직에 프로젝트 언어 반영. Edge backend 의 추천 voice 리스트에 ko-KR 항목 추가:
```python
("ko-KR", "ko-KR-SunHiNeural", "여성, 한국어, 자연스럽고 차분함"),
("ko-KR", "ko-KR-InJoonNeural", "남성, 한국어, 신뢰감 있는 톤"),
```

MiniMax/Qwen/CosyVoice 등 중국 백엔드는 한국어 미지원이므로 fallback 로직 추가 검토.

우선순위: **Low**

---

### G11. 위챗 친화 HTTP 헤더 — `Accept-Language: zh-CN`

**파일**: `scripts/source_to_md/web_to_md.py:126`

```python
"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
```

**상태**: 위챗 등 중국 사이트를 잘 받기 위한 헤더. 한국 뉴스 사이트 등에서는 한국어 콘텐츠를 더 잘 받으려면 `ko-KR,ko;q=0.9,en;q=0.8` 이 나음.

**수정 방향**: URL 도메인을 보고 헤더 결정, 또는 옵션화.

우선순위: **Low**

---

### G12. 문서 URL 한국어 번역

**파일들**: `README.md`, `docs/`, `CONTRIBUTING.md` 등

**상태**: 한국어 번역 없음 (영어, 중국어만).

**수정 방향**: `README.ko.md` 작성. 단, edit2ppt 가 별도 프로젝트이므로 ppt-master 와 동일한 README가 아니라 edit2ppt 자체의 README가 필요.

우선순위: **Low**

---

### G13. 중국어 디렉토리/파일명 → 영문 리네임 (이중 트랙 컨벤션)

**대상**: `skills/ppt-master/templates/layouts/` 의 7개 중국어 디렉토리 + 그 안의 중국어 자산 파일들

**현재 (중국어 디렉토리)**:
```
templates/layouts/中国电建_常规/
templates/layouts/中国电建_现代/
templates/layouts/中汽研_常规/
templates/layouts/中汽研_现代/
templates/layouts/中汽研_商务/
templates/layouts/招商银行/
templates/layouts/重庆大学/
```

**현재 (중국어 자산 파일)**:
```
templates/layouts/重庆大学/重庆大学logo.png
templates/layouts/重庆大学/重庆大学logo2.png
templates/layouts/中国电建_*/水电三局logo.png
templates/layouts/中国电建_*/电建logo.png
templates/layouts/中国电建_*/中国水务logo.png
templates/layouts/中国电建_*/华东院logo.png
```

**문제**: 파일 시스템 식별자에 비-ASCII 문자가 있으면 자동화 도구, 빌드 시스템, 객체 스토리지, URL 라우팅 모두에서 문제 발생 가능 (인코딩 이슈, 매크로 안전성, 검색 가능성). [06-bilingual-conventions.md](06-bilingual-conventions.md) 의 "트랙 A: 기계 식별자" 위반.

**리네임 매핑** (디렉토리):

| 현재 | 영문 키 | 의미 |
|------|---------|------|
| `中国电建_常规` | `china_power_construction_standard` | 中国电力建设集团 표준 |
| `中国电建_现代` | `china_power_construction_modern` | 동, 모던 |
| `中汽研_常规` | `caar_standard` | 中国汽车技术研究中心 표준 |
| `中汽研_现代` | `caar_modern` | 동, 모던 |
| `中汽研_商务` | `caar_business` | 동, 비즈니스 |
| `招商银行` | `cmb_bank` | China Merchants Bank |
| `重庆大学` | `chongqing_university` | 충칭대학교 |

**리네임 매핑** (파일):

| 현재 | 영문 |
|------|------|
| `重庆大学logo.png` | `cqu_logo.png` |
| `重庆大学logo2.png` | `cqu_logo_alt.png` |
| `水电三局logo.png` | `hydropower_bureau3_logo.png` |
| `电建logo.png` | `power_construction_logo.png` |
| `中国水务logo.png` | `china_water_logo.png` |
| `华东院logo.png` | `east_china_institute_logo.png` |

**부수 작업**:
- `templates/layouts/layouts_index.json` 의 키도 동시에 영문화
- 같은 파일 안의 설명 텍스트에 `summary_en`, `summary_ko` 병기 ([06-bilingual-conventions.md §6.5.3](06-bilingual-conventions.md))
- 각 디렉토리의 `design_spec.md`, `*.svg` 안에서 자산 파일을 참조하는 경로도 모두 갱신
- `examples/` 디렉토리는 14개 모두 중국어 이름이므로 **edit2ppt 에서는 제거** (서버 product 에 examples 디렉토리 자체가 불필요)

**자동화**: 매핑 dict 를 갖춘 Python 리네임 스크립트 작성. `git mv` 사용 (히스토리 보존). 이후 grep으로 누락 참조 확인:
```bash
grep -rIn "中国电建\|中汽研\|招商银行\|重庆大学\|水电三局logo\|华东院logo" src/
```

우선순위: **Critical** (M1 작업)

작업량: 1-2일 (리네임 자체는 1시간, SVG/JSON/MD 안의 참조 갱신과 회귀 테스트가 더 큼)

---

## 3.5 패치 우선순위 종합

| ID | 위치 | 우선순위 | 작업량 | 효과 |
|----|------|----------|--------|------|
| G1 | drawingml_utils.py:427-433 | **Critical** | 30분 | Hangul 텍스트 폭 정확히 계산 |
| G2 | pptx_notes.py:75,80,85 + drawingml_elements.py:1002 | **Critical** | 1-2시간 | OOXML lang 동적 결정 |
| G3 | drawingml_utils.py:31-88 + config.py:409 | **High** | 2-3시간 | 한국어 폰트 매핑/기본값 |
| G4 | references/strategist.md | **High** | 4-8시간 | 한국어 디자인 의사결정 품질 |
| G5 | references/executor-*.md | **High** | 4-8시간 | 한국어 레이아웃 품질 |
| G6 | references/image-generator.md | **Medium** | 2-4시간 | 한국 시각 자산 품질 |
| G7 | references/image-searcher.md | **Medium** | 1-2시간 | 한글 쿼리 우회 |
| G8 | templates/layouts/ | **Medium** | 1-3일 | 한국 컨텍스트 레이아웃 |
| G9 | templates/{design_spec,spec_lock}_reference.md | **Medium** | 1-2시간 | 한국어 폰트 스택 예시 |
| G10 | tts_backends/backend_edge.py + notes_to_audio.py | **Low** | 1-2시간 | 한국어 내레이션 기본값 |
| G11 | source_to_md/web_to_md.py:126 | **Low** | 30분 | 한국 사이트 스크래핑 |
| G12 | README.ko.md 작성 | **Low** | 4-8시간 | 사용자 온보딩 |
| **G13** | **중국어 디렉토리/파일 → 영문 리네임** | **Critical** | **1-2일** | **자동화 친화성, 인코딩 안전성** |

**Critical 4개 (G1/G2/G3/G13) 처리하면 한국어 PPT가 "작동" + 파일 시스템 "안전"**. High까지 처리하면 "쓸만한 품질", Medium까지 가면 "한국어 네이티브" 수준.

다음: [04-integration-plan.md](04-integration-plan.md) 에서 서버 아키텍처, [06-bilingual-conventions.md](06-bilingual-conventions.md) 에서 컨벤션 상세.
