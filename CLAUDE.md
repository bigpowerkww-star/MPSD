# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 상태

**v1.0 완성.** 1~4단계 구현 + 검토 + 지적사항 7건(중대 1, 경미 6) 수정 완료. 남은 것은 [REVIEW.md](REVIEW.md) 5.2의 Should/Could 4건뿐이다.

주차별 Excel 매출 실행계획 파일 2개(전주/금주)를 업로드하면 변동을 자동 분석하는 Streamlit 로컬 웹앱이다. 상세 요구사항(FR-101~FR-508)은 PRD에만 있으니 코드를 쓰기 전에 읽을 것.

- [PRD_매출계획_변동분석.md](PRD_매출계획_변동분석.md) — 요구사항 원본 (FR-101~FR-508)
- [REVIEW.md](REVIEW.md) — **PRD 대조표 + 수정 이력 + 남은 항목**. 코드를 고치기 전에 여기부터 볼 것
- [PROMPTS_단계별.md](PROMPTS_단계별.md) — 단계별 실행 프롬프트 (완료됨, 이력용)

`app.py` + `core/` 5개 모듈 + `tests/` **223개 통과**. Must 요구사항 32건 전부 충족.

git 저장소가 아니다.

## 개발 환경

Python 3.14.6 (Anaconda, `C:\Users\User\anaconda3`). `.venv`는 이걸로 생성되어 있다.

> **`python`을 그냥 부르지 말 것.** PATH의 `python`은 Microsoft Store 스텁이라 `--version`을 줘도 "Python"만 찍고 끝난다. 항상 `.venv\Scripts\python.exe`를 쓴다.

```bash
run.bat                                              # venv 자동 생성 + 설치 + 실행
.venv/Scripts/streamlit.exe run app.py               # 직접 실행 (localhost:8501)
.venv/Scripts/python.exe -m pytest tests/ -q         # 전체 테스트
.venv/Scripts/python.exe -m pytest tests/test_regression.py -v        # 회귀 기준값만
.venv/Scripts/python.exe -m pytest tests/test_regression.py::TestJulyBaseline::test_amount -v   # 단일 테스트
```

설치된 주요 버전: **pandas 3.0.5, numpy 2.5.2, streamlit 1.62.0, plotly 7.0.0, openpyxl 3.1.5.** pandas 3.x는 copy-on-write가 기본이고 2.x와 동작이 다른 API가 있으니, 웹에서 찾은 pandas 2.x 예제를 그대로 옮기지 말 것.

`.env`의 `OPENAI_API_KEY`는 이 앱이 쓰지 않는다. 이전 프로젝트 잔여물.

## 모듈 구조

```
app.py                UI 조립만. 계산 로직 금지
core/schema.py        컬럼 정의, 복합키, 측정값 분류, 단위, build_key(), validate_headers()
core/loader.py        load_plan(bytes)             -> LoadResult
core/differ.py        build_diff(before_df, after_df) -> DiffResult
core/aggregator.py    aggregate / build_matrix / period_detail / period_labels / rows_for_axis
core/formatter.py     Styler, 숫자·증감 포맷, 색상, 워터폴 데이터, xlsx/csv 내보내기
tests/conftest.py     before/after fixture (samples/ 두 파일을 파싱해 세션 캐시)
```

데이터 흐름: `load_plan` → `build_diff`(행 단위 효과까지 계산) → `aggregate`(합산) → `build_matrix`/`period_detail`(화면용) → `formatter`(표시).

`?demo=1`로 접속하면 `samples/` 파일을 자동 로드한다. 업로드 없이 화면을 확인할 때 쓴다.

`core/`에는 **Streamlit을 import 하지 않는다.** pytest로 단독 검증 가능해야 한다.

### 자료구조

- `LoadResult` — `df`, `sheet_name`, `header_row`, `row_count`, `period_min/max`, `periods`, `warnings`. `warnings`는 화면에 그대로 노출할 문자열 목록이다. 조용히 삼키지 말 것.
- `DiffResult` — `rows`(행 단위 diff), `duplicate_keys`, `excluded_row_count`, `changed_count`, `status_counts()`.
- `Matrix` — `after / before / delta / pct / changed` 다섯 DataFrame이 **같은 index·columns**를 가진다. 인덱스 끝에 `총계` 행, 컬럼 끝에 `합계` 열.
- `PeriodDetail` — 요약 수치 + `effects` dict + `contributors` + `added_rows/removed_rows` + `monthly_trend`.

