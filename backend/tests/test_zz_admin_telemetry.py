from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from backend.app.config import settings


TEST_DB = Path("data/test_evoagent.db")


def register(client, username: str) -> tuple[dict, dict[str, str]]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "display_name": username,
            "password": "admin-telemetry-pass-2026",
        },
    )
    assert response.status_code == 201, response.text
    value = response.json()
    return value, {"Authorization": f"Bearer {value['token']}"}


def login(client, username: str) -> tuple[dict, dict[str, str]]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "admin-telemetry-pass-2026"},
    )
    assert response.status_code == 200, response.text
    value = response.json()
    return value, {"Authorization": f"Bearer {value['token']}"}


def test_admin_rbac_local_outbox_and_privacy_filter(client):
    _admin_created, _ = register(client, "telemetry_admin")
    regular, regular_auth = register(client, "telemetry_regular")

    connection = sqlite3.connect(TEST_DB)
    try:
        connection.execute(
            "UPDATE user_accounts SET role = 'admin' WHERE username = ?",
            ("telemetry_admin",),
        )
        connection.commit()
    finally:
        connection.close()

    admin, admin_auth = login(client, "telemetry_admin")
    assert admin["user"]["role"] == "admin"

    denied = client.get("/api/admin/overview", headers=regular_auth)
    assert denied.status_code == 403

    recorded = client.post(
        "/api/telemetry/events",
        headers=regular_auth,
        json={
            "event_type": "page.viewed",
            "module": "navigation",
            "resource_type": "route",
            "resource_id": "/learning",
            "detail": {
                "page_title": "学习空间",
                "password": "must-not-leak",
                "local_path": "C:\\Users\\someone\\private.pdf",
            },
        },
    )
    assert recorded.status_code == 201, recorded.text

    status = client.get("/api/telemetry/status", headers=regular_auth)
    assert status.status_code == 200, status.text
    assert status.json()["hub_configured"] is False
    assert status.json()["pending"] >= 1

    synced = client.post("/api/telemetry/sync", headers=regular_auth)
    assert synced.status_code == 200, synced.text
    assert synced.json()["configured"] is False
    assert synced.json()["pending"] >= 1

    overview = client.get("/api/admin/overview", headers=admin_auth)
    assert overview.status_code == 200, overview.text
    summary = overview.json()
    assert summary["scope"] == "local"
    assert summary["metrics"]["total_users"] >= 2
    assert summary["pending_local_events"] >= 1

    users = client.get("/api/admin/users", headers=admin_auth)
    assert users.status_code == 200, users.text
    usernames = {item["username"] for item in users.json()}
    assert {"telemetry_admin", "telemetry_regular"}.issubset(usernames)

    events = client.get(
        "/api/admin/events?username=telemetry_regular&module=navigation",
        headers=admin_auth,
    )
    assert events.status_code == 200, events.text
    page_event = next(item for item in events.json() if item["event_type"] == "page.viewed")
    assert page_event["detail"]["password"] == "[redacted]"
    assert page_event["detail"]["local_path"] == "[local-path]"

    disabled = client.patch(
        f"/api/admin/users/{regular['user']['id']}",
        headers=admin_auth,
        json={"status": "disabled", "note": "automated test"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "disabled"

    self_disable = client.patch(
        f"/api/admin/users/{admin['user']['id']}",
        headers=admin_auth,
        json={"status": "disabled", "note": "must be rejected"},
    )
    assert self_disable.status_code == 422


def test_hub_ingestion_is_disabled_without_explicit_server_mode(client):
    response = client.post(
        "/api/telemetry-hub/devices/register",
        json={
            "installation_id": "a" * 48,
            "device_name": "test",
            "platform": "Windows",
            "app_version": "2.1.4",
        },
    )
    assert response.status_code == 404


def test_hub_device_registration_batch_deduplication_and_admin_summary(client):
    previous_mode = settings.telemetry_hub_mode
    previous_key = settings.telemetry_hub_admin_key
    settings.telemetry_hub_mode = True
    settings.telemetry_hub_admin_key = "test-hub-admin-key"
    try:
        registered = client.post(
            "/api/telemetry-hub/devices/register",
            json={
                "installation_id": "b" * 48,
                "device_name": "offline-client",
                "platform": "Windows 11 x64",
                "app_version": "2.1.4",
            },
        )
        assert registered.status_code == 201, registered.text
        device_token = registered.json()["device_token"]
        event = {
            "id": "00000000-0000-4000-8000-000000000214",
            "installation_id": "b" * 48,
            "user_id": "10000000-0000-4000-8000-000000000001",
            "username": "remote_user",
            "event_type": "user.registered",
            "module": "account",
            "resource_type": "user_account",
            "success": True,
            "detail": {"display_name": "远程用户", "password": "redact-me"},
            "client_version": "2.1.4",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        ingested = client.post(
            "/api/telemetry-hub/events/batch",
            headers={"Authorization": f"Device {device_token}"},
            json={"events": [event]},
        )
        assert ingested.status_code == 200, ingested.text
        assert ingested.json()["received"] == 1

        duplicate = client.post(
            "/api/telemetry-hub/events/batch",
            headers={"Authorization": f"Device {device_token}"},
            json={"events": [event]},
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json() == {"received": 0, "duplicates": 1}

        overview = client.get(
            "/api/telemetry-hub/admin/overview",
            headers={"X-Admin-Key": "test-hub-admin-key"},
        )
        assert overview.status_code == 200, overview.text
        assert overview.json()["scope"] == "hub"
        assert overview.json()["metrics"]["devices"] >= 1

        users = client.get(
            "/api/telemetry-hub/admin/users",
            headers={"X-Admin-Key": "test-hub-admin-key"},
        )
        assert users.status_code == 200, users.text
        assert any(item["username"] == "remote_user" for item in users.json())

        events = client.get(
            "/api/telemetry-hub/admin/events?username=remote_user",
            headers={"X-Admin-Key": "test-hub-admin-key"},
        )
        assert events.status_code == 200, events.text
        assert events.json()[0]["detail"]["password"] == "[redacted]"
    finally:
        settings.telemetry_hub_mode = previous_mode
        settings.telemetry_hub_admin_key = previous_key
