import base64
import hashlib
import hmac

import app as manage_app
from client_update import build_feed_url, build_presigned_put_url, safe_release_file_name


def _setup(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(manage_app, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(manage_app, "DATABASE", str(data_dir / "dramas.db"))
    monkeypatch.setattr(manage_app, "REMOTE_UPLOAD_DIR", str(data_dir / "remote_uploads"))
    monkeypatch.setattr(manage_app, "start_kuaishou_token_refresh_scheduler", lambda: None)
    manage_app.app.config.update(TESTING=True, SECRET_KEY="test-secret", LICENSE_SIGNING_KEY="test-secret")
    manage_app.init_db()
    client = manage_app.app.test_client()
    client.post("/login", data={"username": "admin", "password": "admin123"})
    return client


def test_presigned_put_uses_oss_signature_v1():
    expires_at = 1700000000
    url = build_presigned_put_url(
        endpoint="https://oss-cn-beijing.aliyuncs.com",
        bucket="kuaishou2",
        access_key_id="LTAItest",
        access_key_secret="secret",
        object_key="client-releases/tiktok/releases.win.json",
        expires_at=expires_at,
    )
    string_to_sign = "PUT\n\napplication/octet-stream\n1700000000\n/kuaishou2/client-releases/tiktok/releases.win.json"
    signature = base64.b64encode(hmac.new(b"secret", string_to_sign.encode(), hashlib.sha1).digest()).decode()

    assert url.startswith("https://kuaishou2.oss-cn-beijing.aliyuncs.com/client-releases/tiktok/releases.win.json?")
    assert "OSSAccessKeyId=LTAItest" in url
    assert f"Expires={expires_at}" in url
    encoded = signature.replace("+", "%2B").replace("/", "%2F").replace("=", "%3D")
    assert f"Signature={encoded}" in url


def test_feed_url_joins_public_base_and_prefix():
    assert build_feed_url(
        "https://kuaishou2.oss-cn-beijing.aliyuncs.com/",
        "/client-releases/tiktok/",
    ) == "https://kuaishou2.oss-cn-beijing.aliyuncs.com/client-releases/tiktok/"


def test_release_file_name_rejects_paths():
    try:
        safe_release_file_name("../weixin-channel/images/a.png")
    except ValueError as exc:
        assert "路径" in str(exc)
    else:
        raise AssertionError("path should be rejected")


def test_settings_keep_secret_and_current_release_is_public(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    saved = client.put(
        "/api/client-update/settings",
        json={
            "endpoint": "oss-cn-beijing.aliyuncs.com",
            "bucket": "kuaishou2",
            "accessKeyId": "LTAItest",
            "accessKeySecret": "secret-value",
            "publicBaseUrl": "https://kuaishou2.oss-cn-beijing.aliyuncs.com",
            "objectPrefix": "client-releases/tiktok",
        },
    )
    assert saved.status_code == 200
    body = saved.get_json()
    assert body["hasSecret"] is True
    assert "secret-value" not in saved.get_data(as_text=True)
    assert body["feedUrl"] == "https://kuaishou2.oss-cn-beijing.aliyuncs.com/client-releases/tiktok/"

    kept = client.put(
        "/api/client-update/settings",
        json={
            "endpoint": "oss-cn-beijing.aliyuncs.com",
            "bucket": "kuaishou2",
            "accessKeyId": "LTAItest",
            "accessKeySecret": "",
            "publicBaseUrl": "https://kuaishou2.oss-cn-beijing.aliyuncs.com",
            "objectPrefix": "client-releases/tiktok",
        },
    )
    assert kept.status_code == 200

    ticket = client.post("/api/client-update/upload-ticket", json={"fileName": "releases.win.json"})
    assert ticket.status_code == 200
    assert ticket.get_json()["contentType"] == "application/octet-stream"
    assert "secret-value" not in ticket.get_data(as_text=True)
    assert ticket.get_json()["publicUrl"].endswith("/releases.win.json")

    rejected = client.post("/api/client-update/upload-ticket", json={"fileName": "../secrets.txt"})
    assert rejected.status_code == 400

    published = client.post(
        "/api/client-update/releases",
        json={"version": "3.24.0", "notes": "修复上传日志", "setupFile": "TikTok.ShortDramaAssistant-Setup.exe"},
    )
    assert published.status_code == 200

    current = client.get("/api/client-update/current")
    assert current.status_code == 200
    payload = current.get_json()
    assert payload["available"] is True
    assert payload["version"] == "3.24.0"
    assert payload["feedUrl"].endswith("/client-releases/tiktok/")
    assert payload["setupUrl"].endswith("/TikTok.ShortDramaAssistant-Setup.exe")
    assert "secret-value" not in current.get_data(as_text=True)
