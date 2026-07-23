from __future__ import annotations

import datetime

import app as manage_app
import pytest
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


def _create_tt_user_with_devices(*, max_devices: int = 1) -> tuple[str, str]:
    db = manage_app.get_db()
    db.execute(
        """
        INSERT INTO tt_users (
            username, email, password_hash, status, max_devices, edition, updated_at
        ) VALUES (?, ?, ?, 'active', ?, 'pro', ?)
        """,
        (
            "zhangbiao",
            "zhangbiao@example.test",
            generate_password_hash("secret123"),
            max_devices,
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    user_row = db.execute("SELECT * FROM tt_users WHERE username = ?", ("zhangbiao",)).fetchone()
    token_a = manage_app.issue_tt_account_token(user_row=user_row, machine_id="machine-a")
    token_b = manage_app.issue_tt_account_token(user_row=user_row, machine_id="machine-b")
    db.executemany(
        """
        INSERT INTO tt_user_devices (
            tt_user_id, machine_id, device_name, app_name, app_version,
            token_hash, logged_in_at, last_verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                user_row["id"],
                "machine-a",
                "old-device",
                "TikTok Uploader",
                "1.0",
                manage_app.hash_token(token_a),
                "2026-06-21T08:00:00",
                "2026-06-21T08:00:00",
            ),
            (
                user_row["id"],
                "machine-b",
                "new-device",
                "TikTok Uploader",
                "1.0",
                manage_app.hash_token(token_b),
                "2026-06-21T09:00:00",
                "2026-06-21T09:00:00",
            ),
        ],
    )
    db.commit()
    return token_a, token_b


def test_tt_account_verify_rejects_device_outside_current_limit(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        token_a, token_b = _create_tt_user_with_devices(max_devices=1)

    rejected = client.post(
        "/tt/account/verify",
        json={
            "account": "zhangbiao",
            "machine_id": "machine-a",
            "token": token_a,
            "device_name": "old-device",
            "app_name": "TikTok Uploader",
            "app_version": "1.0",
        },
    )
    assert rejected.status_code == 400
    assert "设备已超过上限" in rejected.get_json()["message"]

    accepted = client.post(
        "/tt/account/verify",
        json={
            "account": "zhangbiao",
            "machine_id": "machine-b",
            "token": token_b,
            "device_name": "new-device",
            "app_name": "TikTok Uploader",
            "app_version": "1.0",
        },
    )
    assert accepted.status_code == 200
    response = accepted.get_json()
    assert response["ok"] is True
    ticket = response["data"]["authorization_ticket"]
    claims = manage_app.verify_tt_authorization_ticket(ticket)
    assert claims["subject"] == "zhangbiao"
    assert claims["machine_id"] == "machine-b"
    assert claims["app_name"] == "TikTok Uploader"
    assert claims["app_version"] == "1.0"
    assert claims["token_sha256"] == manage_app.hash_token(response["data"]["token"])

    parts = ticket.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    with pytest.raises(ValueError):
        manage_app.verify_tt_authorization_ticket(".".join(parts))