`diff.rows`의 컬럼 규약: 측정값마다 `{측정값}_전`, `{측정값}_후`, `{측정값}_증감`. 접미사는 `schema.BEFORE_SUFFIX` 등 상수를 쓴다.

### 측정값 분류 — 아무거나 SUM 하면 안 된다

- `schema.ADDITIVE_MEASURES` (수량·금액·영업이익·손익) — 합계 가능
- `schema.RATE_MEASURES` (단가·Base·Extra·운임·총원가) — 톤당 값. **SUM 금지.** 집계 시 `Σ(rate×수량)/Σ수량`으로 재계산된다
- `schema.EFFECT_COLUMNS` (물량효과·단가효과·Base/Extra/운임기여) — 백만원, 합계 가능

`aggregate(rows, axes, period_unit)`가 이 구분을 알아서 처리한다. `axes=()`면 전체 합계, `period_unit=None`이면 기간을 나누지 않는다. 총계와 세부가 **같은 코드 경로**를 타므로 항상 정합한다 — 별도 총계 계산을 새로 만들지 말 것.

## 데이터 모델 (실제 샘플 파일 분석으로 검증됨)

입력 Excel의 구조는 일반적인 표가 아니다. 이걸 모르고 `pd.read_excel()`을 그냥 부르면 깨진다.

- 단일 시트 **`후판`**, 범위 `A1:BC282` — 55컬럼 × 데이터 277행
- **헤더가 5행 구조**: 1행 파라미터 / 2행 안내문 / 3행 단위 / 4행 *기존* 컬럼명 / **5행 *변경* 컬럼명(실제 헤더)** / 6행부터 데이터
- 헤더 행은 **행 번호로 하드코딩하지 말고 A열 값이 `변경`인 행을 탐색**해서 찾는다 (`loader._find_header_row`)
- 기준년월(T열)은 Excel date serial (46023 = 2026-01-01). epoch는 1900 윤년 버그 때문에 **1899-12-30**

### 컬럼 식별은 헤더 이름이 아니라 열 문자로 한다

5행 헤더에는 **같은 이름이 두 번 나온다**: `수량`(X, AQ), `금액`(Y, AR), `총원가`(AO, AS), `영업이익`(AL, AT). AQ~AZ가 "그룹원가 연동" 블록이라 앞쪽 실적 컬럼과 이름이 겹친다. 이름으로 컬럼을 찾으면 조용히 엉뚱한 값을 집는다.

`schema.COLUMN_MAP`이 열 문자 → 표준 컬럼명을 정의하고, `schema.EXPECTED_HEADERS`는 양식 변경 감지용 검증에만 쓴다. 그룹 블록은 `그룹_` 접두어로 구분한다. AP열은 양식상 빈 구분 열이라 매핑하지 않는다.

### 3행 단위는 신뢰하지 않는다

PRD는 3행에서 단위를 읽으라고 했지만, 실제로는 열 정렬이 어긋난 셀이 있어(구 양식 필드 힌트가 섞여 있음) 그대로 쓸 수 없다. 표시용 단위는 `schema.UNITS`에 선언한 값을 쓴다. **PRD 3.1과 의도적으로 다른 부분이다.**

### 행 매칭 복합키

**차원 18개(B~S: 사업부·영업실·공장·제품군 대분류·품종·품명·규격_분류·제품_특성·제품_용도·내수수출·회의자료_대분류·팀파트·고객그룹·글로벌_고객그룹·정품구분·비고·고객명·권역명) + 기준년월(T)**

샘플 277행에서 이 조합이 100% 유니크임을 확인했다. **`비고`를 키에서 빼면 안 된다** — 6·7·8행은 비고(전기협상가/당기협상가/기타)만 다르다.

### 검증된 항등식

```
단가(Z)  = Base(AA) + Extra(AB) + 운임(AC)
금액(Y)  = 수량(X) × 단가(Z) ÷ 1,000        # 톤 × 천원/톤 → 백만원
         = (BA + BB + BC) ÷ 1,000           # Basex수량 + Extrax수량 + 운임x수량
```

이 항등식이 앱 전체 계산의 근거다. 파서를 고칠 때 검산에 쓸 것.

## 분석 로직의 핵심

