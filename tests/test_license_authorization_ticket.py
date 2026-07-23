from __future__ import annotations

import datetime

import app as manage_app


def _setup_test_app(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(manage_app, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(manage_app, "DATABASE", str(data_dir / "dramas.db"))
    monkeypatch.setattr(manage_app, "REMOTE_UPLOAD_DIR", str(data_dir / "remote_uploads"))
    monkeypatch.setattr(manage_app, "start_kuaishou_token_refresh_scheduler", lambda: None)
    monkeypatch.setattr(manage_app, "_lookup_ip_region_online", lambda ip: "")
    manage_app.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        LICENSE_SIGNING_KEY="test-license-signing-secret",
    )
    manage_app.init_db()
    return manage_app.app.test_client()


def _create_license() -> None:
    db = manage_app.get_db()
    db.execute(
        """
        INSERT INTO licenses (
            license_key, license_key_masked, status, edition, licensee,
            max_activations, expires_at, notes
        ) VALUES (?, ?, 'active', 'pro', ?, 1, ?, '')
        """,
        (
            "WXA-SIGNED-TEST-0001",
            "WXA-****-0001",
            "signed-licensee",
            (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat(
                timespec="seconds"
            ),
        ),
    )
    db.commit()


def test_license_activate_and_verify_return_signed_ticket(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    with manage_app.app.app_context():
        _create_license()

    request_data = {
        "license_key": "WXA-SIGNED-TEST-0001",
        "machine_id": "license-machine",
        "app_name": "短剧助手",
        "app_version": "0.1.6",
    }
    activate_response = client.post("/license/activate", json=request_data)
    assert activate_response.status_code == 200
    activate_data = activate_response.get_json()["data"]
    activate_claims = manage_app.verify_tt_authorization_ticket(
        activate_data["authorization_ticket"]
    )
    assert activate_claims["subject"] == request_data["license_key"]
    assert activate_claims["machine_id"] == request_data["machine_id"]
    assert activate_claims["token_sha256"] == manage_app.hash_token(
        activate_data["token"]
    )
    assert activate_claims["app_name"] == request_data["app_name"]
    assert activate_claims["app_version"] == request_data["app_version"]
    assert activate_claims["licensee"] == "signed-licensee"

    verify_response = client.post(
        "/license/verify",
        json={**request_data, "token": activate_data["token"]},
    )
    assert verify_response.status_code == 200
    verify_data = verify_response.get_json()["data"]
    verify_claims = manage_app.verify_tt_authorization_ticket(
        verify_data["authorization_ticket"]
    )
    assert verify_claims["subject"] == request_data["license_key"]
    assert verify_claims["token_sha256"] == manage_app.hash_token(
        verify_data["token"]
    )
