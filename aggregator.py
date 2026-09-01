"""기간·축 집계.

Streamlit 의존 없음.

집계 순서 규칙 (PRD 5.5) — 이 순서를 어기면 숫자가 틀린다
----------------------------------------------------------
1) 행 단위로 효과를 계산한다 (differ 가 이미 했다)
2) 선택한 기간·축으로 **효과를 SUM** 한다
3) 표시용 단가는 **마지막에** 가중평균으로 재계산한다

집계된 평균단가로 효과를 계산하면 구성비 변화(mix)가 단가효과로 잘못 섞인다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import schema

PERIOD_COLUMN = "기간"
TOTAL_LABEL = "합계"
GRAND_TOTAL_LABEL = "총계"

_COUNT_COLUMNS = ["변동행수", "신규행수", "삭제행수", "행수"]


class AggregationError(Exception):
    pass


@dataclass
class Matrix:
    """메인 표에 필요한 값들. 전부 같은 index/columns 를 가진다."""

    after: pd.DataFrame
    before: pd.DataFrame
    delta: pd.DataFrame
    pct: pd.DataFrame
    changed: pd.DataFrame  # 셀별 변동 행 수
    measure: str
    unit: str
    axes: list[str]
    #: 총계 행의 실제 라벨. 축 값에 "총계" 가 있으면 다른 이름이 된다.
    total_label: str = GRAND_TOTAL_LABEL

    @property
    def body(self) -> pd.DataFrame:
        """총계를 뺀 본문."""
        return self.after.drop(index=self.total_label, errors="ignore")


@dataclass
class PeriodDetail:
    """상세 분석 패널에 필요한 값들."""

    label: str
    amount_before: float
    amount_after: float
    amount_delta: float
    amount_pct: float
    qty_before: float
    qty_after: float
    qty_delta: float
    price_before: float
    price_after: float
    price_delta: float
    effects: dict[str, float]
    counts: dict[str, int]
    contributors: pd.DataFrame
    #: 변동이 있는 축 항목의 전체 개수. contributors 는 그중 상위 N개만 담는다.
    contributor_total: int
    added_rows: pd.DataFrame
    removed_rows: pd.DataFrame
    monthly_trend: pd.DataFrame

    @property
    def contributors_truncated(self) -> bool:
        return self.contributor_total > len(self.contributors)


# ---------------------------------------------------------------------------
# 기간 라벨
# ---------------------------------------------------------------------------

def period_label_series(rows: pd.DataFrame, unit: str) -> pd.Series:
    """행별 기간 라벨. 문자열 정렬이 곧 시간순이 되도록 만든다."""
    year = rows["연도"].astype(int)
    if unit == "월":
        return rows[schema.PERIOD_COLUMN].astype(str)
    if unit == "분기":
        return year.astype(str) + "-Q" + rows[schema.QUARTER_COLUMN].astype(int).astype(str)
    if unit == "반기":
        return year.astype(str) + "-H" + rows[schema.HALF_COLUMN].astype(int).astype(str)
    if unit == "연간":
        return year.astype(str)
    raise AggregationError(f"알 수 없는 기간 단위: {unit}")


def period_labels(rows: pd.DataFrame, unit: str) -> list[str]:
    """데이터에 실제로 존재하는 기간만 시간순으로. 빈 기간은 만들지 않는다(FR-304)."""
    return sorted(period_label_series(rows, unit).unique().tolist())


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------

def aggregate(
    rows: pd.DataFrame,
    axes: list[str] | tuple[str, ...] = (),
    period_unit: str | None = None,
) -> pd.DataFrame:
    """축·기간으로 묶어 합계를 낸다.

    axes 가 비면 전체 합계, period_unit 이 None 이면 기간을 나누지 않는다.
    두 경우 모두 같은 코드 경로를 타므로 합계와 세부가 항상 정합한다.
    """
    df = rows.copy()

    group_cols: list[str] = list(axes)
    if period_unit is not None:
        df[PERIOD_COLUMN] = period_label_series(df, period_unit)
        group_cols.append(PERIOD_COLUMN)

    df["행수"] = 1
    df["변동행수"] = df["증감있음"].astype(int)
    df["신규행수"] = (df["상태"] == schema.STATUS_ADDED).astype(int)
    df["삭제행수"] = (df["상태"] == schema.STATUS_REMOVED).astype(int)

    # 단가성 값은 SUM 이 무의미하다. 가중평균을 내기 위한 분자를 미리 만든다.
    weight_cols: dict[str, str] = {}
    for rate in schema.RATE_MEASURES:
        for side in (schema.BEFORE_SUFFIX, schema.AFTER_SUFFIX):
            src, qty = f"{rate}{side}", f"수량{side}"
            if src not in df.columns:
                continue
            num = f"__num_{rate}{side}"
            df[num] = df[src] * df[qty]
            weight_cols[num] = qty

    sum_cols = (
        [f"{m}{s}" for m in schema.ADDITIVE_MEASURES
         for s in (schema.BEFORE_SUFFIX, schema.AFTER_SUFFIX)]
        + list(schema.EFFECT_COLUMNS)
        + _COUNT_COLUMNS
        + list(weight_cols)
    )
    sum_cols = [c for c in dict.fromkeys(sum_cols) if c in df.columns]

    if group_cols:
        grouped = df.groupby(group_cols, dropna=False, observed=True)[sum_cols].sum()
        out = grouped.reset_index()
    else:
        out = df[sum_cols].sum().to_frame().T

    _finalize(out, weight_cols)
    return out


def _finalize(out: pd.DataFrame, weight_cols: dict[str, str]) -> None:
    """합산이 끝난 뒤 파생값을 만든다 — 순서가 중요하다."""
    for measure in schema.ADDITIVE_MEASURES:
        before, after = f"{measure}{schema.BEFORE_SUFFIX}", f"{measure}{schema.AFTER_SUFFIX}"
        if before in out.columns:
            out[f"{measure}{schema.DELTA_SUFFIX}"] = out[after] - out[before]

    # 3) 단가는 마지막에 가중평균으로 재계산 (FR-306)
    for num, qty in weight_cols.items():
        rate_side = num.replace("__num_", "")
        out[rate_side] = _safe_div(out[num], out[qty])
        out.drop(columns=[num], inplace=True)

    for rate in schema.RATE_MEASURES:
        before, after = f"{rate}{schema.BEFORE_SUFFIX}", f"{rate}{schema.AFTER_SUFFIX}"
        if before in out.columns and after in out.columns:
            out[f"{rate}{schema.DELTA_SUFFIX}"] = out[after] - out[before]

    for measure in schema.TRACKED_MEASURES:
        before = f"{measure}{schema.BEFORE_SUFFIX}"
        delta = f"{measure}{schema.DELTA_SUFFIX}"
        if before in out.columns and delta in out.columns:
            out[f"{measure}_증감률"] = pct_change(out[delta], out[before])

    for col in _COUNT_COLUMNS:
        if col in out.columns:
            out[col] = out[col].fillna(0).astype("int64")


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """0 으로 나누면 inf/NaN 이 아니라 NaN 을 돌려준다."""
    denom = denominator.replace(0, np.nan)
    return numerator / denom


def pct_change(delta, before) -> pd.Series:
    """증감률(%). 분모 0 이면 NaN — inf 를 만들지 않는다(FR-206)."""
    delta = pd.Series(delta) if not isinstance(delta, pd.Series) else delta
    before = pd.Series(before) if not isinstance(before, pd.Series) else before
    base = before.abs().replace(0, np.nan)
    return delta / base * 100.0


# ---------------------------------------------------------------------------
# 메인 표
# ---------------------------------------------------------------------------

def build_matrix(
    rows: pd.DataFrame,
    axis: str,
    period_unit: str,
    measure: str,
    second_axis: str | None = None,
    changed_only: bool = False,
) -> Matrix:
    """행=분석 축, 열=기간(+합계) 매트릭스. 총계 행을 마지막에 붙인다."""
    if measure not in schema.TRACKED_MEASURES:
        raise AggregationError(f"알 수 없는 측정값: {measure}")

    axes = [axis] + ([second_axis] if second_axis else [])
    by_period = aggregate(rows, axes=axes, period_unit=period_unit)
    by_total = aggregate(rows, axes=axes, period_unit=None)
    grand_period = aggregate(rows, axes=(), period_unit=period_unit)
    grand_total = aggregate(rows, axes=(), period_unit=None)

    periods = period_labels(rows, period_unit)
    columns = periods + [TOTAL_LABEL]

    # 축 값 중에 "총계" 가 실제로 있으면 총계 행이 그 행을 덮어써 합계가 틀어진다.
    axis_labels = _axis_index(by_total, axes).tolist()
    total_label = _unique_total_label(axis_labels)

    frames: dict[str, pd.DataFrame] = {}
    for name, suffix in (
        ("after", schema.AFTER_SUFFIX),
        ("before", schema.BEFORE_SUFFIX),
        ("delta", schema.DELTA_SUFFIX),
        ("pct", "_증감률"),
        ("changed", None),
    ):
        column = "변동행수" if suffix is None else f"{measure}{suffix}"
        wide = _pivot(by_period, axes, column, periods)
        wide[TOTAL_LABEL] = _series_by_axis(by_total, axes, column)
        grand = _grand_row(grand_period, grand_total, column, periods)
        wide.loc[total_label] = grand
        # 총계를 맨 위로 — st.dataframe 은 임의 행을 고정할 수 없어, 첫 화면에서
        # 총계가 바로 보이게 하는 것이 최선이다 (FR-408).
        wide = wide.reindex([total_label] + [i for i in wide.index if i != total_label])
        frames[name] = wide.reindex(columns=columns)

    if changed_only:
        keep = frames["changed"].drop(index=total_label).sum(axis=1) > 0
        keep_index = [total_label] + keep[keep].index.tolist()
        frames = {k: v.loc[[i for i in keep_index if i in v.index]]
                  for k, v in frames.items()}

    return Matrix(
        after=frames["after"],
        before=frames["before"],
        delta=frames["delta"],
        pct=frames["pct"],
        changed=frames["changed"],
        measure=measure,
        unit=schema.UNITS.get(measure, ""),
        axes=axes,
        total_label=total_label,
    )


def _unique_total_label(existing: list[str]) -> str:
    """축 값과 겹치지 않는 총계 행 라벨을 고른다."""
    if GRAND_TOTAL_LABEL not in existing:
        return GRAND_TOTAL_LABEL
    for suffix in ("(전체)", "(전체합계)", "(합)"):
        candidate = f"{GRAND_TOTAL_LABEL} {suffix}"
        if candidate not in existing:
            return candidate
    index = 2
    while f"{GRAND_TOTAL_LABEL} ({index})" in existing:
        index += 1
    return f"{GRAND_TOTAL_LABEL} ({index})"


def _axis_index(df: pd.DataFrame, axes: list[str]) -> pd.Series:
    if len(axes) == 1:
        return df[axes[0]].astype(str)
    return df[axes].astype(str).agg(" / ".join, axis=1)


def _pivot(long: pd.DataFrame, axes: list[str], column: str, periods: list[str]) -> pd.DataFrame:
    tmp = long.copy()
    tmp["__axis"] = _axis_index(tmp, axes)
    wide = tmp.pivot_table(
        index="__axis", columns=PERIOD_COLUMN, values=column,
        aggfunc="sum", observed=True,
    )
    wide = wide.reindex(columns=periods)
    wide.index.name = " / ".join(axes)
    return wide


def _series_by_axis(long: pd.DataFrame, axes: list[str], column: str) -> pd.Series:
    tmp = long.copy()
    tmp["__axis"] = _axis_index(tmp, axes)
    return tmp.set_index("__axis")[column]


def _grand_row(grand_period: pd.DataFrame, grand_total: pd.DataFrame,
               column: str, periods: list[str]) -> pd.Series:
    row = grand_period.set_index(PERIOD_COLUMN)[column].reindex(periods)
    row[TOTAL_LABEL] = grand_total[column].iloc[0]
    return row


# ---------------------------------------------------------------------------
# 상세 분석
# ---------------------------------------------------------------------------

def period_detail(
    rows: pd.DataFrame,
    period_unit: str,
    period_label: str,
    axis: str,
    top_n: int = 10,
) -> PeriodDetail:
    """특정 기간 하나에 대한 상세 분석 (FR-501~508)."""
    labels = period_label_series(rows, period_unit)
    subset = rows[labels == period_label]
    if subset.empty:
        raise AggregationError(f"해당 기간에 데이터가 없습니다: {period_label}")

    total = aggregate(subset, axes=(), period_unit=None).iloc[0]

    effects = {name: float(total[name]) for name in schema.EFFECT_COLUMNS}
    counts = {
        "변동": int(total["변동행수"]),
        "신규": int(total["신규행수"]),
        "삭제": int(total["삭제행수"]),
        "전체": int(total["행수"]),
    }

    by_axis = aggregate(subset, axes=[axis], period_unit=None)
    ranked = by_axis.sort_values(f"금액{schema.DELTA_SUFFIX}", ascending=False)
    contributor_total = int(
        (~np.isclose(ranked[f"금액{schema.DELTA_SUFFIX}"], 0.0, atol=1e-9)).sum()
    )
    contributors = _trim_contributors(ranked, axis, top_n)

    added = subset[subset["상태"] == schema.STATUS_ADDED]
    removed = subset[subset["상태"] == schema.STATUS_REMOVED]

    trend = (
        aggregate(subset, axes=(), period_unit="월")
        .sort_values(PERIOD_COLUMN)
        [[PERIOD_COLUMN, f"금액{schema.BEFORE_SUFFIX}", f"금액{schema.AFTER_SUFFIX}",
          f"금액{schema.DELTA_SUFFIX}"] + schema.EFFECT_COLUMNS]
        .reset_index(drop=True)
    )

    return PeriodDetail(
        label=period_label,
        amount_before=float(total[f"금액{schema.BEFORE_SUFFIX}"]),
        amount_after=float(total[f"금액{schema.AFTER_SUFFIX}"]),
        amount_delta=float(total[f"금액{schema.DELTA_SUFFIX}"]),
        amount_pct=float(total["금액_증감률"]),
        qty_before=float(total[f"수량{schema.BEFORE_SUFFIX}"]),
        qty_after=float(total[f"수량{schema.AFTER_SUFFIX}"]),
        qty_delta=float(total[f"수량{schema.DELTA_SUFFIX}"]),
        price_before=float(total[f"단가{schema.BEFORE_SUFFIX}"]),
        price_after=float(total[f"단가{schema.AFTER_SUFFIX}"]),
        price_delta=float(total[f"단가{schema.DELTA_SUFFIX}"]),
        effects=effects,
        counts=counts,
        contributors=contributors,
        contributor_total=contributor_total,
        added_rows=added,
        removed_rows=removed,
        monthly_trend=trend,
    )


def rows_for_axis(
    rows: pd.DataFrame,
    period_unit: str,
    period_label: str,
    axes: list[str] | tuple[str, ...],
    axis_label: str,
    changed_only: bool = True,
) -> pd.DataFrame:
    """특정 기간·축 조합에 속한 원본 행들 (FR-507 드릴다운용).

    `axis_label` 은 메인 표의 행 인덱스 그대로다. 이 함수는 그 라벨을
    **다시 계산해서 비교**한다. 라벨을 구분자로 쪼개면 안 된다 —
    축 값 자체에 "/" 가 들어간다(예: 후판_건설/철구).
    """
    periods = period_label_series(rows, period_unit)
    labels = _axis_index(rows, list(axes))
    subset = rows[(periods == period_label) & (labels == str(axis_label))]
    if changed_only:
        subset = subset[subset["증감있음"]]
    return subset


def _trim_contributors(contributors: pd.DataFrame, axis: str, top_n: int) -> pd.DataFrame:
    """증가 상위 N + 감소 상위 N 만 남긴다. 변동 없는 축은 뺀다."""
    delta_col = f"금액{schema.DELTA_SUFFIX}"
    moved = contributors[~np.isclose(contributors[delta_col], 0.0, atol=1e-9)]
    if moved.empty:
        return moved
    increases = moved[moved[delta_col] > 0].head(top_n)
    decreases = moved[moved[delta_col] < 0].tail(top_n)
    keep = pd.concat([increases, decreases])
    columns = [axis, f"수량{schema.DELTA_SUFFIX}", f"금액{schema.BEFORE_SUFFIX}",
               f"금액{schema.AFTER_SUFFIX}", delta_col, "금액_증감률"] + schema.EFFECT_COLUMNS
    columns = [c for c in columns if c in keep.columns]
    return keep[columns].reset_index(drop=True)
