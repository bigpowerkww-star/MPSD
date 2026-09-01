"""변동 분석 엔진 검증 (PRD 5장, 9.2)."""

import numpy as np
import pandas as pd
import pytest

from core import schema
from core.aggregator import aggregate, build_matrix, pct_change, period_labels
from core.differ import DiffError, build_diff

ATOL = 1e-6


@pytest.fixture(scope="session")
def diff(before, after):
    return build_diff(before.df, after.df, before.source_name, after.source_name)


# ---------------------------------------------------------------------------
# 항등식 — 이게 깨지면 나머지 숫자를 믿을 수 없다
# ---------------------------------------------------------------------------
class TestIdentities:
    def test_effects_sum_to_amount_delta(self, diff):
        """물량효과 + 단가효과 == 금액 증감 (잔차 0). PRD 5.2"""
        rows = diff.rows
        total = rows["물량효과"] + rows["단가효과"]
        assert np.allclose(total, rows[f"금액{schema.DELTA_SUFFIX}"], rtol=0, atol=ATOL)

    def test_components_sum_to_price_effect(self, diff):
        """Base + Extra + 운임 기여 == 단가효과. PRD 5.3"""
        rows = diff.rows
        total = rows["Base기여"] + rows["Extra기여"] + rows["운임기여"]
        assert np.allclose(total, rows["단가효과"], rtol=0, atol=ATOL)

    def test_identity_holds_after_aggregation(self, diff):
        agg = aggregate(diff.rows, axes=["팀파트"], period_unit="월")
        total = agg["물량효과"] + agg["단가효과"]
        assert np.allclose(total, agg[f"금액{schema.DELTA_SUFFIX}"], rtol=0, atol=ATOL)

    @pytest.mark.parametrize("unit", schema.PERIOD_UNITS)
    def test_period_units_agree(self, diff, unit):
        """월 합계 == 분기 합계 == 반기 합계 == 연간 합계."""
        agg = aggregate(diff.rows, axes=(), period_unit=unit)
        monthly = aggregate(diff.rows, axes=(), period_unit="월")
        for col in ("금액_전", "금액_후", "수량_전", "수량_후", "물량효과", "단가효과"):
            assert np.isclose(agg[col].sum(), monthly[col].sum(), rtol=0, atol=1e-4), col

    def test_axis_totals_match_grand_total(self, diff):
        grand = aggregate(diff.rows, axes=(), period_unit=None).iloc[0]
        for axis in ("팀파트", "내수수출", "회의자료_대분류"):
            by_axis = aggregate(diff.rows, axes=[axis], period_unit=None)
            assert np.isclose(by_axis["금액_후"].sum(), grand["금액_후"], atol=1e-4), axis
            assert np.isclose(by_axis["물량효과"].sum(), grand["물량효과"], atol=1e-4), axis


# ---------------------------------------------------------------------------
# 가중평균
# ---------------------------------------------------------------------------
class TestWeightedPrice:
    def test_aggregated_price_is_weighted_not_arithmetic(self, diff):
        agg = aggregate(diff.rows, axes=["팀파트"], period_unit=None)
        expected = agg["금액_후"] / agg["수량_후"] * schema.AMOUNT_SCALE
        assert np.allclose(agg["단가_후"], expected, rtol=1e-9, atol=ATOL)

    def test_weighted_differs_from_arithmetic_mean(self, diff):
        """구성비가 다르면 두 값은 실제로 갈린다 — 산술평균을 쓰면 안 되는 근거."""
        agg = aggregate(diff.rows, axes=["팀파트"], period_unit=None)
        arithmetic = diff.rows.groupby("팀파트", observed=True)["단가_후"].mean()
        weighted = agg.set_index("팀파트")["단가_후"]
        joined = pd.concat([arithmetic, weighted], axis=1, keys=["arith", "weighted"])
        assert not np.allclose(joined["arith"], joined["weighted"], rtol=1e-3)

    def test_zero_quantity_gives_nan_not_inf(self):
        rows = _synthetic_rows()
        rows.loc[:, "수량_후"] = 0.0
        rows.loc[:, "금액_후"] = 0.0
        agg = aggregate(rows, axes=(), period_unit=None)
        assert np.isnan(agg["단가_후"].iloc[0])
        assert not np.isinf(agg["단가_후"].iloc[0])


