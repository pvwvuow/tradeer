import json

import httpx
import pytest

from app.storage.remote import RemoteError, RemoteErrorKind
from app.storage.supabase_client import SupabaseClient

URL = "https://abcdefgh.supabase.co"
KEY = "sb_publishable_test"
SESSION = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 3600,
    "user": {"id": "user-1", "email": "trader@example.com"},
}
Handler = object


def client_for(handler: object) -> SupabaseClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return SupabaseClient(URL, KEY, transport=transport, clock=lambda: 1000.0)


def test_upserts_are_idempotent_requests_with_the_user_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    client_for(handler).upsert("trades", [{"id": "a", "user_id": "u"}], "access-1")
    request = seen[0]
    assert (request.method, request.url.path) == ("POST", "/rest/v1/trades")
    assert request.url.params["on_conflict"] == "id"
    assert request.headers["apikey"] == KEY
    assert request.headers["authorization"] == "Bearer access-1"
    assert request.headers["prefer"] == "resolution=merge-duplicates,return=minimal"
    assert json.loads(request.content) == [{"id": "a", "user_id": "u"}]


def test_sign_in_and_refresh_return_a_session() -> None:
    grants: list[tuple[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/token"
        grants.append((request.url.params["grant_type"], sorted(json.loads(request.content))))
        return httpx.Response(200, json=SESSION)

    client = client_for(handler)
    session = client.sign_in("trader@example.com", "secret")
    assert (session.user_id, session.email, session.expires_at) == (
        "user-1",
        "trader@example.com",
        4600.0,
    )
    assert client.refresh("refresh-1").refresh_token == "refresh-1"
    assert grants == [("password", ["email", "password"]), ("refresh_token", ["refresh_token"])]


def test_a_wrong_password_is_an_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": "invalid_grant", "error_description": "Invalid login credentials"}
        return httpx.Response(400, json=body)

    with pytest.raises(RemoteError) as error:
        client_for(handler).sign_in("trader@example.com", "wrong")
    assert error.value.kind is RemoteErrorKind.AUTH
    assert "Invalid login credentials" in error.value.message


def test_sign_up_with_an_email_confirmation_returns_no_session() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/signup"
        return httpx.Response(200, json={"id": "user-1", "email": "trader@example.com"})

    assert client_for(handler).sign_up("trader@example.com", "secret") is None


def test_network_failures_and_server_errors_are_reported_as_remote_errors() -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "PGRST205", "message": "Could not find the table"})

    for handler, expected in ((offline, RemoteErrorKind.OFFLINE), (missing, RemoteErrorKind.SETUP)):
        with pytest.raises(RemoteError) as error:
            client_for(handler).upsert("trades", [{"id": "a"}], "token")
        assert error.value.kind is expected
