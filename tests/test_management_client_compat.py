import io
import zipfile

from test_tt_client_account_snapshot import (
    _create_tt_user_with_token, _headers, _setup_test_app,
)
import app as manage_app


def test_config_backup_and_permissions(tmp_path, monkeypatch):
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        owner, token = _create_tt_user_with_token(username="owner", email="owner@example.test")
        _, other = _create_tt_user_with_token(
            username="other", email="other@example.test", machine_id="machine-b",
        )
        admin = manage_app.get_db().execute("SELECT id FROM users WHERE role='admin'").fetchone()["id"]
    headers = _headers(account="owner", machine_id="machine-a", token=token)
    headers["X-TT-Device-Name"] = "Test PC"
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", '{"version":1,"scope":"config","accounts":[]}')
    endpoint = "/client-api/tt/config"
    assert client.put(endpoint, data=package.getvalue()).status_code == 401
    assert client.put(endpoint, headers=headers, data=b"invalid").status_code == 400
    for expected in (1, 2):
        assert client.put(endpoint, headers=headers, data=package.getvalue()).status_code == 200
        meta = client.get(endpoint + "/meta", headers=headers).get_json()["data"]
        assert meta["revision"] == expected and meta["source_device_name"] == "Test PC"
    downloaded = client.get(endpoint, headers=headers)
    assert downloaded.data == package.getvalue()
    assert downloaded.headers["X-TT-Config-Revision"] == "2"
    assert downloaded.headers["X-TT-Config-Sha256"] == meta["sha256"]
    assert client.get(endpoint, headers=_headers(account="other", machine_id="machine-b", token=other)).status_code == 404
    with manage_app.app.app_context():
        row = manage_app.get_db().execute("SELECT encrypted_payload FROM account_config_backups").fetchone()
        assert row[0] != package.getvalue() and b"manifest.json" not in row[0]
    feature_headers = {"Authorization": "Bearer " + token,
                       "X-Xinge-Account": "owner", "X-Xinge-Machine-ID": "machine-a"}
    features = "/client-api/account/features"
    assert client.get(features, headers=feature_headers).get_json()["disabled_modules"] == []
    with client.session_transaction() as web:
        web.update(user_id=admin, role="admin", username="admin")
    admin_base = f"/api/managed-accounts/tt/{owner}"
    result = client.get(admin_base + "/cloud-config").get_json()
    assert result["exists"] and result["revision"] == 2 and "encrypted_payload" not in result
    assert client.put(admin_base + "/features", json={"modules": ["kuaishou"], "web": []}).status_code == 200
    assert client.get(features, headers=feature_headers).get_json()["disabled_modules"] == ["kuaishou"]

    assert client.put(admin_base + "/features", json={"modules": [], "web": ["tt_dramas"]}).status_code == 200
    with client.session_transaction() as web:
        web.update(user_id=owner, user_type="tt", role="user", username="owner")
    assert client.get("/dramas/tt").status_code == 403
    assert client.get("/api/platform-dramas?platform=tt").status_code == 403
    assert client.get(admin_base + "/cloud-config").status_code == 403
