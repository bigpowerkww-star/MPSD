"""변동 전/후 행 매칭과 금액 증감의 원인 분해.

Streamlit 의존 없음.

핵심 공식 (PRD 5.2 / 5.3)
------------------------
    Q0,P0 = 변동 전 수량·단가        Q1,P1 = 변동 후 수량·단가

    물량효과 = (Q1 - Q0) * P0 / 1000
    단가효과 = Q1 * (P1 - P0) / 1000
    ────────────────────────────────
    합계     = (Q1*P1 - Q0*P0) / 1000 = 금액 증감      (잔차 0)

    단가 = Base + Extra + 운임 이므로 단가효과도 그대로 쪼개진다:
    Base기여 = Q1 * (Base1 - Base0) / 1000   (Extra·운임 동일)

신규/삭제 행은 단가를 비교할 상대가 없다. 전액 물량효과로 잡고
단가효과에 배분하지 않는다(PRD 5.4). 섞으면 단가효과가 왜곡된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import schema

_KEY = "_key"
_INDICATOR = "_merge"

#: 상태 판정에 쓰는 컬럼. 표시용 파생값(분기/반기 등)은 제외한다.
_COMPARE_COLUMNS = schema.TRACKED_MEASURES

#: diff 결과에 함께 실어 두는 시점 컬럼
_PERIOD_COLUMNS = [schema.QUARTER_COLUMN, schema.HALF_COLUMN, "연도", "월"]


class DiffError(Exception):
    """변동 분석을 수행할 수 없을 때."""


@dataclass
class DiffResult:
    """행 단위 변동 분석 결과."""

    rows: pd.DataFrame
    duplicate_keys: list[str] = field(default_factory=list)
    excluded_row_count: int = 0
    before_label: str = "변동 전"
    after_label: str = "변동 후"

    @property
    def changed_rows(self) -> pd.DataFrame:
        return self.rows[self.rows["상태"] != schema.STATUS_KEPT]

    @property
    def changed_count(self) -> int:
        return int((self.rows["상태"] != schema.STATUS_KEPT).sum())

    def status_counts(self) -> dict[str, int]:
        counts = self.rows["상태"].value_counts().to_dict()
        return {status: int(counts.get(status, 0)) for status in schema.STATUS_ORDER}


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def build_diff(
    before: pd.DataFrame,
    after: pd.DataFrame,
    before_label: str = "변동 전",
    after_label: str = "변동 후",
) -> DiffResult:
    """두 계획 DataFrame 을 복합키로 매칭해 행 단위 변동을 계산한다."""
    _require_columns(before, "변동 전")
    _require_columns(after, "변동 후")

    before = _with_key(before)
    after = _with_key(after)

    dup_keys, excluded = _collect_duplicates(before, after)
    if dup_keys:
        # 중복 키는 임의로 합산하지 않는다(FR-207). 분석에서 제외하고 호출자에게 알린다.
        before = before[~before[_KEY].isin(dup_keys)]
        after = after[~after[_KEY].isin(dup_keys)]

    merged = _merge(before, after)
    rows = _assemble(merged)

    return DiffResult(
        rows=rows,
        duplicate_keys=dup_keys,
        excluded_row_count=excluded,
        before_label=before_label,
        after_label=after_label,
    )


# ---------------------------------------------------------------------------
# 내부 단계
# ---------------------------------------------------------------------------

def _require_columns(df: pd.DataFrame, side: str) -> None:
    needed = set(schema.KEY_COLUMNS) | set(_COMPARE_COLUMNS)
    missing = needed - set(df.columns)
    if missing:
        raise DiffError(f"{side} 데이터에 컬럼이 없습니다: {sorted(missing)}")


def _with_key(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # pandas 3.x 에서 문자열 컬럼의 .values 는 ArrowStringArray 라 index/merge 에서 문제를
    # 일으킨다. Series 를 그대로 컬럼에 붙이고 object dtype 으로 고정한다.
    out[_KEY] = schema.build_key(df).astype("object")
    return out


def _collect_duplicates(before: pd.DataFrame, after: pd.DataFrame) -> tuple[list[str], int]:
    dup = set()
    excluded = 0
    for df in (before, after):
        counts = df[_KEY].value_counts()
        offenders = counts[counts > 1]
        dup.update(offenders.index.tolist())
    if dup:
        for df in (before, after):
            excluded += int(df[_KEY].isin(dup).sum())
    return sorted(dup), excluded


def _merge(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    keep = [_KEY] + schema.KEY_COLUMNS + _PERIOD_COLUMNS + _COMPARE_COLUMNS
    return before[keep].merge(
        after[keep],
        on=_KEY,
        how="outer",
        suffixes=(schema.BEFORE_SUFFIX, schema.AFTER_SUFFIX),
        indicator=_INDICATOR,
    )


def _assemble(merged: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=merged.index)

    # --- 키·시점: 변동 후 값을 우선하고, 삭제 행은 변동 전 값으로 채운다 ---
    for col in schema.KEY_COLUMNS + _PERIOD_COLUMNS:
        after_col = f"{col}{schema.AFTER_SUFFIX}"
        before_col = f"{col}{schema.BEFORE_SUFFIX}"
        out[col] = merged[after_col].where(merged[after_col].notna(), merged[before_col])

    for col in _PERIOD_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("int64")

    # --- 측정값: 한쪽에만 있는 행은 반대편을 0 으로 채운다(FR-203) ---
    only_after = merged[_INDICATOR].to_numpy() == "right_only"
    only_before = merged[_INDICATOR].to_numpy() == "left_only"
    both = ~(only_after | only_before)

    for measure in _COMPARE_COLUMNS:
        before_vals = _numeric(merged[f"{measure}{schema.BEFORE_SUFFIX}"])
        after_vals = _numeric(merged[f"{measure}{schema.AFTER_SUFFIX}"])
        out[f"{measure}{schema.BEFORE_SUFFIX}"] = before_vals
        out[f"{measure}{schema.AFTER_SUFFIX}"] = after_vals
        out[f"{measure}{schema.DELTA_SUFFIX}"] = after_vals - before_vals

    # --- 상태 분류 (FR-202, FR-205) ---
    out["상태"] = np.where(
        only_after, schema.STATUS_ADDED,
        np.where(only_before, schema.STATUS_REMOVED, schema.STATUS_KEPT),
    )
    changed = both & _has_material_change(out)
    out.loc[changed, "상태"] = schema.STATUS_CHANGED

    # --- 효과 분해 (PRD 5.2 / 5.3) ---
    _apply_effects(out, matched=both)

    out["증감있음"] = out["상태"] != schema.STATUS_KEPT
    return out.reset_index(drop=True)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0).astype("float64")


def _has_material_change(out: pd.DataFrame) -> np.ndarray:
    """상대오차 FLOAT_RTOL 이하는 변동 없음으로 본다(FR-205)."""
    changed = np.zeros(len(out), dtype=bool)
    for measure in _COMPARE_COLUMNS:
        b = out[f"{measure}{schema.BEFORE_SUFFIX}"].to_numpy()
        a = out[f"{measure}{schema.AFTER_SUFFIX}"].to_numpy()
        changed |= ~np.isclose(a, b, rtol=schema.FLOAT_RTOL, atol=0.0)
    return changed


def _apply_effects(out: pd.DataFrame, matched: np.ndarray) -> None:
    scale = schema.AMOUNT_SCALE
    q0 = out[f"수량{schema.BEFORE_SUFFIX}"].to_numpy()
    q1 = out[f"수량{schema.AFTER_SUFFIX}"].to_numpy()
    p0 = out[f"단가{schema.BEFORE_SUFFIX}"].to_numpy()
    p1 = out[f"단가{schema.AFTER_SUFFIX}"].to_numpy()
    amount_delta = out[f"금액{schema.DELTA_SUFFIX}"].to_numpy()

    volume = (q1 - q0) * p0 / scale
    price = q1 * (p1 - p0) / scale

    # 신규/삭제는 비교할 단가가 없다. 전액 물량효과로 잡는다(PRD 5.4).
    out["물량효과"] = np.where(matched, volume, amount_delta)
    out["단가효과"] = np.where(matched, price, 0.0)

    for component in schema.PRICE_COMPONENTS:
        c0 = out[f"{component}{schema.BEFORE_SUFFIX}"].to_numpy()
        c1 = out[f"{component}{schema.AFTER_SUFFIX}"].to_numpy()
        out[f"{component}기여"] = np.where(matched, q1 * (c1 - c0) / scale, 0.0)
