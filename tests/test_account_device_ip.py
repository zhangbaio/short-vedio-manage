from __future__ import annotations

import datetime

import app as manage_app
from werkzeug.security import generate_password_hash


def _setup_test_app(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(manage_app, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(manage_app, "DATABASE", str(data_dir / "dramas.db"))
    monkeypatch.setattr(manage_app, "REMOTE_UPLOAD_DIR", str(data_dir / "remote_uploads"))
    monkeypatch.setattr(manage_app, "start_kuaishou_token_refresh_scheduler", lambda: None)
    monkeypatch.setattr(manage_app, "_lookup_ip_region_online", lambda ip: f"region-{ip}")
    manage_app.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        LICENSE_SIGNING_KEY="test-secret",
    )
    manage_app.init_db()
    return manage_app.app.test_client()


def _create_user() -> None:
    db = manage_app.get_db()
    db.execute(
        """
        INSERT INTO users (
            username, email, password_hash, role, status, max_devices, edition, updated_at
        ) VALUES (?, ?, ?, 'user', 'active', 1, 'pro', ?)
        """,
        (
            "ipuser",
            "ipuser@example.test",
            generate_password_hash("secret123"),
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    db.commit()


def test_account_device_records_login_and_last_ip(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        _create_user()

    login_response = client.post(
        "/account/login",
        headers={"X-Real-IP": "8.8.8.8"},
        json={
            "account": "ipuser",
            "password": "secret123",
            "machine_id": "machine-ip",
            "device_name": "desktop",
            "app_name": "shortdrama",
            "app_version": "1.0",
        },
    )
    assert login_response.status_code == 200
    login_data = login_response.get_json()["data"]

    verify_response = client.post(
        "/account/verify",
        headers={"X-Real-IP": "1.1.1.1"},
        json={
            "account": "ipuser",
            "machine_id": "machine-ip",
            "token": login_data["token"],
            "device_name": "desktop",
            "app_name": "shortdrama",
            "app_version": "1.0",
        },
    )
    assert verify_response.status_code == 200

    with manage_app.app.app_context():
        row = manage_app.get_db().execute(
            "SELECT login_ip, login_ip_region, last_ip, last_ip_region FROM user_devices WHERE machine_id = ?",
            ("machine-ip",),
        ).fetchone()

    assert dict(row) == {
        "login_ip": "8.8.8.8",
        "login_ip_region": "region-8.8.8.8",
        "last_ip": "1.1.1.1",
        "last_ip_region": "region-1.1.1.1",
    }