단순 증감 표시가 아니라 **변동 원인 분해**가 이 앱의 존재 이유다.

```
Q0,P0 = 변동 전 수량·단가       Q1,P1 = 변동 후 수량·단가

물량효과 = (Q1 − Q0) × P0 ÷ 1,000
단가효과 = Q1 × (P1 − P0) ÷ 1,000     → Base/Extra/운임 기여로 재분해
────────────────────────────────
합계 = 금액 증감 (잔차 0)
```

신규/삭제 행은 단가 비교 대상이 없으므로 **단가효과로 배분하지 않는다.** 전액 물량효과로 잡고 별도 표기한다. 섞으면 단가효과가 왜곡된다.

## 자주 틀리는 지점

- **원본 분기/반기 컬럼을 그대로 믿으면 안 된다.** 샘플 데이터에 4월인데 분기=1, 7월인데 분기=1로 찍힌 행이 실제로 섞여 있다(분기 3건, 반기 1건). 그대로 쓰면 **월 합계 ≠ 분기 합계**가 되어 집계 전체를 신뢰할 수 없다. `loader._coerce_period_part`가 기준년월과 대조해 보정하고 경고를 남긴다. 이 보정을 끄지 말 것.
- **차원 값의 빈 셀이 숫자 `0`으로 저장되어 있다** (특히 비고 열). `_clean_dimension`이 `None`/빈문자/숫자 0을 전부 `"미지정"`으로 통일한다. 이게 없으면 파일 간 키가 어긋난다.
- **집계 단가는 가중평균**(Σ금액 ÷ Σ수량 × 1,000). 행 단가의 산술평균을 내면 물량 구성이 다른 항목이 뒤섞여 틀린 값이 나온다.
- **효과 계산은 행 단위로 먼저, 합산은 나중에.** 집계된 평균단가로 효과를 계산하면 구성비 변화(mix)가 단가효과로 잘못 섞인다.
- **Streamlit은 위젯 조작마다 스크립트 전체를 재실행한다.** 파싱·diff를 `@st.cache_data`로 감싸지 않으면 축을 바꿀 때마다 Excel을 다시 판다. 캐시 키는 `UploadedFile` 객체가 아니라 **파일 바이트 해시**를 쓸 것.
- **`st.metric`의 기본 delta 색상은 증가=초록**이다. 국내 재무 관행(증가=빨강 ▲, 감소=파랑 ▼)과 반대이므로 `delta_color`를 조정해야 한다.
- 증감률 분모가 0(신규 행)일 때 `∞`/`NaN`을 노출하지 말고 `신규`로 표기한다.
- **`st.set_page_config`에 `initial_sidebar_state="expanded"`가 필요하다.** 기본값 `"auto"`는 창이 좁으면 사이드바를 접어버리는데, 이 앱은 조작 위젯이 전부 사이드바에 있어서 접히면 아무것도 못 한다.
- **pandas 3.0: 문자열 Series의 `.values`가 `ArrowStringArray`를 반환한다.** `set_index`/`merge`에 넘기면 `TypeError: unhashable type`으로 터진다. `.to_numpy()`를 쓰거나 Series를 그대로 넘길 것. `differ._with_key`가 키를 `object` dtype으로 고정하는 이유다.
- **`st.metric`의 색은 `delta_color`에 색 이름을 직접 준다.** `"normal"`은 증가=초록, `"inverse"`는 **감소=초록**이라 둘 다 국내 관행과 어긋난다. `formatter.metric_delta_color()`가 부호를 보고 `red`/`blue`를 고른다. Streamlit 1.62+ 필요.
- **`delta_arrow`도 함께 명시해야 한다.** Streamlit은 delta **문자열**을 파싱해 화살표 방향을 정하는데, 우리는 부호 대신 색·화살표로 방향을 표현해 문자열에 마이너스가 없다. 그대로 두면 감소인데 위 화살표가 뜬다.
- **축 조합 문자열을 `" / "`로 자르지 말 것.** 2단계 축은 값을 `" / "`로 이어 붙이는데, 축 값 자체에 `/`가 들어간다(`후판_건설/철구`, `후판_유통/기타`). 라벨을 되돌리려면 자르지 말고 `_axis_index()`로 **다시 계산해서 비교**한다 — `rows_for_axis`가 그렇게 한다. 이 방식으로 고치기 전에 실제 버그가 있었다(REVIEW.md 3.1).
- **총계 행 라벨을 `"총계"`로 하드코딩하지 말 것.** 축 값에 `총계`가 있으면 충돌하므로 `Matrix.total_label`이 다른 이름을 고른다. 총계는 표의 **첫 행**이다(마지막 아님).
- **워터폴 Y축은 반드시 좁혀야 한다.** 금액 ≈1,430,000에 효과 ≈−10,000이라 0부터 그리면 차이가 보이지 않는다. `formatter.waterfall_yrange()`가 누적 경로에 맞춰 범위를 잡는다.

