from __future__ import annotations

import app as manage_app
from werkzeug.security import generate_password_hash


def _setup_test_app(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(manage_app, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(manage_app, "DATABASE", str(data_dir / "dramas.db"))
    monkeypatch.setattr(manage_app, "REMOTE_UPLOAD_DIR", str(data_dir / "remote_uploads"))
    monkeypatch.setattr(manage_app, "start_kuaishou_token_refresh_scheduler", lambda: None)
    manage_app.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        LICENSE_SIGNING_KEY="test-secret",
    )
    manage_app.init_db()
    return manage_app.app.test_client()


def _add_ks_user(username: str, password: str, *, status: str = "active", max_devices: int = 1) -> int:
    with manage_app.app.app_context():
        return _insert_ks_user(username, password, status=status, max_devices=max_devices)


def _insert_ks_user(username: str, password: str, *, status: str, max_devices: int) -> int:
    db = manage_app.get_db()
    db.execute(
        """
        INSERT INTO ks_users (
            username, password_hash, status, max_devices, edition, updated_at
        ) VALUES (?, ?, ?, ?, 'pro', CURRENT_TIMESTAMP)
        """,
        (username, generate_password_hash(password), status, max_devices),
    )
    db.commit()
    return int(db.execute("SELECT id FROM ks_users WHERE username = ?", (username,)).fetchone()["id"])


def _login(client, username: str, password: str, machine_id: str = "machine-a"):
    return client.post(
        "/ks/account/login",
        json={
            "account": username,
            "password": password,
            "machine_id": machine_id,
            "device_name": "yunfan",
            "app_name": "云帆多平台发布助手",
            "app_version": "0.1.0",
        },
    )


def test_ks_login_verify_and_logout(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    _add_ks_user("yunfan1", "secret1")

    logged_in = _login(client, "yunfan1", "secret1")
    assert logged_in.status_code == 200
    body = logged_in.get_json()["data"]
    assert body["username"] == "yunfan1"
    assert body["authorization_ticket"]

    verified = client.post(
        "/ks/account/verify",
        json={
            "account": "yunfan1",
            "machine_id": "machine-a",
            "token": body["token"],
            "device_name": "yunfan",
            "app_name": "云帆多平台发布助手",
            "app_version": "0.1.0",
        },
    )
    assert verified.status_code == 200
    current_token = verified.get_json()["data"]["token"]

    logged_out = client.post(
        "/ks/account/logout",
        json={"machine_id": "machine-a", "token": current_token, "account": "yunfan1"},
    )
    assert logged_out.status_code == 200

    rejected = client.post(
        "/ks/account/verify",
        json={
            "account": "yunfan1",
            "machine_id": "machine-a",
            "token": current_token,
            "app_name": "云帆多平台发布助手",
            "app_version": "0.1.0",
        },
    )
    assert rejected.status_code == 400


def test_second_machine_is_rejected_until_revoked(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    _add_ks_user("yunfan2", "secret2", max_devices=1)
    assert _login(client, "yunfan2", "secret2", "machine-a").status_code == 200
    second = _login(client, "yunfan2", "secret2", "machine-b")
    assert second.status_code == 400
    assert "其他电脑" in second.get_json()["message"]


def test_tt_token_cannot_authorize_yunfan(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        db = manage_app.get_db()
        db.execute(
        """
        INSERT INTO tt_users (username, password_hash, status, max_devices, edition)
        VALUES (?, ?, 'active', 1, 'pro')
        """,
            ("tt-only", generate_password_hash("secret3")),
        )
        db.commit()
        user_row = db.execute("SELECT * FROM tt_users WHERE username = ?", ("tt-only",)).fetchone()
        token = manage_app.issue_tt_account_token(user_row=user_row, machine_id="machine-a")
    rejected = client.post(
        "/ks/account/verify",
        json={
            "account": "tt-only",
            "machine_id": "machine-a",
            "token": token,
            "app_name": "云帆多平台发布助手",
            "app_version": "0.1.0",
        },
    )
    assert rejected.status_code == 400


def test_admin_can_create_ks_user(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"

    created = client.post(
        "/api/ks-users",
        json={
            "username": "ksadmin",
            "password": "secret4",
            "status": "active",
            "edition": "pro",
            "max_devices": 1,
        },
    )
    assert created.status_code == 201
    listed = client.get("/api/ks-users")
    assert listed.status_code == 200
    assert any(item["username"] == "ksadmin" for item in listed.get_json())
