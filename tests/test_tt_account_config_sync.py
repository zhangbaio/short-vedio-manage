import base64
import datetime
import hashlib

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
        TT_CONFIG_ENCRYPTION_KEY="sync-test-key",
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
    user_id = int(db.execute("SELECT id FROM tt_users WHERE username = ?", (username,)).fetchone()["id"])
    db.commit()
    return user_id, _add_tt_device_token(user_id=user_id, machine_id=machine_id)


def _headers(*, account: str, machine_id: str, token: str) -> dict[str, str]:
    return {
        "X-TT-Account": account,
        "X-TT-Machine-Id": machine_id,
        "X-TT-Token": token,
    }


def _payload(**overrides):
    body = {
        "base_revision": 0,
        "tiktok_username": "1544722162@qq.com",
        "config": {
            "name": "账号1",
            "tiktokContractId": "contract-1",
            "xingeRemoteDispatchEnabled": True,
            "tiktokProxyHost": "127.0.0.1",
            "profileDir": "C:\\should-not-sync",
            "tiktokStorageStatePath": "C:\\auth.json",
            "lastWorkspace": "E:\\workspace",
            "tiktokLoginPassword": "should-stay-secret",
        },
        "secrets": {
            "tiktokLoginPassword": "secret-password",
            "tiktokProxyPassword": "proxy-secret",
        },
    }
    body.update(overrides)
    return body


def _put(client, token, account_id="acct-a", machine_id="machine-a", **overrides):
    return client.put(
        f"/client-api/tt/account-configs/{account_id}",
        headers=_headers(account="zhangbiao", machine_id=machine_id, token=token),
        json=_payload(**overrides),
    )


def test_account_config_roundtrip_keeps_secrets_encrypted(tmp_path, monkeypatch):
    monkeypatch.setenv("TT_CONFIG_ENCRYPTION_KEY", "sync-test-key")
    client = _setup_test_app(tmp_path, monkeypatch)
    manage_app.app.config["TT_CONFIG_ENCRYPTION_KEY"] = "sync-test-key"
    with manage_app.app.app_context():
        _, token = _create_tt_user_with_token()

    login_state = b'{"cookies":[{"name":"session","value":"abc"}]}'
    created = _put(
        client,
        token,
        login_state_base64=base64.b64encode(login_state).decode("ascii"),
        seal_base64=base64.b64encode(b"seal-bytes").decode("ascii"),
        seal_filename="company.png",
    )
    assert created.status_code == 200
    assert created.get_json()["data"]["revision"] == 1

    listed = client.get(
        "/client-api/tt/account-configs",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token),
    )
    assert listed.status_code == 200
    account = listed.get_json()["data"]["accounts"][0]
    assert account["config"]["tiktokContractId"] == "contract-1"
    assert "profileDir" not in account["config"]
    assert "lastWorkspace" not in account["config"]
    assert "tiktokLoginPassword" not in account["config"]
    assert account["secrets"]["tiktokLoginPassword"] == "secret-password"
    assert account["login_state_sha256"] == hashlib.sha256(login_state).hexdigest()

    downloaded = client.get(
        "/client-api/tt/account-configs/acct-a/login-state",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token),
    )
    assert downloaded.status_code == 200
    assert downloaded.data == login_state

    with manage_app.app.app_context():
        row = manage_app.get_db().execute("SELECT * FROM tt_account_configs").fetchone()
        stored = " ".join(
            str(row[key] or "")
            for key in ("config_json", "secrets_ciphertext", "login_state_ciphertext", "seal_ciphertext")
        )
        assert "secret-password" not in stored
        assert "proxy-secret" not in stored
        assert b"seal-bytes" not in stored.encode("utf-8")
        assert "session" not in row["login_state_ciphertext"]