## 회귀 기준값

`samples/`의 8-1주 → 8-2주 파일로 아래가 재현되어야 한다. 로직 리팩터링의 안전망이므로 `tests/test_regression.py`로 자동화한다.

| 항목 | 기대값 |
|---|---|
| 파싱 행 수 | 각 277행 |
| 복합키 유니크 | 277 / 277 |
| 변동 행 수 | 26행, 전부 2026-07 |
| 2026-01~06 증감 | 전부 0 |
| 2026-07 수량 | 1,075,073 → 1,067,673 톤 (−7,400) |
| 2026-07 금액 | 1,430,709.9 → 1,419,218.7 백만원 (−11,491.2, −0.80%) |
| 신규/삭제 행 | 0건 |

샘플은 `samples/8-1주.xlsx`, `samples/8-2주.xlsx`로 복사되어 있고 `tests/conftest.py`의 `before`/`after` fixture가 이를 로드한다. 원본은 `C:\Users\User\Downloads\후판) □□영업본부_'26년 실행계획_신규양식_실습용 더미데이터_8-{1,2}주.xlsx`.

**8개 항목 전부 `tests/test_regression.py`에서 통과 중이다.** 총계는 9,117,124.4 → 9,105,633.2.

7월 변동의 실제 분해 결과 (이 숫자가 바뀌면 로직이 바뀐 것이다):

| | 백만원 | 비중 |
|---|---:|---:|
| 물량효과 | −10,058.1 | 87.5% |
| 단가효과 | −1,433.1 | 12.5% |
| └ Base기여 | −1,433.1 | 12.5% |
| └ Extra·운임기여 | 0.0 | 0% |

내수 3개 파트는 물량 그대로 단가만 하락, 수출 3개 파트는 단가 그대로 물량만 감소로 깔끔히 갈린다.

### 테스트 파일 구성

| 파일 | 역할 |
|---|---|
| `test_loader.py` | 파싱·복합키·항등식 |
| `test_differ.py` | 효과 분해 항등식, 상태 분류, 가중평균, 매트릭스, 전 조합 렌더 |
| `test_regression.py` | PRD 9.1 실측 기준값 |
| `test_app_smoke.py` | `AppTest`로 앱 전체 렌더 + **캐시 적중 검증** + 색상/화살표 방향 |
| `test_drilldown.py` | 드릴다운이 표와 같은 모집단을 고르는지 + 포맷 회귀 (REVIEW.md 지적사항 고정) |
| `test_cacheability.py` | `LoadResult` 피클 가능 여부 |

`test_app_smoke.py`는 `file_uploader`를 조작할 수 없는 대신 **`session_state`에 파일 바이트를 미리 넣어** 업로드 이후 경로를 전부 태운다. 앱이 업로드 즉시 `getvalue()`로 바이트를 뜨고 그 뒤로는 `session_state`만 읽도록 설계된 덕분이다 — 이 구조를 깨면 스모크 테스트도 같이 죽는다.

## 구조 원칙

계산 로직은 `core/`(schema / loader / differ / aggregator / formatter), `app.py`는 UI 조립만 한다. Streamlit에 묶이지 않아야 pytest로 로직을 단독 검증할 수 있다. **M2(분석 엔진)까지는 UI 없이 pytest로 회귀 기준값을 맞춘 뒤 화면을 붙인다** — 숫자가 틀렸을 때 로직 문제인지 렌더링 문제인지 헤매지 않기 위해서다.

`.streamlit/config.toml`의 `address = "localhost"`와 `gatherUsageStats = false`는 사내 데이터 보호를 위한 필수 설정이다. 기본값이면 서버가 모든 네트워크 인터페이스에 열리고 사용 통계가 외부로 나간다.
