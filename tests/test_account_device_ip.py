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


def _create_user() -> int:
    db = manage_app.get_db()
    cursor = db.execute(
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
    return int(cursor.lastrowid)


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


def test_ip_region_text_normalizes_common_isp_to_chinese() -> None:
    assert (
        manage_app.normalize_ip_region_text("中国 湖北 武汉 China Mobile communications corporation")
        == "中国 湖北 武汉 中国移动"
    )
    assert manage_app.normalize_ip_region_text("中国 湖北 武汉 China Telecom") == "中国 湖北 武汉 中国电信"
    assert manage_app.normalize_ip_region_text("中国 湖北 武汉 China Unicom") == "中国 湖北 武汉 中国联通"


def test_user_device_list_backfills_unknown_ip_region(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        user_id = _create_user()
        manage_app.get_db().execute(
            """
            INSERT INTO user_devices (
                user_id, machine_id, device_name, app_name, app_version,
                token_hash, logged_in_at, last_verified_at,
                login_ip, login_ip_region, last_ip, last_ip_region
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                "machine-backfill",
                "desktop",
                "shortdrama",
                "1.0",
                "token-hash",
                "2026-07-01T10:00:00",
                "2026-07-01T10:00:00",
                "8.8.8.8",
                "未知",
                "8.8.8.8",
                "未知",
            ),
        )
        manage_app.get_db().commit()

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"

    response = client.get(f"/api/users/{user_id}/devices")
    assert response.status_code == 200
    device = response.get_json()["devices"][0]
    assert device["last_ip_region"] == "region-8.8.8.8"
    assert device["login_ip_region"] == "region-8.8.8.8"

    with manage_app.app.app_context():
        row = manage_app.get_db().execute(
            "SELECT login_ip_region, last_ip_region FROM user_devices WHERE machine_id = ?",
            ("machine-backfill",),
        ).fetchone()
    assert dict(row) == {
        "login_ip_region": "region-8.8.8.8",
        "last_ip_region": "region-8.8.8.8",
    }


def test_user_device_list_normalizes_existing_english_isp_region(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        user_id = _create_user()
        manage_app.get_db().execute(
            """
            INSERT INTO user_devices (
                user_id, machine_id, device_name, app_name, app_version,
                token_hash, logged_in_at, last_verified_at,
                login_ip, login_ip_region, last_ip, last_ip_region
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                "machine-cn-region",
                "desktop",
                "shortdrama",
                "1.0",
                "token-hash",
                "2026-07-01T10:00:00",
                "2026-07-01T10:00:00",
                "117.152.1.139",
                "中国 湖北 武汉 China Mobile communications corporation",
                "117.152.1.139",
                "中国 湖北 武汉 China Mobile communications corporation",
            ),
        )
        manage_app.get_db().commit()

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"

    response = client.get(f"/api/users/{user_id}/devices")
    assert response.status_code == 200
    device = response.get_json()["devices"][0]
    assert device["last_ip_region"] == "中国 湖北 武汉 中国移动"
    assert device["login_ip_region"] == "中国 湖北 武汉 中国移动"

    with manage_app.app.app_context():
        row = manage_app.get_db().execute(
            "SELECT login_ip_region, last_ip_region FROM user_devices WHERE machine_id = ?",
            ("machine-cn-region",),
        ).fetchone()
    assert dict(row) == {
        "login_ip_region": "中国 湖北 武汉 中国移动",
        "last_ip_region": "中国 湖北 武汉 中国移动",
    }


def test_user_and_tt_lists_include_latest_ip_region(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        user_id = _create_user()
        db = manage_app.get_db()
        db.execute(
            """
            INSERT INTO user_devices (
                user_id, machine_id, device_name, app_name, app_version,
                token_hash, logged_in_at, last_verified_at,
                login_ip, login_ip_region, last_ip, last_ip_region
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                "machine-user-list",
                "desktop",
                "shortdrama",
                "1.0",
                "token-hash",
                "2026-07-01T10:00:00",
                "2026-07-01T10:00:00",
                "117.152.1.139",
                "中国 湖北 武汉 China Mobile communications corporation",
                "117.152.1.139",
                "中国 湖北 武汉 China Mobile communications corporation",
            ),
        )
        cursor = db.execute(
            """
            INSERT INTO tt_users (
                username, email, password_hash, status, max_devices, edition, updated_at
            ) VALUES (?, ?, ?, 'active', 1, 'pro', ?)
            """,
            (
                "ttipuser",
                "ttipuser@example.test",
                generate_password_hash("secret123"),
                datetime.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        tt_user_id = int(cursor.lastrowid)
        db.execute(
            """
            INSERT INTO tt_user_devices (
                tt_user_id, machine_id, device_name, app_name, app_version,
                token_hash, logged_in_at, last_verified_at,
                login_ip, login_ip_region, last_ip, last_ip_region
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tt_user_id,
                "machine-tt-list",
                "desktop",
                "shortdrama",
                "1.0",
                "token-hash",
                "2026-07-01T10:00:00",
                "2026-07-01T10:00:00",
                "27.17.224.43",
                "中国 湖北 武汉 China Telecom",
                "27.17.224.43",
                "中国 湖北 武汉 China Telecom",
            ),
        )
        db.commit()

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"

    users = client.get("/api/users").get_json()
    user_item = next(item for item in users if item["id"] == user_id)
    assert user_item["ip_region"] == "中国 湖北 武汉 中国移动"

    tt_users = client.get("/api/tt-users").get_json()
    tt_item = next(item for item in tt_users if item["id"] == tt_user_id)
    assert tt_item["ip_region"] == "中国 湖北 武汉 中国电信"
