"""The service's own contract: the session store really holds a turn, and no request after
session start names a principal."""

from __future__ import annotations

import inspect

from commerce_common.testing import FakeClient, text_message
from fastapi.testclient import TestClient

from service import main as service_main
from tests.test_turn import StubStorefront


def build_client() -> TestClient:
    app = service_main.create_app(
        backend=StubStorefront(),
        model_client=FakeClient([text_message("Hello from the assistant.")]),
        redis_url=None,
    )
    # The app answers only to loopback host names -- rejecting anything else is what
    # stops DNS rebinding, which CORS does not -- and TestClient's default is "testserver".
    return TestClient(app, base_url="http://localhost")


def test_a_turn_streams_and_the_session_holds_the_transcript():
    client = build_client()
    started = client.post("/api/session", json={}).json()
    assert started["guest"] is True
    session_id = started["session_id"]

    streamed = client.post(
        "/api/chat", json={"message": "hello"}, headers={"X-Session-Id": session_id}
    )
    assert streamed.status_code == 200
    assert "Hello from the assistant." in streamed.text

    # Load the session again: the turn is in the store, which is what proves the store.
    reloaded = client.app.state.sessions.require(session_id)
    roles = [message["role"] for message in reloaded.messages]
    assert roles[:2] == ["user", "assistant"]


def test_a_request_without_a_session_is_refused():
    client = build_client()
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 401
    assert client.get("/api/cart", headers={"X-Session-Id": "made-up"}).status_code == 401


def test_no_scoped_request_names_a_principal():
    """Session start is the only place a principal enters. Every other request carries the
    session id alone, so nothing downstream can be talked into acting for someone else."""
    banned = ("user_id", "customer_id", "customerid", "operator", "merchant_id", "anonymous_id")
    for name, model in vars(service_main).items():
        if not (inspect.isclass(model) and name.endswith("Request")):
            continue
        if name == "StartSessionRequest":
            continue
        fields = getattr(model, "model_fields", {})
        offenders = [field for field in fields if field.lower() in banned]
        assert not offenders, f"{name} carries a principal: {offenders}"


def test_the_guest_convention_decides_the_commercetools_identity_field():
    assert service_main.is_guest_principal("anon-abc") is True
    assert service_main.is_guest_principal("a-real-customer-uuid") is False
    guest = service_main.context(
        type("R", (), {"session_id": "s", "user_id": "anon-1", "state": None})()
    )
    member = service_main.context(
        type("R", (), {"session_id": "s", "user_id": "cust-1", "state": None})()
    )
    assert guest.cart_owner_fields() == {"anonymousId": "anon-1"}
    assert member.cart_owner_fields() == {"customerId": "cust-1"}