# ---------------------------------------------------------------------------
# 상태 분류
# ---------------------------------------------------------------------------
class TestStatus:
    def test_sample_has_no_added_or_removed(self, diff):
        counts = diff.status_counts()
        assert counts[schema.STATUS_ADDED] == 0
        assert counts[schema.STATUS_REMOVED] == 0

    def test_removed_row_is_detected(self, before, after):
        trimmed = after.df.iloc[1:].copy()
        diff = build_diff(before.df, trimmed)
        counts = diff.status_counts()
        assert counts[schema.STATUS_REMOVED] == 1
        assert counts[schema.STATUS_ADDED] == 0

    def test_added_row_is_detected(self, before, after):
        trimmed = before.df.iloc[1:].copy()
        diff = build_diff(trimmed, after.df)
        counts = diff.status_counts()
        assert counts[schema.STATUS_ADDED] == 1

    def test_added_row_goes_entirely_to_volume_effect(self, before, after):
        """신규 행은 단가효과에 배분하지 않는다. PRD 5.4"""
        diff = build_diff(before.df.iloc[1:].copy(), after.df)
        added = diff.rows[diff.rows["상태"] == schema.STATUS_ADDED]
        assert len(added) == 1
        assert np.isclose(added["단가효과"].iloc[0], 0.0, atol=ATOL)
        assert np.isclose(added["물량효과"].iloc[0], added["금액_증감"].iloc[0], atol=ATOL)
        assert np.isclose(added["Base기여"].iloc[0], 0.0, atol=ATOL)

    def test_removed_row_goes_entirely_to_volume_effect(self, before, after):
        diff = build_diff(before.df, after.df.iloc[1:].copy())
        removed = diff.rows[diff.rows["상태"] == schema.STATUS_REMOVED]
        assert len(removed) == 1
        assert np.isclose(removed["단가효과"].iloc[0], 0.0, atol=ATOL)
        assert np.isclose(removed["물량효과"].iloc[0], removed["금액_증감"].iloc[0], atol=ATOL)

    def test_identity_survives_added_and_removed(self, before, after):
        diff = build_diff(before.df.iloc[2:].copy(), after.df.iloc[:-3].copy())
        rows = diff.rows
        assert np.allclose(rows["물량효과"] + rows["단가효과"],
                           rows["금액_증감"], rtol=0, atol=ATOL)

    def test_identical_files_report_no_change(self, before):
        diff = build_diff(before.df, before.df.copy())
        assert diff.changed_count == 0
        assert np.isclose(diff.rows["금액_증감"].abs().sum(), 0.0, atol=ATOL)

    def test_tiny_float_noise_is_not_a_change(self, before):
        """상대오차 1e-9 이하는 변동 없음(FR-205)."""
        nudged = before.df.copy()
        nudged["금액"] = nudged["금액"] * (1 + 1e-12)
        diff = build_diff(before.df, nudged)
        assert diff.changed_count == 0


# ---------------------------------------------------------------------------
# 증감률 / 엣지
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_pct_change_zero_denominator_is_nan(self):
        result = pct_change(pd.Series([5.0, -3.0]), pd.Series([0.0, 0.0]))
        assert result.isna().all()
        assert not np.isinf(result).any()

    def test_no_inf_anywhere_in_aggregate(self, diff):
        agg = aggregate(diff.rows, axes=["팀파트"], period_unit="월")
        numeric = agg.select_dtypes("number")
        assert not np.isinf(numeric.to_numpy()).any()

    def test_duplicate_keys_are_reported_not_summed(self, before, after):
        doubled = pd.concat([before.df, before.df.iloc[[0]]], ignore_index=True)
        diff = build_diff(doubled, after.df)
        assert len(diff.duplicate_keys) == 1
        assert diff.excluded_row_count >= 2  # 변동 전 2행 + 변동 후 1행

    def test_missing_columns_raise(self, before, after):
        broken = before.df.drop(columns=["수량"])
        with pytest.raises(DiffError):
            build_diff(broken, after.df)


