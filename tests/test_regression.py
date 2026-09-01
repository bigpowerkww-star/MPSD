"""PRD 9.1 회귀 기준값.

8-1주 -> 8-2주 샘플로 아래 숫자가 정확히 재현되어야 한다.
로직을 리팩터링할 때 이 파일이 안전망이 된다.
"""

import numpy as np
import pytest

from core import schema
from core.aggregator import aggregate, period_detail
from core.differ import build_diff

EXPECTED_ROWS = 277
EXPECTED_CHANGED_ROWS = 26
CHANGED_PERIOD = "2026-07"
UNCHANGED_PERIODS = [f"2026-{m:02d}" for m in range(1, 7)]

JULY_QTY_BEFORE = 1_075_073.0
JULY_QTY_AFTER = 1_067_673.0
JULY_QTY_DELTA = -7_400.0

JULY_AMOUNT_BEFORE = 1_430_709.9
JULY_AMOUNT_AFTER = 1_419_218.7
JULY_AMOUNT_DELTA = -11_491.2
JULY_AMOUNT_PCT = -0.80

#: 백만원 단위 표시 반올림 오차 허용치
AMOUNT_TOL = 0.1
QTY_TOL = 0.5


@pytest.fixture(scope="session")
def diff(before, after):
    return build_diff(before.df, after.df, before.source_name, after.source_name)


@pytest.fixture(scope="session")
def monthly(diff):
    return aggregate(diff.rows, axes=(), period_unit="월").set_index("기간")


class TestParsing:
    def test_row_counts(self, before, after):
        assert before.row_count == EXPECTED_ROWS
        assert after.row_count == EXPECTED_ROWS

    def test_composite_key_unique(self, before, after):
        assert schema.build_key(before.df).nunique() == EXPECTED_ROWS
        assert schema.build_key(after.df).nunique() == EXPECTED_ROWS

    def test_no_duplicate_keys_excluded(self, diff):
        assert diff.duplicate_keys == []
        assert diff.excluded_row_count == 0


class TestChangeScope:
    def test_changed_row_count(self, diff):
        assert diff.changed_count == EXPECTED_CHANGED_ROWS

    def test_all_changes_are_in_july(self, diff):
        periods = diff.changed_rows[schema.PERIOD_COLUMN].unique().tolist()
        assert periods == [CHANGED_PERIOD]

    def test_no_added_or_removed_rows(self, diff):
        counts = diff.status_counts()
        assert counts[schema.STATUS_ADDED] == 0
        assert counts[schema.STATUS_REMOVED] == 0

    @pytest.mark.parametrize("period", UNCHANGED_PERIODS)
    def test_untouched_months_have_zero_delta(self, monthly, period):
        assert np.isclose(monthly.loc[period, "금액_증감"], 0.0, atol=1e-6)
        assert np.isclose(monthly.loc[period, "수량_증감"], 0.0, atol=1e-6)


class TestJulyBaseline:
    def test_quantity(self, monthly):
        row = monthly.loc[CHANGED_PERIOD]
        assert row["수량_전"] == pytest.approx(JULY_QTY_BEFORE, abs=QTY_TOL)
        assert row["수량_후"] == pytest.approx(JULY_QTY_AFTER, abs=QTY_TOL)
        assert row["수량_증감"] == pytest.approx(JULY_QTY_DELTA, abs=QTY_TOL)

    def test_amount(self, monthly):
        row = monthly.loc[CHANGED_PERIOD]
        assert row["금액_전"] == pytest.approx(JULY_AMOUNT_BEFORE, abs=AMOUNT_TOL)
        assert row["금액_후"] == pytest.approx(JULY_AMOUNT_AFTER, abs=AMOUNT_TOL)
        assert row["금액_증감"] == pytest.approx(JULY_AMOUNT_DELTA, abs=AMOUNT_TOL)

    def test_amount_pct(self, monthly):
        assert monthly.loc[CHANGED_PERIOD, "금액_증감률"] == pytest.approx(
            JULY_AMOUNT_PCT, abs=0.01
        )

    def test_effects_decompose_july_delta(self, monthly):
        """물량효과 + 단가효과 == -11,491.2 (잔차 0)."""
        row = monthly.loc[CHANGED_PERIOD]
        assert row["물량효과"] + row["단가효과"] == pytest.approx(
            JULY_AMOUNT_DELTA, abs=AMOUNT_TOL
        )

    def test_price_effect_splits_into_components(self, monthly):
        row = monthly.loc[CHANGED_PERIOD]
        components = row["Base기여"] + row["Extra기여"] + row["운임기여"]
        assert components == pytest.approx(row["단가효과"], abs=1e-6)


class TestGrandTotal:
    def test_overall_amounts(self, diff):
        total = aggregate(diff.rows, axes=(), period_unit=None).iloc[0]
        assert total["금액_전"] == pytest.approx(9_117_124.4, abs=AMOUNT_TOL)
        assert total["금액_후"] == pytest.approx(9_105_633.2, abs=AMOUNT_TOL)
        assert total["금액_증감"] == pytest.approx(JULY_AMOUNT_DELTA, abs=AMOUNT_TOL)

    def test_row_count_preserved(self, diff):
        assert len(diff.rows) == EXPECTED_ROWS


class TestPeriodDetail:
    def test_july_detail_matches_baseline(self, diff):
        detail = period_detail(diff.rows, "월", CHANGED_PERIOD, axis="팀파트")
        assert detail.amount_before == pytest.approx(JULY_AMOUNT_BEFORE, abs=AMOUNT_TOL)
        assert detail.amount_after == pytest.approx(JULY_AMOUNT_AFTER, abs=AMOUNT_TOL)
        assert detail.amount_delta == pytest.approx(JULY_AMOUNT_DELTA, abs=AMOUNT_TOL)
        assert detail.counts["변동"] == EXPECTED_CHANGED_ROWS
        assert detail.counts["신규"] == 0
        assert detail.counts["삭제"] == 0

    def test_july_effects_sum(self, diff):
        detail = period_detail(diff.rows, "월", CHANGED_PERIOD, axis="팀파트")
        total = detail.effects["물량효과"] + detail.effects["단가효과"]
        assert total == pytest.approx(JULY_AMOUNT_DELTA, abs=AMOUNT_TOL)

    def test_contributors_sum_to_total_delta(self, diff):
        """기여도 목록에서 잘려나간 항목이 없는지 — 7월은 변동 축이 적어 전체가 들어온다."""
        detail = period_detail(diff.rows, "월", CHANGED_PERIOD, axis="팀파트", top_n=99)
        assert detail.contributors["금액_증감"].sum() == pytest.approx(
            JULY_AMOUNT_DELTA, abs=AMOUNT_TOL
        )

    def test_quarter_detail_matches_july(self, diff):
        """7월만 바뀌었으므로 2026-Q3 증감은 7월 증감과 같아야 한다."""
        detail = period_detail(diff.rows, "분기", "2026-Q3", axis="팀파트")
        assert detail.amount_delta == pytest.approx(JULY_AMOUNT_DELTA, abs=AMOUNT_TOL)
