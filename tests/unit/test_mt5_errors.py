from app.mt5 import api
from app.mt5.errors import SAME_USER_HINT, error_from_last, explain


def test_common_errors_get_a_plain_title_and_a_fix() -> None:
    login = explain(api.RES_E_AUTH_FAILED, "Terminal: Authorization failed")
    assert "wrong login number, password or server" in login.title
    assert "server name is case-sensitive" in login.fix
    timeout = explain(api.RES_E_INTERNAL_FAIL_TIMEOUT, "IPC timeout")
    assert "IPC timeout" in timeout.title
    assert SAME_USER_HINT in timeout.fix
    assert "Algo Trading" in explain(api.RES_E_AUTO_TRADING_DISABLED).fix


def test_ipc_errors_say_how_this_app_runs() -> None:
    elevated = explain(api.RES_E_INTERNAL_FAIL_INIT, "IPC initialize failed", elevated=True)
    assert elevated.fix.endswith("This app is running as administrator right now.")
    normal = explain(api.RES_E_INTERNAL_FAIL_TIMEOUT, "", elevated=False)
    assert "normally (not as administrator)" in normal.fix
    assert "right now" not in explain(api.RES_E_AUTH_FAILED, "", elevated=True).fix


def test_unknown_codes_and_text_only_errors() -> None:
    assert explain(-12345).title == "MT5 reported an error"
    assert "wrong login" in explain(None, "authorization failed").title
    error = error_from_last((api.RES_E_AUTH_FAILED, "Terminal: Authorization failed"))
    assert error.code == api.RES_E_AUTH_FAILED
    assert error.detail == "Terminal: Authorization failed"
    assert "wrong login" in str(error)
    assert error_from_last(None).code is None
