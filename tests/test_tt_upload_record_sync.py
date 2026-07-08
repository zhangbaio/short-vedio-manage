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


def _create_tt_user_with_token(
    *,
    username: str = "zhangbiao",
    email: str = "zhangbiao@example.test",
    machine_id: str = "machine-a",
) -> str:
    db = manage_app.get_db()
    db.execute(
        """
        INSERT INTO tt_users (
            username, email, password_hash, status, max_devices, edition, updated_at
        ) VALUES (?, ?, ?, 'active', 1, 'pro', ?)
        """,
        (
            username,
            email,
            generate_password_hash("secret123"),
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    user_row = db.execute("SELECT * FROM tt_users WHERE username = ?", (username,)).fetchone()
    token = manage_app.issue_tt_account_token(user_row=user_row, machine_id=machine_id)
    db.execute(
        """
        INSERT INTO tt_user_devices (
            tt_user_id, machine_id, device_name, app_name, app_version,
            token_hash, logged_in_at, last_verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_row["id"],
            machine_id,
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
                    "remark": "需要人工复核封面",
                }
            ]
        },
    )

    assert sync_response.status_code == 200
    sync_item = sync_response.get_json()["data"]["items"][0]
    assert sync_item["uploader_display"] == "测试1"
    assert sync_item["account_profile_name"] == "测试1"
    assert sync_item["tiktok_username"] == "2720937754@qq.com"
    assert sync_item["remark"] == "需要人工复核封面"

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
    assert item["remark"] == "需要人工复核封面"


def test_tt_platform_dramas_filter_uses_tt_user_owner_id(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        zhaoke_token = _create_tt_user_with_token(
            username="zhaoke",
            email="zhaoke@example.test",
            machine_id="machine-zhaoke",
        )
        other_token = _create_tt_user_with_token(
            username="other",
            email="other@example.test",
            machine_id="machine-other",
        )
        db = manage_app.get_db()
        zhaoke_id = int(db.execute("SELECT id FROM tt_users WHERE username = ?", ("zhaoke",)).fetchone()["id"])
        other_id = int(db.execute("SELECT id FROM tt_users WHERE username = ?", ("other",)).fetchone()["id"])

    for account, machine_id, token, original_name in (
        ("zhaoke", "machine-zhaoke", zhaoke_token, "玉镯莲心"),
        ("other", "machine-other", other_token, "别人的短剧"),
    ):
        response = client.post(
            "/client-api/upload-records/batch",
            headers={
                "X-TT-Account": account,
                "X-TT-Machine-Id": machine_id,
                "X-TT-Token": token,
            },
            json={
                "records": [
                    {
                        "platform": "tt",
                        "sync_key": f"record-{account}",
                        "record_time": "2026-07-08T18:59:43+08:00",
                        "original_name": original_name,
                        "new_name": original_name,
                        "upload_status": "成功",
                    }
                ]
            },
        )
        assert response.status_code == 200

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
        session["user_type"] = "user"

    zhaoke_response = client.get(f"/api/platform-dramas?platform=tt&user_id={zhaoke_id}")
    assert zhaoke_response.status_code == 200
    zhaoke_items = zhaoke_response.get_json()["items"]
    assert [item["owner_username"] for item in zhaoke_items] == ["zhaoke"]
    assert zhaoke_items[0]["original_name"] == "玉镯莲心"

    other_response = client.get(f"/api/platform-dramas?platform=tt&user_id={other_id}")
    assert other_response.status_code == 200
    other_items = other_response.get_json()["items"]
    assert [item["owner_username"] for item in other_items] == ["other"]


def test_tt_duplicate_check_supports_tiktok_username_scope(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        owner_token = _create_tt_user_with_token(
            username="owner",
            email="owner@example.test",
            machine_id="machine-owner",
        )
        other_token = _create_tt_user_with_token(
            username="other",
            email="other@example.test",
            machine_id="machine-other",
        )

    sync_response = client.post(
        "/client-api/upload-records/batch",
        headers={
            "X-TT-Account": "owner",
            "X-TT-Machine-Id": "machine-owner",
            "X-TT-Token": owner_token,
        },
        json={
            "records": [
                {
                    "platform": "tt",
                    "sync_key": "record-duplicate-scope",
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

    software_user_response = client.post(
        "/client-api/upload-records/check-duplicates",
        headers={
            "X-TT-Account": "other",
            "X-TT-Machine-Id": "machine-other",
            "X-TT-Token": other_token,
        },
        json={
            "platform": "tt",
            "dedupe_scope": "software_user",
            "original_names": ["山野孤女京城成长记"],
            "tiktok_username": "2720937754@qq.com",
        },
    )
    assert software_user_response.status_code == 200
    assert software_user_response.get_json()["data"]["duplicates"] == []

    tiktok_username_response = client.post(
        "/client-api/upload-records/check-duplicates",
        headers={
            "X-TT-Account": "other",
            "X-TT-Machine-Id": "machine-other",
            "X-TT-Token": other_token,
        },
        json={
            "platform": "tt",
            "dedupe_scope": "tiktok_username",
            "original_names": ["山野孤女京城成长记"],
            "tiktok_username": "2720937754@qq.com",
        },
    )
    assert tiktok_username_response.status_code == 200
    assert tiktok_username_response.get_json()["data"]["duplicates"] == ["山野孤女京城成长记"]
