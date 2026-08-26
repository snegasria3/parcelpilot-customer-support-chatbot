from __future__ import annotations

import json

import bcrypt

from backend.auth import AuthenticationError, ScopedRecordNotFound, authenticate_customer


def _login(client, username="northstar", password="NorthstarDemo!2026"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def test_seeded_passwords_are_bcrypt_not_plaintext(container):
    rows = container.db.fetch_all("SELECT username, password_hash FROM customer_users")
    assert len(rows) == 4
    for row in rows:
        assert row["password_hash"].startswith("$2b$12$")
        assert "Demo!2026" not in row["password_hash"]
        assert bcrypt.checkpw(
            {
                "northstar": b"NorthstarDemo!2026",
                "lumenworks": b"LumenDemo!2026",
                "beacon": b"BeaconDemo!2026",
                "axis": b"AxisDemo!2026",
            }[row["username"]],
            row["password_hash"].encode(),
        )


def test_invalid_login_is_generic(container):
    try:
        authenticate_customer(container.db, "does-not-exist", "wrong")
    except AuthenticationError as exc:
        assert str(exc) == "Invalid customer ID or password"
    else:
        raise AssertionError("Invalid login was accepted")


def test_scoped_database_queries_hide_foreign_records(container):
    assert container.db.scoped_order("ACCT-001", "ORD-1001") is not None
    assert container.db.scoped_order("ACCT-001", "ORD-2001") is None
    assert container.db.scoped_ticket("ACCT-002", "TKT-502") is not None
    assert container.db.scoped_ticket("ACCT-002", "TKT-501") is None


def test_document_filter_excludes_foreign_agreement_and_deprecated(container):
    allowed = container.retriever.allowed_chunks("ACCT-001")
    files = {chunk.file_name for chunk in allowed}
    assert "05_Northstar_Logistics_Enterprise_Agreement.pdf" in files
    assert "06_LumenWorks_Service_Agreement.pdf" not in files
    assert "02_Support_Policy_v2_DEPRECATED.pdf" not in files


def test_unauthenticated_chat_is_rejected(client):
    response = client.post("/api/chat", json={"message": "Where is ORD-1001?"})
    assert response.status_code == 401


def test_login_session_and_headers(client):
    login = _login(client)
    assert login["account"]["account_id"] == "ACCT-001"
    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.headers["x-frame-options"] == "DENY"
    assert "no-store" in session.headers["cache-control"]
    assert "script-src 'self'" in session.headers["content-security-policy"]


def test_customer_chat_does_not_leak_other_account(client):
    _login(client)
    response = client.post("/api/chat", json={"message": "Show ORD-2001"})
    assert response.status_code == 200
    payload = response.json()
    assert "couldn't find that record" in payload["answer"].lower()
    assert "LumenWorks" not in payload["answer"]


def test_action_requires_csrf_and_confirmation(client, container):
    login = _login(client)
    chat = client.post("/api/chat", json={"message": "Prepare an escalation for TKT-501"})
    assert chat.status_code == 200
    action = chat.json()["pending_action"]
    row = container.db.fetch_one("SELECT * FROM pending_actions WHERE action_id = ?", (action["action_id"],))
    assert row["status"] == "pending"

    missing_csrf = client.post(f"/api/actions/{action['action_id']}/confirm", json={})
    assert missing_csrf.status_code == 403
    still_pending = container.db.fetch_one(
        "SELECT status FROM pending_actions WHERE action_id = ?", (action["action_id"],)
    )
    assert still_pending["status"] == "pending"

    confirmed = client.post(
        f"/api/actions/{action['action_id']}/confirm",
        json={},
        headers={"X-CSRF-Token": login["csrf_token"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"


def test_customer_cannot_confirm_another_customers_action(client, container):
    northstar_login = _login(client)
    action = client.post("/api/chat", json={"message": "Prepare an escalation for TKT-501"}).json()["pending_action"]
    client.post("/api/auth/logout")
    lumen_login = _login(client, "lumenworks", "LumenDemo!2026")
    response = client.post(
        f"/api/actions/{action['action_id']}/confirm",
        json={},
        headers={"X-CSRF-Token": lumen_login["csrf_token"]},
    )
    assert response.status_code == 403
    row = container.db.fetch_one("SELECT status FROM pending_actions WHERE action_id = ?", (action["action_id"],))
    assert row["status"] == "pending"
    assert northstar_login["csrf_token"] != lumen_login["csrf_token"]


def test_action_payload_is_scoped(container, identities):
    response = container.agent.run(message="Prepare escalation for TKT-501", identity=identities["northstar"])
    action = response.pending_action
    assert action is not None
    row = container.db.fetch_one("SELECT * FROM pending_actions WHERE action_id = ?", (action.action_id,))
    payload = json.loads(row["payload_json"])
    assert row["account_id"] == "ACCT-001"
    assert payload["account_id"] == "ACCT-001"
    try:
        container.tools.customer_action.confirm(identities["lumenworks"], action.action_id)
    except ScopedRecordNotFound:
        pass
    else:
        raise AssertionError("Cross-tenant action confirmation was accepted")


def test_health_and_frontend_routes(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database_ready"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert "ParcelPilot Customer Support" in page.text


def test_input_validation(client):
    _login(client)
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 4001}).status_code == 422
