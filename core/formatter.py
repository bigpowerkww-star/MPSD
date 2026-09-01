"""표시 계층 — 숫자 포맷, 증감 표기, Styler.

Streamlit 의존 없음. 계산은 하지 않는다. aggregator 가 낸 값을 보기 좋게 만들 뿐이다.

색상 규칙 (PRD 6.2)
-------------------
국내 재무 관행을 따른다: **증가 = 빨강 ▲, 감소 = 파랑 ▼, 무변동 = 회색 −**
(`st.metric` 의 기본 delta 색상은 증가=초록이라 반대다. 호출부에서 뒤집어야 한다.)
"""

from __future__ import annotations

import importlib.util
from io import BytesIO

import numpy as np
import pandas as pd

from . import schema
from .aggregator import TOTAL_LABEL, Matrix

# ---------------------------------------------------------------------------
# 색상
# ---------------------------------------------------------------------------
INCREASE_COLOR = "#C62828"   # 빨강 — 증가
DECREASE_COLOR = "#1565C0"   # 파랑 — 감소
NEUTRAL_COLOR = "#9E9E9E"    # 회색 — 무변동
NEW_COLOR = "#6A1B9A"        # 보라 — 신규/삭제

INCREASE_BG = "rgba(198, 40, 40, 0.07)"
DECREASE_BG = "rgba(21, 101, 192, 0.07)"

UP_MARK = "▲"
DOWN_MARK = "▼"
FLAT_MARK = "−"
EMPTY_MARK = "—"
NEW_MARK = "신규"
REMOVED_MARK = "삭제"

#: 측정값별 소수 자릿수
DECIMALS: dict[str, int] = {
    "금액": 0, "수량": 0, "영업이익": 0, "손익": 0,
    "단가": 1, "Base": 1, "Extra": 1, "운임": 1, "총원가": 1,
}
PCT_DECIMALS = 2

#: 이 값 이하의 증감은 무변동으로 표시한다(표시 전용. 판정은 differ 가 한다).
DISPLAY_EPS = 5e-2

#: 표시 모드 (PRD FR-405)
DISPLAY_MODES: list[str] = ["값 + 증감", "값 + 증감률", "값 + 증감 + 증감률", "값만"]
DEFAULT_MODE = DISPLAY_MODES[0]


# ---------------------------------------------------------------------------
# 스칼라 포맷
# ---------------------------------------------------------------------------

def decimals_for(measure: str) -> int:
    return DECIMALS.get(measure, 1)


def fmt_number(value, decimals: int = 1) -> str:
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return EMPTY_MARK
    text = f"{value:,.{decimals}f}"
    # -0.04 를 소수 1자리로 찍으면 "-0.0" 이 된다. 음수 0 은 표기하지 않는다.
    if text.startswith("-") and float(text.replace(",", "")) == 0.0:
        text = text[1:]
    return text


def fmt_delta(value, decimals: int = 1, *, signed_mark: bool = True) -> str:
    """증감액. ▲1,234 / ▼1,234 / −"""
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return EMPTY_MARK
    if abs(value) < DISPLAY_EPS:
        return FLAT_MARK
    mark = (UP_MARK if value > 0 else DOWN_MARK) if signed_mark else ""
    return f"{mark}{abs(value):,.{decimals}f}"


def fmt_pct(pct, delta=None) -> str:
    """증감률. 분모가 0이면 ∞/NaN 대신 '신규' 또는 '−' (FR-206)."""
    if pct is None or (isinstance(pct, float) and (np.isnan(pct) or np.isinf(pct))):
        if delta is not None and not _is_flat(delta):
            return NEW_MARK if delta > 0 else REMOVED_MARK
        return EMPTY_MARK
    if abs(pct) < 10 ** (-PCT_DECIMALS):
        return FLAT_MARK
    mark = UP_MARK if pct > 0 else DOWN_MARK
    return f"{mark}{abs(pct):,.{PCT_DECIMALS}f}%"