def test_same_username_on_another_machine_adopts_canonical_account(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    manage_app.app.config["TT_CONFIG_ENCRYPTION_KEY"] = "sync-test-key"
    with manage_app.app.app_context():
        user_id, token_a = _create_tt_user_with_token(machine_id="machine-a")
        token_b = _add_tt_device_token(user_id=user_id, machine_id="machine-b")

    assert _put(client, token_a).status_code == 200
    conflict = _put(
        client,
        token_b,
        account_id="acct-b",
        machine_id="machine-b",
        tiktok_username="1544722162@QQ.COM",
        config={"name": "另一台电脑", "tiktokContractId": "contract-2"},
        secrets={"tiktokLoginPassword": "from-b", "tiktokProxyPassword": ""},
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["data"]["client_account_id"] == "acct-a"
    assert conflict.get_json()["data"]["secrets"]["tiktokLoginPassword"] == "secret-password"

    adopted = _put(
        client,
        token_b,
        account_id="acct-b",
        machine_id="machine-b",
        base_revision=1,
        tiktok_username="1544722162@QQ.COM",
        config={"name": "另一台电脑", "tiktokContractId": "contract-2"},
        secrets={"tiktokLoginPassword": "from-b", "tiktokProxyPassword": ""},
    )
    assert adopted.status_code == 200
    assert adopted.get_json()["data"]["client_account_id"] == "acct-a"
    assert adopted.get_json()["data"]["adopted"] is True

    listed = client.get(
        "/client-api/tt/account-configs",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token_a),
    )
    accounts = listed.get_json()["data"]["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["revision"] == 2
    assert accounts[0]["config"]["tiktokContractId"] == "contract-2"
    assert accounts[0]["updated_by_machine"] == "machine-b"


def test_stale_revision_conflicts_and_identical_put_does_not_bump(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    manage_app.app.config["TT_CONFIG_ENCRYPTION_KEY"] = "sync-test-key"
    with manage_app.app.app_context():
        _, token = _create_tt_user_with_token()

    assert _put(client, token).status_code == 200
    changed = _put(
        client,
        token,
        base_revision=1,
        config={"name": "账号1", "tiktokContractId": "contract-2"},
    )
    assert changed.status_code == 200
    assert changed.get_json()["data"]["revision"] == 2

    stale = _put(
        client,
        token,
        base_revision=1,
        config={"name": "账号1", "tiktokContractId": "contract-3"},
    )
    assert stale.status_code == 409
    assert stale.get_json()["code"] == "revision_conflict"

    repeated = _put(
        client,
        token,
        base_revision=1,
        config={"name": "账号1", "tiktokContractId": "contract-2"},
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["data"]["revision"] == 2


def test_delete_hides_secrets_and_revoked_device_cannot_read(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    manage_app.app.config["TT_CONFIG_ENCRYPTION_KEY"] = "sync-test-key"
    with manage_app.app.app_context():
        user_id, token = _create_tt_user_with_token()
        device_id = int(
            manage_app.get_db().execute(
                "SELECT id FROM tt_user_devices WHERE tt_user_id = ?",
                (user_id,),
            ).fetchone()["id"]
        )

    assert _put(
        client,
        token,
        login_state_base64=base64.b64encode(b"cookie").decode("ascii"),
    ).status_code == 200
    deleted = client.delete(
        "/client-api/tt/account-configs/acct-a",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token),
    )
    assert deleted.status_code == 200
    listed = client.get(
        "/client-api/tt/account-configs",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token),
    )
    account = listed.get_json()["data"]["accounts"][0]
    assert account["deleted"] is True
    assert account["secrets"] == {}
    missing = client.get(
        "/client-api/tt/account-configs/acct-a/login-state",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token),
    )
    assert missing.status_code == 404

    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"
        session["user_type"] = "user"
    admin_list = client.get(f"/api/tt-users/{user_id}/account-configs")
    assert admin_list.status_code == 200
    item = admin_list.get_json()[0]
    assert item["deleted"] is True
    assert "secrets" not in item
    assert "config" not in item

    revoked = client.post(f"/api/tt-users/{user_id}/devices/{device_id}/revoke")
    assert revoked.status_code == 200
    denied = client.get(
        "/client-api/tt/account-configs",
        headers=_headers(account="zhangbiao", machine_id="machine-a", token=token),
    )
    assert denied.status_code == 401
