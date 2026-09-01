"""매출 실행계획 주간 변동 분석 — Streamlit 엔트리포인트.

이 파일은 UI 조립만 한다. 계산은 전부 core/ 에 있다.
레이아웃은 PRD 6장 와이어프레임을 따른다: 조작 위젯은 전부 사이드바, 결과는 메인.
"""

from __future__ import annotations

import hashlib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import formatter as fmt
from core import schema
from core import aggregator
from core.aggregator import (
    AggregationError,
    build_matrix,
    period_detail,
    period_labels,
    rows_for_axis,
)
from core.differ import DiffError, DiffResult, build_diff
from core.loader import LoadResult, PlanLoadError, load_plan

st.set_page_config(
    page_title="매출 실행계획 변동 분석",
    page_icon="📊",
    layout="wide",
    # 조작 위젯이 전부 사이드바에 있으므로 접힌 채로 시작하면 안 된다.
    # 기본값 "auto" 는 창이 좁으면 접어버린다.
    initial_sidebar_state="expanded",
)

BEFORE_SLOT = "before"
AFTER_SLOT = "after"

st.markdown(
    """
    <style>
      [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
      [data-testid="stMetricDelta"] { font-variant-numeric: tabular-nums; }
      .block-container { padding-top: 2.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------
# 캐시 키는 UploadedFile 객체가 아니라 **파일 바이트** 여야 한다.
# UploadedFile 은 rerun 마다 새 객체가 되어 캐시가 매번 빗나간다.
@st.cache_data(show_spinner=False, max_entries=4, ttl=3600)
def load_cached(file_bytes: bytes, source_name: str) -> LoadResult:
    return load_plan(file_bytes, source_name=source_name)


@st.cache_data(show_spinner=False, max_entries=2, ttl=3600)
def diff_cached(
    before_bytes: bytes, after_bytes: bytes, before_name: str, after_name: str
) -> DiffResult:
    before = load_cached(before_bytes, before_name)
    after = load_cached(after_bytes, after_name)
    return build_diff(before.df, after.df, before_name, after_name)


def file_digest(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 표시 헬퍼
# ---------------------------------------------------------------------------
def signed_metric(column, label: str, value: float, delta: float | None = None,
                  decimals: int = 1, pct: float | None = None) -> None:
    """증가=빨강 / 감소=파랑 으로 표시한다 (PRD 6.2, 국내 재무 관행).

    st.metric 기본값은 증가=초록이고, delta_color="inverse" 는 증가=빨강/**감소=초록**이라
    둘 다 관행과 맞지 않는다. Streamlit 1.62 부터 delta_color 에 색 이름을 직접 줄 수 있어
    부호에 따라 red/blue 를 명시한다. 이 때문에 requirements 가 streamlit>=1.62 이다.
    """
    delta_text = None
    color, arrow = "gray", "off"
    if delta is not None:
        delta_text = fmt.fmt_delta(delta, decimals, signed_mark=False)
        if delta_text not in (fmt.FLAT_MARK, fmt.EMPTY_MARK):
            color = fmt.metric_delta_color(delta)
            arrow = fmt.metric_delta_arrow(delta)
            if pct is not None and not pd.isna(pct):
                delta_text += f"  ({abs(pct):.2f}%)"
        else:
            delta_text = None
    column.metric(
        label,
        fmt.fmt_number(value, decimals),
        delta=delta_text,
        delta_color=color,
        delta_arrow=arrow,
    )


def render_waterfall(detail) -> go.Figure:
    data = fmt.waterfall_data(detail)
    figure = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=data["measures"],
            x=data["labels"],
            y=data["values"],
            text=data["texts"],
            textposition="outside",
            connector={"line": {"color": "#BDBDBD"}},
            increasing={"marker": {"color": fmt.INCREASE_COLOR}},
            decreasing={"marker": {"color": fmt.DECREASE_COLOR}},
            totals={"marker": {"color": "#546E7A"}},
        )
    )
    figure.update_layout(
        height=380,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        yaxis_title="백만원",
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        uniformtext={"mode": "show", "minsize": 10},
    )
    # 0 부터 그리면 1,430,000 막대에 가려 -10,000 효과가 보이지 않는다.
    figure.update_yaxes(
        gridcolor="rgba(0,0,0,0.07)",
        zerolinecolor="rgba(0,0,0,0.2)",
        range=data["yrange"],
        tickformat=",.0f",
    )
    return figure


def render_trend(trend: pd.DataFrame) -> go.Figure:
    """월별 증감 막대. 증가는 빨강, 감소는 파랑 — 부호마다 색이 달라야 한다."""
    delta_column = f"금액{schema.DELTA_SUFFIX}"
    values = trend[delta_column]
    figure = go.Figure(
        go.Bar(
            x=trend["기간"],
            y=values,
            marker_color=[fmt.delta_color(v) for v in values],
            text=[fmt.fmt_delta(v, 0) for v in values],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:,.1f} 백만원<extra></extra>",
        )
    )
    figure.update_layout(
        height=240,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        yaxis_title="백만원",
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
    )
    figure.update_yaxes(gridcolor="rgba(0,0,0,0.07)", zerolinecolor="rgba(0,0,0,0.3)",
                        tickformat=",.0f")
    return figure


def colored_stat(container, label: str, value: float, decimals: int = 0,
                 note: str | None = None) -> None:
    """부호에 따라 색이 붙는 수치 표시.

    st.metric 은 값 자체에 색을 줄 수 없고 좁은 폭에서 숫자가 잘린다.
    효과 분해처럼 부호가 핵심인 값은 직접 그린다.
    """
    color = fmt.delta_color(value)
    mark = fmt.UP_MARK if value > 0 else (fmt.DOWN_MARK if value < 0 else "")
    container.markdown(
        f"<div style='line-height:1.35'>"
        f"<div style='font-size:0.8rem;color:#555'>{label}</div>"
        f"<div style='font-size:1.5rem;font-weight:650;color:{color};"
        f"font-variant-numeric:tabular-nums'>{mark}{fmt.fmt_number(abs(value), decimals)}</div>"
        + (f"<div style='font-size:0.78rem;color:#777'>{note}</div>" if note else "")
        + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 사이드바 — 업로드
# ---------------------------------------------------------------------------
def capture_upload(slot: str, label: str, help_text: str) -> None:
    """업로드 즉시 bytes 를 확보해 session_state 에 보관한다.

    UploadedFile 은 rerun 을 넘기면 읽을 수 없게 될 수 있으므로 getvalue() 로 떠 둔다.
    """
    uploaded = st.file_uploader(label, type=["xlsx"], key=f"upload_{slot}", help=help_text)
    if uploaded is not None:
        st.session_state[f"{slot}_bytes"] = uploaded.getvalue()
        st.session_state[f"{slot}_name"] = uploaded.name


def slot_payload(slot: str) -> tuple[bytes | None, str]:
    return st.session_state.get(f"{slot}_bytes"), st.session_state.get(f"{slot}_name", "")


def seed_from_samples() -> bool:
    """?demo=1 로 접속하면 samples/ 파일을 자동 로드한다 (개발·시연용).

    업로드 없이 화면을 확인하기 위한 것이다. 파일이 없으면 아무 일도 하지 않는다.
    """
    from pathlib import Path

    samples = Path(__file__).parent / "samples"
    pairs = ((BEFORE_SLOT, "8-1주.xlsx"), (AFTER_SLOT, "8-2주.xlsx"))
    if not all((samples / name).exists() for _, name in pairs):
        return False
    for slot, name in pairs:
        st.session_state[f"{slot}_bytes"] = (samples / name).read_bytes()
        st.session_state[f"{slot}_name"] = name
    return True


if st.query_params.get("demo") and BEFORE_SLOT + "_bytes" not in st.session_state:
    if seed_from_samples():
        st.rerun()


def swap_slots() -> None:
    for suffix in ("bytes", "name"):
        a, b = f"{BEFORE_SLOT}_{suffix}", f"{AFTER_SLOT}_{suffix}"
        st.session_state[a], st.session_state[b] = (
            st.session_state.get(b),
            st.session_state.get(a),
        )


with st.sidebar:
    st.subheader("📂 파일")
    capture_upload(BEFORE_SLOT, "변동 전 (전주)", "지난주 실행계획 xlsx")
    capture_upload(AFTER_SLOT, "변동 후 (금주)", "이번주 실행계획 xlsx")

    both_ready = all(slot_payload(s)[0] is not None for s in (BEFORE_SLOT, AFTER_SLOT))
    if st.button("⇄ 전/후 교체", width="stretch", disabled=not both_ready):
        swap_slots()
        st.rerun()


# ---------------------------------------------------------------------------
# 파일 파싱
# ---------------------------------------------------------------------------
before_bytes, before_name = slot_payload(BEFORE_SLOT)
after_bytes, after_name = slot_payload(AFTER_SLOT)

st.title("매출 실행계획 변동 분석")

if before_bytes is None and after_bytes is None:
    st.info(
        "왼쪽 사이드바에서 **변동 전(전주)** 과 **변동 후(금주)** Excel 파일을 업로드하세요.\n\n"
        "같은 양식의 두 주차 파일을 비교해 증감을 계산하고, "
        "변동 원인을 **물량효과**와 **단가효과**로 나눠 보여줍니다."
    )
    st.stop()


def parse_slot(title: str, data: bytes | None, name: str) -> LoadResult | None:
    if data is None:
        return None
    try:
        with st.spinner(f"{title} 파싱 중..."):
            return load_cached(data, name)
    except PlanLoadError as exc:
        st.error(f"**{title}** `{name}` — {exc}")
    except Exception as exc:  # 예상치 못한 예외로 앱이 죽지 않게 (PRD 8장)
        st.error(f"**{title}** `{name}` — 예상치 못한 오류가 발생했습니다.")
        st.exception(exc)
    return None


before_result = parse_slot("변동 전", before_bytes, before_name)
after_result = parse_slot("변동 후", after_bytes, after_name)

if before_result is None or after_result is None:
    missing = "변동 전(전주)" if before_result is None else "변동 후(금주)"
    if before_bytes is None or after_bytes is None:
        st.info(f"**{missing}** 파일이 아직 없습니다. 두 파일이 모두 있어야 분석할 수 있습니다.")
    ready = before_result or after_result
    if ready:
        with st.expander("업로드된 파일 정보", expanded=True):
            st.write(
                f"`{ready.source_name}` · {ready.row_count:,}행 · {ready.period_range} · "
                f"시트 `{ready.sheet_name}` (헤더 {ready.header_row}행)"
            )
            for warning in ready.warnings:
                st.warning(warning)
    st.stop()

if list(before_result.df.columns) != list(after_result.df.columns):
    only_before = set(before_result.df.columns) - set(after_result.df.columns)
    only_after = set(after_result.df.columns) - set(before_result.df.columns)
    st.error("두 파일의 컬럼 구성이 다릅니다. 같은 양식인지 확인하세요.")
    if only_before:
        st.write("변동 전에만 있는 컬럼:", sorted(only_before))
    if only_after:
        st.write("변동 후에만 있는 컬럼:", sorted(only_after))
    st.stop()

if before_bytes == after_bytes:
    st.warning("두 슬롯에 **같은 파일**이 올라와 있습니다. 변동 전/후 파일을 각각 지정하세요.")

# 한쪽 파일에만 있는 기간은 행 전체가 신규/삭제로 잡힌다. 계획이 통째로 없어진 것으로
# 오해하기 쉬우므로 미리 알려준다 (PRD 8장 "기간 범위가 서로 다름").
only_before_periods = sorted(set(before_result.periods) - set(after_result.periods))
only_after_periods = sorted(set(after_result.periods) - set(before_result.periods))
if only_before_periods or only_after_periods:
    lines = ["**두 파일의 계획 기간이 다릅니다.** 한쪽에만 있는 기간의 행은 신규/삭제로 잡힙니다."]
    if only_before_periods:
        lines.append(f"- 변동 전에만 있음 → 전부 *삭제* 로 집계: `{', '.join(only_before_periods)}`")
    if only_after_periods:
        lines.append(f"- 변동 후에만 있음 → 전부 *신규* 로 집계: `{', '.join(only_after_periods)}`")
    st.info("\n".join(lines))

st.caption(
    f"`{before_name}` → `{after_name}` ·  "
    f"{after_result.row_count:,}행 · {after_result.period_range} · "
    f"시트 `{after_result.sheet_name}`"
)

all_warnings = [(before_name, w) for w in before_result.warnings]
all_warnings += [(after_name, w) for w in after_result.warnings]
if all_warnings:
    with st.expander(f"⚠ 데이터 확인 필요 ({len(all_warnings)}건)"):
        for name, warning in all_warnings:
            st.write(f"`{name}` — {warning}")


# ---------------------------------------------------------------------------
# 변동 분석
# ---------------------------------------------------------------------------
try:
    with st.spinner("변동 분석 중..."):
        diff = diff_cached(before_bytes, after_bytes, before_name, after_name)
except DiffError as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:
    st.error("변동 분석 중 예상치 못한 오류가 발생했습니다.")
    st.exception(exc)
    st.stop()

if diff.duplicate_keys:
    st.error(
        f"복합키가 중복된 항목 **{len(diff.duplicate_keys)}건**을 분석에서 제외했습니다 "
        f"(제외 행 {diff.excluded_row_count}건). 합산하지 않았으니 원본을 확인하세요."
    )
    with st.expander("중복 키 목록"):
        st.code("\n".join(k.replace("\x1f", " | ") for k in diff.duplicate_keys[:50]))


# ---------------------------------------------------------------------------
# 사이드바 — 분석 옵션
# ---------------------------------------------------------------------------
with st.sidebar:
    st.divider()
    st.subheader("⏱ 기간 단위")
    period_unit = st.radio("기간 단위", schema.PERIOD_UNITS, horizontal=True,
                           label_visibility="collapsed")

    st.subheader("📊 측정값")
    measure = st.radio(
        "측정값", schema.MEASURES,
        format_func=lambda m: f"{m}  ({schema.UNITS.get(m, '')})",
        label_visibility="collapsed",
    )

    st.subheader("🧭 분석 축")
    axis = st.selectbox("1단계", schema.ANALYSIS_AXES, index=0)
    second_choice = st.selectbox(
        "2단계 (선택)", ["(없음)"] + [a for a in schema.ANALYSIS_AXES if a != axis]
    )
    second_axis = None if second_choice == "(없음)" else second_choice

    st.subheader("🔍 표시 옵션")
    changed_only = st.checkbox("변동 있는 행만", value=False)
    display_mode = st.selectbox("증감 표시", fmt.DISPLAY_MODES, index=0)

labels = period_labels(diff.rows, period_unit)

try:
    matrix = build_matrix(
        diff.rows, axis=axis, period_unit=period_unit, measure=measure,
        second_axis=second_axis, changed_only=changed_only,
    )
except AggregationError as exc:
    st.error(str(exc))
    st.stop()


# ---------------------------------------------------------------------------
# 총계 요약
# ---------------------------------------------------------------------------
grand_before = matrix.before.loc[matrix.total_label, aggregator.TOTAL_LABEL]
grand_after = matrix.after.loc[matrix.total_label, aggregator.TOTAL_LABEL]
grand_delta = matrix.delta.loc[matrix.total_label, aggregator.TOTAL_LABEL]
grand_pct = matrix.pct.loc[matrix.total_label, aggregator.TOTAL_LABEL]
decimals = fmt.decimals_for(measure)
counts = diff.status_counts()

c1, c2, c3, c4 = st.columns(4)
signed_metric(c1, f"변동 전 · {matrix.unit}", grand_before, decimals=decimals)
signed_metric(c2, f"변동 후 · {matrix.unit}", grand_after, grand_delta, decimals, grand_pct)
c3.metric("변동 행", f"{diff.changed_count:,} / {len(diff.rows):,}")
c4.metric("신규 / 삭제",
          f"{counts[schema.STATUS_ADDED]:,} / {counts[schema.STATUS_REMOVED]:,}")

if diff.changed_count == 0:
    st.success("두 파일 사이에 변동이 없습니다.")


# ---------------------------------------------------------------------------
# 메인 표
# ---------------------------------------------------------------------------
st.divider()
axis_label = axis if second_axis is None else f"{axis} / {second_axis}"
st.subheader(f"변동 후 {measure} · {axis_label} × {period_unit}")

if matrix.after.empty:
    st.info("표시할 행이 없습니다. '변동 있는 행만' 필터를 해제해 보세요.")
else:
    st.dataframe(
        fmt.style_matrix(matrix, display_mode),
        width="stretch",
        height=min(80 + 35 * len(matrix.after), 560),
        on_select="rerun",
        selection_mode="single-row",
        key="matrix_table",
    )

    export1, export2, _ = st.columns([1, 1, 4])
    export1.download_button(
        "⬇ Excel", data=fmt.matrix_to_excel(matrix),
        file_name=f"변동분석_{measure}_{period_unit}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    export2.download_button(
        "⬇ CSV", data=fmt.matrix_to_csv(matrix, display_mode),
        file_name=f"변동분석_{measure}_{period_unit}.csv",
        mime="text/csv", width="stretch",
    )

st.caption(
    f"{fmt.UP_MARK} 증가 · {fmt.DOWN_MARK} 감소 · {fmt.FLAT_MARK} 무변동 ｜ "
    f"단위 {matrix.unit} ｜ 단가는 금액÷수량 가중평균"
)


# ---------------------------------------------------------------------------
# 상세 분석
# ---------------------------------------------------------------------------
st.divider()
st.subheader("상세 분석")

detail_label = st.segmented_control(
    "분석 대상 기간", labels, default=labels[-1], key="detail_period",
)
if detail_label is None:
    detail_label = labels[-1]

try:
    detail = period_detail(diff.rows, period_unit, detail_label, axis=axis)
except AggregationError as exc:
    st.error(str(exc))
    st.stop()

d1, d2, d3, d4 = st.columns(4)
signed_metric(d1, "변동 전", detail.amount_before, decimals=0)
signed_metric(d2, "변동 후", detail.amount_after,
              detail.amount_delta, 0, detail.amount_pct)
signed_metric(d3, "수량", detail.qty_after, detail.qty_delta, 0)
signed_metric(d4, "단가", detail.price_after, detail.price_delta, 1)
st.caption("금액 백만원 · 수량 톤 · 단가 천원/톤")

left, right = st.columns([2, 3])

with left:
    st.markdown(
        "**효과 분해**&nbsp; <span style='color:#777;font-size:.8rem'>백만원</span>",
        unsafe_allow_html=True,
    )
    volume, price = detail.effects["물량효과"], detail.effects["단가효과"]
    magnitude = abs(volume) + abs(price)
    e1, e2 = st.columns(2)
    colored_stat(e1, "물량효과", volume,
                 note=f"{abs(volume) / magnitude * 100:.1f}%" if magnitude else None)
    colored_stat(e2, "단가효과", price,
                 note=f"{abs(price) / magnitude * 100:.1f}%" if magnitude else None)

    components = "&nbsp;&nbsp;".join(
        f"<span style='color:#666'>{c}</span> "
        f"<b style='color:{fmt.delta_color(detail.effects[f'{c}기여'])}'>"
        f"{fmt.fmt_delta(detail.effects[f'{c}기여'], 0)}</b>"
        for c in schema.PRICE_COMPONENTS
    )
    st.markdown(
        f"<div style='margin-top:.7rem;font-size:.85rem'>"
        f"<span style='color:#999'>단가효과 내역</span>&nbsp;&nbsp;{components}</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"행 상태 — 값변동 {detail.counts['변동']:,} · "
        f"신규 {detail.counts['신규']:,} · 삭제 {detail.counts['삭제']:,} "
        f"(전체 {detail.counts['전체']:,})"
    )

with right:
    st.plotly_chart(render_waterfall(detail), width="stretch")

if period_unit != "월" and len(detail.monthly_trend) > 1:
    st.markdown("**월별 증감 추이**  (백만원)")
    st.plotly_chart(render_trend(detail.monthly_trend), width="stretch")

tab_top, tab_added, tab_removed = st.tabs([
    f"기여도 상위 · {axis}",
    f"신규 행 ({detail.counts['신규']})",
    f"삭제 행 ({detail.counts['삭제']})",
])

with tab_top:
    if detail.contributors.empty:
        st.info(f"{detail_label} 에는 변동이 없습니다.")
    else:
        st.dataframe(
            fmt.style_contributors(detail.contributors, axis),
            width="stretch", hide_index=True,
        )
        if detail.contributors_truncated:
            st.caption(
                f"변동이 있는 {axis} {detail.contributor_total}개 중 "
                f"증가·감소 상위 {len(detail.contributors)}개만 표시했습니다. "
                f"합계가 전체 증감과 다를 수 있습니다."
            )
        else:
            st.caption(f"변동이 있는 {axis} {detail.contributor_total}개 전부 표시했습니다.")

with tab_added:
    if detail.added_rows.empty:
        st.caption("없음")
    else:
        st.dataframe(
            detail.added_rows[schema.KEY_COLUMNS + ["수량_후", "금액_후"]],
            width="stretch", hide_index=True,
        )

with tab_removed:
    if detail.removed_rows.empty:
        st.caption("없음")
    else:
        st.dataframe(
            detail.removed_rows[schema.KEY_COLUMNS + ["수량_전", "금액_전"]],
            width="stretch", hide_index=True,
        )


# ---------------------------------------------------------------------------
# 원본 행 드릴다운 (FR-507)
# ---------------------------------------------------------------------------
selection = st.session_state.get("matrix_table", {})
selected_rows = (selection or {}).get("selection", {}).get("rows", [])

if selected_rows and not matrix.after.empty:
    index = selected_rows[0]
    if index < len(matrix.after.index):
        axis_value = str(matrix.after.index[index])
        if axis_value != matrix.total_label:
            # 라벨을 구분자로 쪼개지 않는다. 축 값에 "/" 가 들어간다(후판_건설/철구).
            drill = rows_for_axis(diff.rows, period_unit, detail_label,
                                  matrix.axes, axis_value)
            with st.expander(f"원본 행 비교 — {axis_value} · {detail_label}", expanded=True):
                if drill.empty:
                    st.caption("이 기간에 변동된 행이 없습니다.")
                else:
                    measures = ["수량", "금액", "단가", "Base", "Extra", "운임"]
                    columns = ["비고", "고객그룹", "품명"] + [
                        f"{m}{s}" for m in measures
                        for s in (schema.BEFORE_SUFFIX, schema.AFTER_SUFFIX)
                    ]
                    columns = [c for c in columns if c in drill.columns]
                    st.dataframe(
                        fmt.style_row_comparison(drill[columns], measures),
                        width="stretch", hide_index=True,
                    )
                    st.caption("노란 배경 = 값이 바뀐 셀")
else:
    st.caption("표에서 행을 클릭하면 해당 항목의 원본 행 전/후 비교를 볼 수 있습니다.")
