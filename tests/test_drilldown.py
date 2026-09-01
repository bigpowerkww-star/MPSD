"""드릴다운(FR-507)과 표시 계층 회귀 테스트.

REVIEW.md 에서 발견된 문제들이 다시 생기지 않게 고정한다.
"""

import importlib.util

import numpy as np
import pandas as pd
import pytest

from core import formatter as fmt
from core import schema
from core.aggregator import build_matrix, period_detail, rows_for_axis
from core.differ import build_diff

PERIOD = "2026-07"


@pytest.fixture(scope="session")
def diff(before, after):
    return build_diff(before.df, after.df, before.source_name, after.source_name)


class TestDrilldownMatchesTable:
    """메인 표에서 고른 행과 드릴다운 결과가 정확히 같은 모집단이어야 한다."""

    @pytest.mark.parametrize("axes", [
        ("팀파트",),
        ("팀파트", "비고"),
        ("팀파트", "고객그룹"),
        ("회의자료_대분류", "품명"),
        ("내수수출", "권역명"),
    ])
    def test_every_row_label_resolves_to_the_right_rows(self, diff, axes):
        axis, second = axes[0], (axes[1] if len(axes) > 1 else None)
        matrix = build_matrix(diff.rows, axis=axis, period_unit="월",
                              measure="금액", second_axis=second)

        for label in matrix.body.index:
            drilled = rows_for_axis(diff.rows, "월", PERIOD, matrix.axes, label,
                                    changed_only=False)
            mask = diff.rows[schema.PERIOD_COLUMN] == PERIOD
            for column, value in zip(matrix.axes, _split_expected(diff.rows, matrix.axes, label)):
                mask &= diff.rows[column].astype(str) == value
            expected = diff.rows[mask]
            assert len(drilled) == len(expected), f"{axes} / {label}"

    def test_two_axis_drilldown_is_narrower_than_one_axis(self, diff):
        """2단계 축을 켜면 1단계만 걸었을 때보다 행이 줄어야 한다 (버그 재발 방지)."""
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월",
                              measure="금액", second_axis="비고")
        multi = [lbl for lbl in matrix.body.index
                 if diff.rows[diff.rows["팀파트"] == lbl.split(" / ")[0]]["비고"].nunique() > 1]
        assert multi, "1:N 조합이 없어 검증할 수 없습니다."

        label = multi[0]
        two = rows_for_axis(diff.rows, "월", PERIOD, matrix.axes, label, changed_only=False)
        one = rows_for_axis(diff.rows, "월", PERIOD, ["팀파트"], label.split(" / ")[0],
                            changed_only=False)
        assert len(two) < len(one), f"{label}: 2단계={len(two)} 1단계={len(one)}"

    def test_axis_value_containing_slash_still_resolves(self, diff):
        """축 값에 '/' 가 들어간다(후판_건설/철구). 라벨을 쪼개면 깨진다."""
        with_slash = [v for v in diff.rows["고객그룹"].unique() if "/" in str(v)]
        assert with_slash, "슬래시가 든 축 값이 없어 검증할 수 없습니다."

        matrix = build_matrix(diff.rows, axis="고객그룹", period_unit="월", measure="금액")
        for value in with_slash:
            drilled = rows_for_axis(diff.rows, "월", PERIOD, matrix.axes, str(value),
                                    changed_only=False)
            expected = diff.rows[
                (diff.rows[schema.PERIOD_COLUMN] == PERIOD)
                & (diff.rows["고객그룹"].astype(str) == str(value))
            ]
            assert len(drilled) == len(expected), value

    def test_drilled_amounts_match_the_cell(self, diff):
        """드릴다운 행들의 금액 합이 표 셀 값과 같아야 한다."""
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월",
                              measure="금액", second_axis="비고")
        for label in matrix.body.index[:6]:
            drilled = rows_for_axis(diff.rows, "월", PERIOD, matrix.axes, label,
                                    changed_only=False)
            assert np.isclose(drilled["금액_후"].sum(),
                              matrix.after.loc[label, PERIOD], atol=1e-4), label


class TestContributorTruncation:
    def test_reports_total_when_truncated(self, diff):
        few = period_detail(diff.rows, "월", PERIOD, axis="팀파트", top_n=2)
        assert few.contributors_truncated
        assert few.contributor_total > len(few.contributors)

    def test_not_truncated_when_all_fit(self, diff):
        full = period_detail(diff.rows, "월", PERIOD, axis="팀파트", top_n=99)
        assert not full.contributors_truncated
        assert full.contributor_total == len(full.contributors)
        assert full.contributors["금액_증감"].sum() == pytest.approx(
            full.amount_delta, abs=0.1
        )


class TestNumberFormatting:
    @pytest.mark.parametrize("value", [-0.04, -0.0, -0.001, -0.049])
    def test_no_negative_zero(self, value):
        assert not fmt.fmt_number(value, 1).startswith("-"), fmt.fmt_number(value, 1)

    def test_real_negatives_keep_sign(self):
        assert fmt.fmt_number(-1.5, 1) == "-1.5"
        assert fmt.fmt_number(-1234.0, 0) == "-1,234"

    @pytest.mark.parametrize("mode", fmt.DISPLAY_MODES)
    def test_display_frame_has_no_negative_zero(self, diff, mode):
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="단가")
        text = fmt.build_display_frame(matrix, mode).to_numpy().ravel()
        assert not [t for t in text if "-0.0" in str(t) or "-0 " in str(t)]


class TestExcelExport:
    def test_export_works_with_current_engine(self, diff):
        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="금액")
        data = fmt.matrix_to_excel(matrix)
        assert data[:2] == b"PK"      # xlsx 는 zip
        assert len(data) > 5000

    def test_falls_back_to_openpyxl_without_xlsxwriter(self, diff, monkeypatch):
        """XlsxWriter 없는 환경(.venv 밖 Anaconda base 등)에서도 죽지 않아야 한다."""
        real_find_spec = importlib.util.find_spec

        def without_xlsxwriter(name, *args, **kwargs):
            if name == "xlsxwriter":
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(fmt.importlib.util, "find_spec", without_xlsxwriter)
        assert fmt._excel_engine() == "openpyxl"

        matrix = build_matrix(diff.rows, axis="팀파트", period_unit="월", measure="금액")
        data = fmt.matrix_to_excel(matrix)
        assert data[:2] == b"PK"


class TestTrendChartColors:
    def test_increase_and_decrease_get_different_colors(self):
        assert fmt.delta_color(100.0) == fmt.INCREASE_COLOR
        assert fmt.delta_color(-100.0) == fmt.DECREASE_COLOR
        assert fmt.delta_color(0.0) == fmt.NEUTRAL_COLOR


def _split_expected(rows: pd.DataFrame, axes: list[str], label: str) -> list[str]:
    """라벨에 대응하는 축 값들을 데이터에서 되찾는다(문자열 분해에 의존하지 않음)."""
    joined = rows[axes].astype(str).agg(" / ".join, axis=1)
    match = rows.loc[joined == label, axes]
    assert not match.empty, label
    return [str(v) for v in match.iloc[0].tolist()]
