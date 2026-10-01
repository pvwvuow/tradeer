from app.storage.remote import AuthSession, RemoteErrorKind, classify


def kind(status: int, body: str = "") -> RemoteErrorKind:
    return classify(status, body, {}).kind


def test_http_answers_are_sorted_for_the_sync_engine() -> None:
    assert kind(401, '{"message":"JWT expired"}') is RemoteErrorKind.AUTH
    assert kind(404, '{"code":"PGRST205","message":"no such table"}') is RemoteErrorKind.SETUP
    assert kind(400, '{"code":"PGRST204","message":"no such column"}') is RemoteErrorKind.SETUP
    assert kind(400, '{"code":"22P02","message":"invalid uuid"}') is RemoteErrorKind.REJECTED
    assert kind(403, '{"code":"42501","message":"row-level security"}') is RemoteErrorKind.REJECTED
    assert kind(540, "Project paused") is RemoteErrorKind.PAUSED
    assert kind(503, "This project is paused") is RemoteErrorKind.PAUSED
    assert kind(502, "Bad gateway") is RemoteErrorKind.SERVER


def test_rate_limits_keep_the_retry_after_header() -> None:
    limited = classify(429, "", {"retry-after": "30"})
    assert (limited.kind, limited.retry_after) == (RemoteErrorKind.RATE_LIMITED, 30.0)
    assert classify(429, "", {"retry-after": "soon"}).retry_after is None


def test_error_messages_include_the_status_and_the_code() -> None:
    error = classify(400, '{"code":"23502","message":"null value in column"}', {})
    assert error.message == "HTTP 400 23502: null value in column"
    assert error.status == 400


def test_sessions_never_show_their_tokens() -> None:
    session = AuthSession("user", "trader@example.com", "access-secret", "refresh-secret", 10.0)
    assert "secret" not in repr(session)
    assert session.expires_soon(now=0.0, margin_seconds=60.0)
    assert not session.expires_soon(now=0.0, margin_seconds=5.0)
    assert AuthSession("user", "e", "", "refresh", 1e12).expires_soon(now=0.0)
