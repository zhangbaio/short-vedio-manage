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


def _add_tt_device_token(*, user_id: int, machine_id: str) -> str:
    db = manage_app.get_db()
    user_row = db.execute("SELECT * FROM tt_users WHERE id = ?", (user_id,)).fetchone()
    token = manage_app.issue_tt_account_token(user_row=user_row, machine_id=machine_id)
    db.execute(
        """
        INSERT INTO tt_user_devices (
            tt_user_id, machine_id, device_name, app_name, app_version,
            token_hash, logged_in_at, last_verified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            machine_id,
            "desktop",
            "TikTok Uploader",
            "1.0",
            manage_app.hash_token(token),
            "2026-07-14T08:00:00",
            "2026-07-14T08:00:00",
        ),
    )
    db.commit()
    return token


def _create_tt_user_with_token(
    *,
    username: str = "zhangbiao",
    email: str = "zhangbiao@example.test",
    machine_id: str = "machine-a",
    max_devices: int = 2,
) -> tuple[int, str]:
    db = manage_app.get_db()
    db.execute(
        """
        INSERT INTO tt_users (
            username, email, password_hash, status, max_devices, edition, updated_at
        ) VALUES (?, ?, ?, 'active', ?, 'pro', ?)
        """,
        (
            username,
            email,
            generate_password_hash("secret123"),
            max_devices,
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )
    user_id = int(
        db.execute(
            "SELECT id FROM tt_users WHERE username = ?",
            (username,),
        ).fetchone()["id"]
    )
    db.commit()
    return user_id, _add_tt_device_token(user_id=user_id, machine_id=machine_id)


def _headers(*, account: str, machine_id: str, token: str) -> dict[str, str]:
    return {
        "X-TT-Account": account,
        "X-TT-Machine-Id": machine_id,
        "X-TT-Token": token,
    }


def test_tt_account_snapshot_is_idempotent_and_reconciles_admin_list(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        _, token = _create_tt_user_with_token()

    headers = _headers(account="zhangbiao", machine_id="machine-a", token=token)
    first_payload = {
        "accounts": [
            {
                "client_account_id": "acct-a",
                "tiktok_username": "1544722162@qq.com",
                "subject_company": "武汉速视科技有限公司",
            },
            {
                "client_account_id": "acct-b",
                "tiktok_username": "2720937754@qq.com",
                "subject_company": "湖北云漫科技有限公司",
            },
        ]
    }
    first = client.put("/client-api/tt/accounts/snapshot", headers=headers, json=first_payload)
    assert first.status_code == 200
    assert first.get_json()["data"] == {
        "total": 2,
        "created": 2,
        "updated": 0,
        "deleted": 0,
    }

    repeated = client.put("/client-api/tt/accounts/snapshot", headers=headers, json=first_payload)
    assert repeated.status_code == 200
    assert repeated.get_json()["data"] == {
        "total": 2,
        "created": 0,
        "updated": 0,
        "deleted": 0,
    }

    replaced = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=headers,
        json={
            "accounts": [
                {
                    "client_account_id": "acct-a",
                    "tiktok_username": "renamed@example.test",
                    "subject_company": "武汉速视科技有限公司2",
                },
                {
                    "client_account_id": "acct-c",
                    "tiktok_username": "15327086817@163.com",
                    "subject_company": "湖北云漫科技有限公司",
                },
            ]
        },
    )
    assert replaced.status_code == 200
    assert replaced.get_json()["data"] == {
        "total": 2,
        "created": 1,
        "updated": 1,
        "deleted": 1,
    }

    with manage_app.app.app_context():
        rows = manage_app.get_db().execute(
            """
            SELECT client_account_id, tiktok_username, subject_company
            FROM tt_client_accounts
            ORDER BY client_account_id
            """
        ).fetchall()
        assert [
            (row["client_account_id"], row["tiktok_username"], row["subject_company"])
            for row in rows
        ] == [
            ("acct-a", "renamed@example.test", "武汉速视科技有限公司2"),
            ("acct-c", "15327086817@163.com", "湖北云漫科技有限公司"),
        ]

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
        session["user_type"] = "user"
    page_response = client.get("/tt-users")
    assert page_response.status_code == 200
    assert "TIKTOK用户名" in page_response.get_data(as_text=True)

    users_response = client.get("/api/tt-users")
    assert users_response.status_code == 200
    tt_user = next(item for item in users_response.get_json() if item["username"] == "zhangbiao")
    assert tt_user["tiktok_usernames"] == ["15327086817@163.com", "renamed@example.test"]
    assert tt_user["tiktok_accounts"] == [
        {
            "tiktok_username": "15327086817@163.com",
            "subject_company": "湖北云漫科技有限公司",
        },
        {
            "tiktok_username": "renamed@example.test",
            "subject_company": "武汉速视科技有限公司2",
        },
    ]
    assert tt_user["subject_company"] == "湖北云漫科技有限公司、武汉速视科技有限公司2"

    cleared = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=headers,
        json={"accounts": []},
    )
    assert cleared.status_code == 200
    assert cleared.get_json()["data"]["deleted"] == 2


def test_tt_user_list_supports_search_filter_and_pagination(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        alpha_id, alpha_token = _create_tt_user_with_token(
            username="alpha",
            email="alpha@example.test",
            machine_id="machine-alpha",
        )
        _create_tt_user_with_token(
            username="beta",
            email="beta@example.test",
            machine_id="machine-beta",
        )
        db = manage_app.get_db()
        db.execute(
            "UPDATE tt_users SET full_name = ?, responsible_person = ? WHERE id = ?",
            ("Alpha User", "Alice", alpha_id),
        )
        db.commit()

    snapshot = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=_headers(account="alpha", machine_id="machine-alpha", token=alpha_token),
        json={
            "accounts": [
                {
                    "client_account_id": "acct-alpha",
                    "tiktok_username": "needle@example.test",
                    "subject_company": "湖北云漫科技有限公司",
                }
            ]
        },
    )
    assert snapshot.status_code == 200

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
        session["user_type"] = "user"

    paged_response = client.get("/api/tt-users?page=1&page_size=1")
    assert paged_response.status_code == 200
    paged_data = paged_response.get_json()
    assert paged_data["total"] == 2
    assert paged_data["pages"] == 2
    assert len(paged_data["items"]) == 1

    search_response = client.get("/api/tt-users?page=1&page_size=20&search=needle")
    assert search_response.status_code == 200
    search_data = search_response.get_json()
    assert search_data["total"] == 1
    assert search_data["items"][0]["username"] == "alpha"

    company_response = client.get("/api/tt-users?page=1&page_size=20&subject_company=云漫")
    assert company_response.status_code == 200
    company_data = company_response.get_json()
    assert company_data["total"] == 1
    assert company_data["items"][0]["tiktok_accounts"] == [
        {
            "tiktok_username": "needle@example.test",
            "subject_company": "湖北云漫科技有限公司",
        }
    ]

    owner_response = client.get("/api/tt-users?page=1&page_size=20&responsible_person=Alice")
    assert owner_response.status_code == 200
    owner_data = owner_response.get_json()
    assert owner_data["total"] == 1
    assert owner_data["items"][0]["username"] == "alpha"


def test_tt_account_snapshot_is_scoped_to_authenticated_machine(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        user_id, token_a = _create_tt_user_with_token(machine_id="machine-a")
        token_b = _add_tt_device_token(user_id=user_id, machine_id="machine-b")

    response_a = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token_a),
        json={
            "accounts": [
                {"client_account_id": "acct-a", "tiktok_username": "shared@example.test"},
                {"client_account_id": "acct-a2", "tiktok_username": "only-a@example.test"},
            ]
        },
    )
    response_b = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=_headers(account="zhangbiao", machine_id="machine-b", token=token_b),
        json={
            "accounts": [
                {"client_account_id": "acct-b", "tiktok_username": "SHARED@example.test"},
                {"client_account_id": "acct-b2", "tiktok_username": "only-b@example.test"},
            ]
        },
    )
    assert response_a.status_code == 200
    assert response_b.status_code == 200

    cleared_a = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token_a),
        json={"accounts": []},
    )
    assert cleared_a.status_code == 200
    assert cleared_a.get_json()["data"]["deleted"] == 2

    with manage_app.app.app_context():
        rows = manage_app.get_db().execute(
            """
            SELECT machine_id, tiktok_username
            FROM tt_client_accounts
            ORDER BY machine_id, client_account_id
            """
        ).fetchall()
        assert [(row["machine_id"], row["tiktok_username"]) for row in rows] == [
            ("machine-b", "SHARED@example.test"),
            ("machine-b", "only-b@example.test"),
        ]

    invalid = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=_headers(account="zhangbiao", machine_id="machine-b", token=token_a),
        json={"accounts": []},
    )
    assert invalid.status_code == 401


def test_tt_account_snapshot_rejects_non_object_and_non_string_fields(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        _, token = _create_tt_user_with_token()

    headers = _headers(account="zhangbiao", machine_id="machine-a", token=token)
    for invalid_body in ([], "accounts", 123, None):
        response = client.put(
            "/client-api/tt/accounts/snapshot",
            headers=headers,
            json=invalid_body,
        )
        assert response.status_code == 400
        assert response.get_json()["ok"] is False

    invalid_accounts = [
        {"client_account_id": 123, "tiktok_username": "name-a"},
        {"profile_id": 123, "tiktok_username": "name-b"},
        {"client_account_id": "acct-c", "tiktok_username": 123},
        {
            "client_account_id": None,
            "profile_id": "fallback-must-not-mask-invalid-primary",
            "tiktok_username": "name-d",
        },
    ]
    for invalid_account in invalid_accounts:
        response = client.put(
            "/client-api/tt/accounts/snapshot",
            headers=headers,
            json={"accounts": [invalid_account]},
        )
        assert response.status_code == 400
        assert response.get_json()["ok"] is False

    profile_id_alias = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=headers,
        json={"accounts": [{"profile_id": "profile-a", "tiktok_username": "name-a"}]},
    )
    assert profile_id_alias.status_code == 200


def test_tt_account_logout_removes_same_machine_snapshot(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        user_id, token = _create_tt_user_with_token()

    headers = _headers(account="zhangbiao", machine_id="machine-a", token=token)
    synced = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=headers,
        json={"accounts": [{"client_account_id": "acct-a", "tiktok_username": "name-a"}]},
    )
    assert synced.status_code == 200

    logged_out = client.post(
        "/client-api/tt/account/logout",
        json={"account": "zhangbiao", "machine_id": "machine-a", "token": token},
    )
    assert logged_out.status_code == 200

    stale_snapshot = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=headers,
        json={"accounts": [{"client_account_id": "acct-a", "tiktok_username": "name-a"}]},
    )
    assert stale_snapshot.status_code == 401

    with manage_app.app.app_context():
        db = manage_app.get_db()
        assert db.execute(
            "SELECT COUNT(*) FROM tt_client_accounts WHERE owner_tt_user_id = ? AND machine_id = ?",
            (user_id, "machine-a"),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT revoked_at FROM tt_user_devices WHERE tt_user_id = ? AND machine_id = ?",
            (user_id, "machine-a"),
        ).fetchone()["revoked_at"]


def test_force_login_removes_replaced_machine_snapshot(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        user_id, token = _create_tt_user_with_token(max_devices=1)

    synced = client.put(
        "/client-api/tt/accounts/snapshot",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token),
        json={"accounts": [{"client_account_id": "acct-a", "tiktok_username": "name-a"}]},
    )
    assert synced.status_code == 200

    forced = client.post(
        "/tt/account/login",
        json={
            "account": "zhangbiao",
            "password": "secret123",
            "machine_id": "machine-b",
            "device_name": "replacement",
            "app_name": "TikTok Uploader",
            "app_version": "1.0",
            "force_login": True,
        },
    )
    assert forced.status_code == 200
    assert forced.get_json()["data"]["replaced_device_count"] == 1

    with manage_app.app.app_context():
        db = manage_app.get_db()
        assert db.execute(
            "SELECT COUNT(*) FROM tt_client_accounts WHERE owner_tt_user_id = ? AND machine_id = ?",
            (user_id, "machine-a"),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT revoked_at FROM tt_user_devices WHERE tt_user_id = ? AND machine_id = ?",
            (user_id, "machine-a"),
        ).fetchone()["revoked_at"]


def test_admin_revoke_removes_only_revoked_machine_snapshot(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        user_id, token_a = _create_tt_user_with_token(machine_id="machine-a")
        token_b = _add_tt_device_token(user_id=user_id, machine_id="machine-b")

    for machine_id, token, account_id in (
        ("machine-a", token_a, "acct-a"),
        ("machine-b", token_b, "acct-b"),
    ):
        response = client.put(
            "/client-api/tt/accounts/snapshot",
            headers=_headers(account="zhangbiao", machine_id=machine_id, token=token),
            json={
                "accounts": [
                    {"client_account_id": account_id, "tiktok_username": f"{account_id}-name"}
                ]
            },
        )
        assert response.status_code == 200

    with manage_app.app.app_context():
        device_id = int(
            manage_app.get_db().execute(
                "SELECT id FROM tt_user_devices WHERE tt_user_id = ? AND machine_id = ?",
                (user_id, "machine-a"),
            ).fetchone()["id"]
        )
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
        session["user_type"] = "user"

    revoked = client.post(f"/api/tt-users/{user_id}/devices/{device_id}/revoke")
    assert revoked.status_code == 200

    with manage_app.app.app_context():
        rows = manage_app.get_db().execute(
            """
            SELECT machine_id, client_account_id
            FROM tt_client_accounts
            WHERE owner_tt_user_id = ?
            ORDER BY machine_id
            """,
            (user_id,),
        ).fetchall()
        assert [(row["machine_id"], row["client_account_id"]) for row in rows] == [
            ("machine-b", "acct-b")
        ]
