"""1단계 파서 검증 (PRD 3.1~3.4)."""

import numpy as np
import pytest

from core import schema
from core.loader import PlanLoadError, load_plan

EXPECTED_ROWS = 277
EXPECTED_PERIODS = [f"2026-{m:02d}" for m in range(1, 8)]

# 부동소수점 비교 허용 오차 (상대)
RTOL = 1e-9


@pytest.fixture(params=["before", "after"])
def loaded(request):
    return request.getfixturevalue(request.param)


class TestStructure:
    def test_sheet_name(self, loaded):
        assert loaded.sheet_name == "후판"

    def test_header_row_is_detected_not_hardcoded(self, loaded):
        # 이 양식에서는 5행이지만, 탐지 결과여야 한다
        assert loaded.header_row == 5

    def test_row_count(self, loaded):
        assert loaded.row_count == EXPECTED_ROWS
        assert len(loaded.df) == EXPECTED_ROWS

    def test_no_header_warnings(self, loaded):
        header_warnings = [w for w in loaded.warnings if "헤더가 다릅니다" in w]
        assert header_warnings == [], header_warnings

    def test_all_schema_columns_present(self, loaded):
        missing = set(schema.COLUMN_MAP.values()) - set(loaded.df.columns)
        assert not missing


class TestPeriods:
    def test_period_range(self, loaded):
        assert loaded.periods == EXPECTED_PERIODS
        assert loaded.period_min == "2026-01"
        assert loaded.period_max == "2026-07"

    def test_quarter_and_half_consistent_with_month(self, loaded):
        """보정 후에는 예외 없이 기준년월과 일치해야 한다.

        이게 깨지면 2단계에서 '월 합계 == 분기 합계' 가 성립하지 않는다.
        """
        df = loaded.df
        assert (df["분기"] == (df["월"] - 1) // 3 + 1).all()
        assert (df["반기"] == (df["월"] - 1) // 6 + 1).all()

    def test_source_quarter_conflicts_are_reported(self, loaded):
        """원본 샘플에는 분기/반기가 기준년월과 어긋난 행이 실제로 있다.

        조용히 고치지 말고 경고로 드러내야 한다.
        """
        messages = [w for w in loaded.warnings if "불일치" in w]
        assert any("분기" in w for w in messages), loaded.warnings
        assert any("반기" in w for w in messages), loaded.warnings


class TestKey:
    def test_composite_key_is_unique(self, loaded):
        """PRD 3.4 — 차원 18 + 기준년월 조합이 277개 전부 유니크."""
        keys = schema.build_key(loaded.df)
        assert len(keys) == EXPECTED_ROWS
        assert keys.nunique() == EXPECTED_ROWS

    def test_key_needs_비고(self, loaded):
        """비고를 키에서 빼면 중복이 생긴다 — 뺄 수 없다는 근거."""
        cols = [c for c in schema.KEY_COLUMNS if c != "비고"]
        reduced = loaded.df[cols].astype(str).agg("\x1f".join, axis=1)
        assert reduced.nunique() < EXPECTED_ROWS

    def test_dimensions_are_strings_without_blanks(self, loaded):
        for col in schema.DIMENSIONS:
            series = loaded.df[col]
            assert series.map(lambda v: isinstance(v, str)).all(), col
            assert not series.str.strip().eq("").any(), col


class TestIdentities:
    """PRD 3.3 — 앱 전체 계산의 근거가 되는 항등식."""

    def test_unit_price_is_sum_of_components(self, loaded):
        df = loaded.df
        expected = df["Base"] + df["Extra"] + df["운임"]
        assert np.allclose(df["단가"], expected, rtol=1e-6, atol=1e-6)

    def test_amount_equals_component_products(self, loaded):
        df = loaded.df
        expected = (df["Base_x_수량"] + df["Extra_x_수량"] + df["운임_x_수량"]) / schema.AMOUNT_SCALE
        assert np.allclose(df["금액"], expected, rtol=RTOL, atol=1e-6)

    def test_amount_approximates_qty_times_price(self, loaded):
        """금액 ≈ 수량 × 단가 ÷ 1000 (단가는 반올림 표시라 근사)."""
        df = loaded.df
        expected = df["수량"] * df["단가"] / schema.AMOUNT_SCALE
        assert np.allclose(df["금액"], expected, rtol=1e-4, atol=1.0)

    def test_component_products_match_components(self, loaded):
        df = loaded.df
        assert np.allclose(df["Base_x_수량"], df["Base"] * df["수량"], rtol=1e-6, atol=1e-3)
        assert np.allclose(df["Extra_x_수량"], df["Extra"] * df["수량"], rtol=1e-6, atol=1e-3)
        assert np.allclose(df["운임_x_수량"], df["운임"] * df["수량"], rtol=1e-6, atol=1e-3)


class TestErrors:
    def test_garbage_bytes_raise_plan_load_error(self):
        with pytest.raises(PlanLoadError):
            load_plan(b"not an xlsx at all")


class TestCrossFile:
    """양식이 같은 두 파일은 구조가 일치해야 한다 (FR-103 의 근거)."""

    def test_same_columns_and_shape(self, before, after):
        assert list(before.df.columns) == list(after.df.columns)
        assert before.row_count == after.row_count
        assert before.periods == after.periods

    def test_key_sets_match(self, before, after):
        assert set(schema.build_key(before.df)) == set(schema.build_key(after.df))
