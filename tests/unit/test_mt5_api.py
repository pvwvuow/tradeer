import sys

import pytest

from app.mt5 import api

COPIED = [name for name in dir(api) if name.isupper() and name != "TIMEFRAMES"]


@pytest.mark.skipif(sys.platform != "win32", reason="MetaTrader5 ships Windows wheels only")
def test_copied_constants_match_the_real_package() -> None:
    import MetaTrader5

    different = {
        name: (getattr(api, name), getattr(MetaTrader5, name, None))
        for name in COPIED
        if getattr(MetaTrader5, name, None) != getattr(api, name)
    }
    assert different == {}


def test_timeframe_names_map_to_the_copied_constants() -> None:
    assert api.TIMEFRAMES["M15"] == api.TIMEFRAME_M15 == 15
    assert api.TIMEFRAMES["H1"] == api.TIMEFRAME_H1
    assert len(COPIED) > 30