def _is_flat(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return True
    return abs(value) < DISPLAY_EPS


def delta_color(value) -> str:
    """CSS 색상값."""
    if _is_flat(value):
        return NEUTRAL_COLOR
    return INCREASE_COLOR if value > 0 else DECREASE_COLOR


def metric_delta_color(value) -> str:
    """st.metric 의 delta_color 에 넘길 색 이름.

    "normal"(증가=초록) 도 "inverse"(감소=초록) 도 국내 관행과 맞지 않아
    부호를 보고 red/blue 를 직접 지정한다.
    """
    if _is_flat(value):
        return "gray"
    return "red" if value > 0 else "blue"


def metric_delta_arrow(value) -> str:
    """st.metric 의 delta_arrow.

    Streamlit 은 delta **문자열**을 파싱해 방향을 정한다. 우리는 부호 대신
    화살표와 색으로 방향을 보여주므로 문자열에 마이너스가 없다.
    그대로 두면 감소인데 위쪽 화살표가 뜬다. 반드시 명시할 것.
    """
    if _is_flat(value):
        return "off"
    return "up" if value > 0 else "down"


# ---------------------------------------------------------------------------
# 메인 표
# ---------------------------------------------------------------------------

def build_display_frame(matrix: Matrix, mode: str = DEFAULT_MODE) -> pd.DataFrame:
    """값 + 증감을 한 셀에 합쳐 문자열 DataFrame 을 만든다 (FR-404)."""
    decimals = decimals_for(matrix.measure)
    after, delta, pct = matrix.after, matrix.delta, matrix.pct

    def cell(a, d, p) -> str:
        value = fmt_number(a, decimals)
        if mode == "값만":
            return value
        if mode == "값 + 증감":
            return f"{value}  {fmt_delta(d, decimals)}"
        if mode == "값 + 증감률":
            return f"{value}  {fmt_pct(p, d)}"
        return f"{value}  {fmt_delta(d, decimals)} ({fmt_pct(p, d)})"

    data = {
        col: [cell(a, d, p) for a, d, p in zip(after[col], delta[col], pct[col])]
        for col in after.columns
    }
    out = pd.DataFrame(data, index=after.index)
    out.index.name = after.index.name
    return out


def style_matrix(matrix: Matrix, mode: str = DEFAULT_MODE):
    """표시용 Styler. 증감 방향으로 글자색과 배경 음영을 준다."""
    display = build_display_frame(matrix, mode)
    delta = matrix.delta.reindex(index=display.index, columns=display.columns)

    def css(_: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=display.index, columns=display.columns)
        for col in display.columns:
            values = delta[col]
            styles[col] = [_cell_css(v) for v in values]
        if matrix.total_label in styles.index:
            styles.loc[matrix.total_label] = [
                s + "font-weight:700;border-bottom:2px solid #444;"
                for s in styles.loc[matrix.total_label]
            ]
        if TOTAL_LABEL in styles.columns:
            styles[TOTAL_LABEL] = [s + "font-weight:700;" for s in styles[TOTAL_LABEL]]
        return styles

    return display.style.apply(css, axis=None).set_properties(
        **{"text-align": "right", "font-variant-numeric": "tabular-nums"}
    )


def _cell_css(value) -> str:
    if _is_flat(value):
        return f"color:{NEUTRAL_COLOR};"
    if value > 0:
        return f"color:{INCREASE_COLOR};background-color:{INCREASE_BG};"
    return f"color:{DECREASE_COLOR};background-color:{DECREASE_BG};"


# ---------------------------------------------------------------------------
# 기여도 / 원본 행 표
# ---------------------------------------------------------------------------

def style_contributors(df: pd.DataFrame, axis: str):
    """기여도 표. 증감 관련 컬럼만 색을 준다."""
    if df.empty:
        return df.style

    numeric_formats = {}
    for col in df.columns:
        if col == axis:
            continue
        if col.endswith("_증감률"):
            numeric_formats[col] = lambda v: fmt_pct(v)
        elif col.startswith("수량"):
            numeric_formats[col] = lambda v: fmt_number(v, 0)
        else:
            numeric_formats[col] = lambda v: fmt_number(v, 1)

    signed = [c for c in df.columns
              if c.endswith(schema.DELTA_SUFFIX) or c.endswith("_증감률")
              or c in schema.EFFECT_COLUMNS]

    styler = df.style.format(numeric_formats)
    for col in signed:
        styler = styler.map(lambda v: f"color:{delta_color(v)};", subset=[col])
    return styler.set_properties(**{"font-variant-numeric": "tabular-nums"})


def style_row_comparison(df: pd.DataFrame, measures: list[str]):
    """원본 행 전/후 비교. 값이 바뀐 셀을 강조한다 (FR-507)."""
    if df.empty:
        return df.style

    def css(_: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for measure in measures:
            before, after = f"{measure}{schema.BEFORE_SUFFIX}", f"{measure}{schema.AFTER_SUFFIX}"
            if before not in df.columns or after not in df.columns:
                continue
            changed = ~np.isclose(df[before], df[after], rtol=schema.FLOAT_RTOL, atol=0)
            for col in (before, after):
                styles[col] = np.where(changed, "background-color:rgba(255,193,7,0.18);", "")
        return styles

    formats = {}
    for col in df.columns:
        measure = col.rsplit("_", 1)[0]
        if pd.api.types.is_numeric_dtype(df[col]):
            formats[col] = lambda v, d=decimals_for(measure): fmt_number(v, d)
    return df.style.apply(css, axis=None).format(formats)


# ---------------------------------------------------------------------------
# 워터폴 차트 데이터
# ---------------------------------------------------------------------------

def waterfall_data(detail) -> dict[str, list]:
    """변동 전 → 물량효과 → Base → Extra → 운임 → 변동 후 (FR-504).

    단가효과 = Base + Extra + 운임 이므로 구성요소만 세우면 합이 맞는다.
    """
    labels = ["변동 전", "물량효과", "Base", "Extra", "운임", "변동 후"]
    values = [
        detail.amount_before,
        detail.effects["물량효과"],
        detail.effects["Base기여"],
        detail.effects["Extra기여"],
        detail.effects["운임기여"],
        detail.amount_after,
    ]
    measures = ["absolute", "relative", "relative", "relative", "relative", "total"]
    texts = [
        fmt_number(detail.amount_before, 0),
        fmt_delta(detail.effects["물량효과"], 0),
        fmt_delta(detail.effects["Base기여"], 0),
        fmt_delta(detail.effects["Extra기여"], 0),
        fmt_delta(detail.effects["운임기여"], 0),
        fmt_number(detail.amount_after, 0),
    ]
    return {
        "labels": labels,
        "values": values,
        "measures": measures,
        "texts": texts,
        "yrange": waterfall_yrange(values),
    }


def waterfall_yrange(values: list[float]) -> list[float] | None:
    """워터폴 Y축 범위.

    금액(≈1,430,000)에 비해 효과(≈-10,000)가 훨씬 작아서 0 부터 그리면
    막대 높이 차이가 눈에 보이지 않는다. 누적 경로의 최소/최대에 맞춰 축을 좁힌다.
    """
    if not values:
        return None
    cumulative = [values[0]]
    for step in values[1:-1]:
        cumulative.append(cumulative[-1] + step)
    cumulative.append(values[-1])

    low, high = min(cumulative), max(cumulative)
    spread = high - low
    if spread <= 0:
        pad = max(abs(high) * 0.05, 1.0)
        return [low - pad, high + pad]
    # 위쪽은 라벨이 들어갈 자리를 더 준다
    return [low - spread * 0.35, high + spread * 0.55]


# ---------------------------------------------------------------------------
# 내보내기
# ---------------------------------------------------------------------------

def _excel_engine() -> str:
    """쓸 수 있는 xlsx 작성 엔진을 고른다.

    XlsxWriter 가 없는 환경(예: .venv 가 아닌 Anaconda base)에서도 다운로드가
    죽지 않게 openpyxl 로 물러선다. openpyxl 은 파서로 이미 필수 의존성이다.
    """
    for engine, module in (("xlsxwriter", "xlsxwriter"), ("openpyxl", "openpyxl")):
        if importlib.util.find_spec(module) is not None:
            return engine
    raise RuntimeError(
        "xlsx 를 만들 수 있는 엔진이 없습니다. XlsxWriter 또는 openpyxl 을 설치하세요."
    )


def matrix_to_excel(matrix: Matrix, sheet_prefix: str = "") -> bytes:
    """값 / 증감 / 증감률을 시트로 나눠 xlsx 바이트를 만든다 (FR-409)."""
    buffer = BytesIO()
    sheets = {
        "변동후": matrix.after,
        "변동전": matrix.before,
        "증감": matrix.delta,
        "증감률": matrix.pct,
    }
    with pd.ExcelWriter(buffer, engine=_excel_engine()) as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=f"{sheet_prefix}{name}"[:31])
    return buffer.getvalue()


def matrix_to_csv(matrix: Matrix, mode: str = DEFAULT_MODE) -> bytes:
    """화면에 보이는 그대로 CSV 로. Excel 이 한글을 깨지 않게 BOM 을 붙인다."""
    return build_display_frame(matrix, mode).to_csv().encode("utf-8-sig")