# ---------------------------------------------------------------------------
# 메인 표
# ---------------------------------------------------------------------------
class TestMatrix:
    def test_matrix_shape_and_total_row(self, diff):
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="금액")
        periods = period_labels(diff.rows, "월")
        assert list(matrix.after.columns) == periods + ["합계"]
        assert matrix.total_label in matrix.after.index

    def test_total_row_is_first(self, diff):
        """FR-408 — st.dataframe 은 임의 행을 고정할 수 없다. 첫 행이어야 바로 보인다."""
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="금액")
        assert matrix.after.index[0] == matrix.total_label
        for frame in (matrix.before, matrix.delta, matrix.pct, matrix.changed):
            assert frame.index[0] == matrix.total_label

    def test_total_row_equals_column_sum(self, diff):
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="분기", measure="금액")
        assert np.allclose(matrix.body.sum(axis=0), matrix.after.loc[matrix.total_label],
                           atol=1e-4)

    def test_axis_value_named_총계_does_not_break_total(self, diff):
        """축 값이 문자열 '총계' 여도 합계 행이 덮어써지면 안 된다."""
        poisoned = diff.rows.copy()
        poisoned.loc[poisoned.index[:5], "팀파트"] = "총계"
        matrix = build_matrix(poisoned, axis="팀파트", period_unit="월", measure="금액")
        assert matrix.total_label != "총계"
        assert "총계" in matrix.after.index          # 원래 축 값이 살아 있어야 한다
        assert not matrix.after.index.duplicated().any()
        assert np.allclose(matrix.body.sum(axis=0),
                           matrix.after.loc[matrix.total_label], atol=1e-4)

    def test_total_column_equals_row_sum(self, diff):
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="금액")
        periods = period_labels(diff.rows, "월")
        assert np.allclose(matrix.after[periods].sum(axis=1), matrix.after["합계"], atol=1e-4)

    def test_price_matrix_total_is_not_row_sum(self, diff):
        """단가 매트릭스의 합계는 행 합이 아니라 가중평균이어야 한다."""
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="단가")
        periods = period_labels(diff.rows, "월")
        naive = matrix.after[periods].sum(axis=1)
        assert not np.allclose(naive.dropna(), matrix.after["합계"].dropna(), rtol=1e-3)

    def test_changed_only_filter(self, diff):
        full = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="금액")
        filtered = build_matrix(diff.rows, axis="팀파트", period_unit="월",
                                measure="금액", changed_only=True)
        assert len(filtered.after) <= len(full.after)
        assert filtered.after.index[0] == filtered.total_label

    @pytest.mark.parametrize("unit", schema.PERIOD_UNITS)
    @pytest.mark.parametrize("axis", schema.ANALYSIS_AXES)
    @pytest.mark.parametrize("measure", schema.MEASURES)
    def test_every_combination_renders(self, diff, unit, axis, measure):
        """모든 기간 x 축 x 측정값 조합에서 예외가 없어야 한다."""
        matrix = build_matrix(diff.rows, axis=axis, period_unit=unit, measure=measure)
        assert not matrix.after.empty


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _synthetic_rows() -> pd.DataFrame:
    """최소 컬럼만 갖춘 집계용 더미 행."""
    data = {
        "연도": [2026], "월": [1],
        schema.PERIOD_COLUMN: ["2026-01"],
        schema.QUARTER_COLUMN: [1], schema.HALF_COLUMN: [1],
        "상태": [schema.STATUS_CHANGED], "증감있음": [True],
        "팀파트": ["A"],
    }
    for measure in schema.TRACKED_MEASURES:
        data[f"{measure}{schema.BEFORE_SUFFIX}"] = [100.0]
        data[f"{measure}{schema.AFTER_SUFFIX}"] = [110.0]
        data[f"{measure}{schema.DELTA_SUFFIX}"] = [10.0]
    for effect in schema.EFFECT_COLUMNS:
        data[effect] = [0.0]
    return pd.DataFrame(data)
