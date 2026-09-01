"""st.cache_data 는 반환값을 직렬화해 보관한다.

LoadResult 가 피클 불가능하면 업로드 시점에야 터진다. 여기서 미리 잡는다.
"""

import pickle

import pandas as pd

from core import schema


def test_load_result_is_picklable(before):
    restored = pickle.loads(pickle.dumps(before))
    assert restored.row_count == before.row_count
    assert restored.sheet_name == before.sheet_name
    assert restored.periods == before.periods
    pd.testing.assert_frame_equal(restored.df, before.df)


def test_cached_result_is_not_mutated_by_key_building(before):
    """캐시 반환값을 변조하면 캐시가 오염된다. build_key 는 원본을 건드리지 않아야 한다."""
    snapshot = before.df.copy(deep=True)
    schema.build_key(before.df)
    pd.testing.assert_frame_equal(before.df, snapshot)
