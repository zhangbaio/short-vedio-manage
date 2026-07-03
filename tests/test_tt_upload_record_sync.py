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
    manage_app.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        LICENSE_SIGNING_KEY="test-secret",
    )
    manage_app.init_db()
    return manage_app.app.test_client()


def _create_tt_user_with_token() -> str:
    db = manage_app.get_db()
    db.execute(
        """
        INSERT INTO tt_users (
            username, email, password_hash, status, max_devices, edition, updated_at
        ) VALUES (?, ?, ?, 'active', 1, 'pro', ?)
        """,
        (
            "zhangbiao",
            "zhangbiao@example.test",
            generate_password_hash("secret123"),
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    user_row = db.execute("SELECT * FROM tt_users WHERE username = ?", ("zhangbiao",)).fetchone()
    token = manage_app.issue_tt_account_token(user_row=user_row, machine_id="machine-a")
    db.execute(
        """
        INSERT INTO tt_user_devices (
            tt_user_id, machine_id, device_name, app_name, app_version,
            token_hash, logged_in_at, last_verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_row["id"],
            "machine-a",
            "desktop",
            "TikTok Uploader",
            "1.0",
            manage_app.hash_token(token),
            "2026-07-03T08:00:00",
            "2026-07-03T08:00:00",
        ),
    )
    db.commit()
    return token


def test_tt_upload_record_sync_splits_account_nickname_and_username(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        token = _create_tt_user_with_token()

    sync_response = client.post(
        "/client-api/upload-records/batch",
        headers={
            "X-TT-Account": "zhangbiao",
            "X-TT-Machine-Id": "machine-a",
            "X-TT-Token": token,
        },
        json={
            "records": [
                {
                    "platform": "tt",
                    "sync_key": "record-1",
                    "record_time": "2026-07-03 09:00:00",
                    "original_name": "山野孤女京城成长记",
                    "new_name": "山野孤女京城成长记",
                    "upload_status": "成功",
                    "uploader_display": "测试1",
                    "account_profile_name": "测试1",
                    "tiktok_username": "2720937754@qq.com",
                }
            ]
        },
    )

    assert sync_response.status_code == 200
    sync_item = sync_response.get_json()["data"]["items"][0]
    assert sync_item["uploader_display"] == "测试1"
    assert sync_item["account_profile_name"] == "测试1"
    assert sync_item["tiktok_username"] == "2720937754@qq.com"

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
        session["user_type"] = "user"

    list_response = client.get("/api/platform-dramas?platform=tt")

    assert list_response.status_code == 200
    item = list_response.get_json()["items"][0]
    assert item["uploader_display"] == "测试1"
    assert item["account_profile_name"] == "测试1"
    assert item["tiktok_username"] == "2720937754@qq.com"
