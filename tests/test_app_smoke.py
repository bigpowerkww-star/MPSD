"""앱이 실제 데이터로 끝까지 렌더되는지 확인한다.

file_uploader 는 AppTest 로 조작할 수 없지만, 이 앱은 업로드 즉시 바이트를
session_state 에 넣고 그 뒤로는 session_state 만 읽는다. 그래서 session_state 를
미리 채우면 업로드 이후의 모든 경로를 그대로 태울 수 있다.
"""

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import AFTER_FILE, BEFORE_FILE, ROOT

APP = str(ROOT / "app.py")
TIMEOUT = 120


def _seeded_app(**session) -> AppTest:
    if not (BEFORE_FILE.exists() and AFTER_FILE.exists()):
        pytest.skip("샘플 파일이 없습니다.")
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    app.session_state["before_bytes"] = BEFORE_FILE.read_bytes()
    app.session_state["before_name"] = BEFORE_FILE.name
    app.session_state["after_bytes"] = AFTER_FILE.read_bytes()
    app.session_state["after_name"] = AFTER_FILE.name
    for key, value in session.items():
        app.session_state[key] = value
    return app


def test_app_starts_without_files():
    app = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not app.exception
    assert any("업로드" in info.value for info in app.info)


def test_app_renders_analysis_with_samples():
    app = _seeded_app().run()
    assert not app.exception, app.exception
    rendered = " ".join(str(m.value) for m in app.markdown)
    assert "효과 분해" in rendered
    assert len(app.dataframe) > 0


def test_grand_total_metric_matches_baseline():
    """화면에 찍히는 총계가 회귀 기준값과 같은지 — 렌더 단계에서 숫자가 틀어지지 않았는지."""
    app = _seeded_app().run()
    assert not app.exception
    values = [str(m.value) for m in app.metric]
    deltas = [str(m.delta) for m in app.metric]

    # 총계
    assert any("9,117,124" in v for v in values), values
    assert any("9,105,633" in v for v in values), values
    # 7월 상세 (기본 선택은 마지막 기간)
    assert any("1,430,710" in v for v in values), values
    assert any("1,419,219" in v for v in values), values
    # 증감은 metric 의 delta 에 실린다. 부호는 화살표+색으로 표현되므로 절대값이다.
    assert any("11,491" in d for d in deltas), deltas
    assert any("0.80%" in d for d in deltas), deltas


def test_delta_direction_is_down():
    """감소인데 위쪽 화살표가 뜨면 안 된다."""
    from streamlit.proto.Metric_pb2 import Metric as MetricProto

    app = _seeded_app().run()
    with_delta = [m for m in app.metric if m.delta not in (None, "")]
    assert with_delta
    down = MetricProto.MetricDirection.DOWN
    assert all(m.proto.direction == down for m in with_delta), [
        (m.label, m.proto.direction) for m in with_delta
    ]


def test_decrease_is_blue_not_green():
    """PRD 6.2 — 국내 재무 관행: 증가=빨강, 감소=파랑.

    st.metric 기본값은 증가=초록이고 delta_color='inverse' 는 감소=초록이라
    둘 다 틀리다. 색 이름을 직접 지정했는지 확인한다.
    샘플은 전부 감소이므로 delta 가 붙은 metric 은 모두 파랑이어야 한다.
    """
    from streamlit.proto.Metric_pb2 import Metric as MetricProto

    app = _seeded_app().run()
    assert not app.exception
    with_delta = [m for m in app.metric if m.delta not in (None, "")]
    assert with_delta, "delta 가 붙은 metric 이 없습니다."

    blue = MetricProto.MetricColor.BLUE
    green = MetricProto.MetricColor.GREEN
    colors = [(m.label, m.proto.color) for m in with_delta]
    assert all(c == blue for _, c in colors), colors
    assert not any(c == green for _, c in colors), colors


def test_waterfall_chart_is_rendered():
    app = _seeded_app().run()
    assert not app.exception
    assert len(app.get("plotly_chart")) >= 1


def test_download_buttons_exist():
    app = _seeded_app().run()
    assert not app.exception
    labels = [b.label for b in app.get("download_button")]
    assert any("Excel" in label for label in labels), labels
    assert any("CSV" in label for label in labels), labels


@pytest.mark.parametrize("unit", ["월", "분기", "반기", "연간"])
def test_every_period_unit_renders(unit):
    app = _seeded_app().run()
    app.radio[0].set_value(unit).run()
    assert not app.exception, f"{unit}: {app.exception}"


@pytest.mark.parametrize("measure", ["금액", "수량", "단가"])
def test_every_measure_renders(measure):
    app = _seeded_app().run()
    app.radio[1].set_value(measure).run()
    assert not app.exception, f"{measure}: {app.exception}"


def test_axis_switch_renders():
    app = _seeded_app().run()
    app.selectbox[0].set_value("내수수출").run()
    assert not app.exception, app.exception


def test_changed_only_filter_renders():
    app = _seeded_app().run()
    app.checkbox[0].set_value(True).run()
    assert not app.exception, app.exception


def test_same_file_on_both_slots_warns():
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    payload = BEFORE_FILE.read_bytes()
    app.session_state["before_bytes"] = payload
    app.session_state["before_name"] = BEFORE_FILE.name
    app.session_state["after_bytes"] = payload
    app.session_state["after_name"] = BEFORE_FILE.name
    app.run()
    assert not app.exception
    assert any("같은 파일" in w.value for w in app.warning)


def test_only_one_file_uploaded_does_not_crash():
    app = AppTest.from_file(APP, default_timeout=TIMEOUT)
    app.session_state["before_bytes"] = BEFORE_FILE.read_bytes()
    app.session_state["before_name"] = BEFORE_FILE.name
    app.run()
    assert not app.exception


def test_widget_changes_do_not_reparse_excel(monkeypatch):
    """FR-108 — 위젯을 건드릴 때마다 Excel 을 다시 파면 체감 속도가 무너진다.

    loader 에 호출 카운터를 심어 캐시가 실제로 먹는지 확인한다.
    """
    import core.loader as loader_module

    calls = {"n": 0}
    original = loader_module.load_plan

    def counting_load_plan(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(loader_module, "load_plan", counting_load_plan)

    app = _seeded_app().run()
    assert not app.exception

    calls["n"] = 0  # 최초 파싱은 정상. 이후가 문제다.
    app.radio[0].set_value("분기").run()
    app.selectbox[0].set_value("내수수출").run()
    app.checkbox[0].set_value(True).run()

    assert not app.exception
    assert calls["n"] == 0, f"위젯 조작 중 Excel 을 {calls['n']}회 다시 파싱했습니다."
