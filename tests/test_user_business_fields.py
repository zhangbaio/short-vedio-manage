from __future__ import annotations

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
        LICENSE_SIGNING_KEY="test-secret",
    )
    manage_app.init_db()
    return manage_app.app.test_client()


def _login_admin(client) -> None:
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "admin"
        session["role"] = "admin"


def test_user_business_fields_create_update_filter_and_paginate(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    _login_admin(client)

    create_response = client.post(
        "/api/users",
        json={
            "username": "business_user",
            "full_name": "Business User",
            "email": "business@example.test",
            "password": "secret123",
            "role": "user",
            "status": "active",
            "edition": "pro",
            "max_devices": 1,
            "subject_company": "Alpha Media",
            "responsible_person": "Alice",
            "video_channel_name": "Alpha Video",
        },
    )
    assert create_response.status_code == 201

    list_response = client.get("/api/users?page=1&page_size=10&responsible_person=Alice")
    assert list_response.status_code == 200
    list_data = list_response.get_json()
    assert list_data["total"] == 1
    assert list_data["page"] == 1
    assert list_data["pages"] == 1
    item = list_data["items"][0]
    assert item["subject_company"] == "Alpha Media"
    assert item["responsible_person"] == "Alice"
    assert item["video_channel_name"] == "Alpha Video"

    update_response = client.put(
        f"/api/users/{item['id']}",
        json={
            "username": "business_user",
            "full_name": "Business User",
            "email": "business@example.test",
            "role": "user",
            "status": "active",
            "edition": "pro",
            "max_devices": 2,
            "subject_company": "Beta Media",
            "responsible_person": "Bob",
            "video_channel_name": "Beta Video",
        },
    )
    assert update_response.status_code == 200

    filtered_response = client.get(
        "/api/users?page=1&page_size=5&search=Beta&subject_company=Beta&video_channel_name=Video"
    )
    assert filtered_response.status_code == 200
    filtered_data = filtered_response.get_json()
    assert filtered_data["total"] == 1
    assert filtered_data["items"][0]["responsible_person"] == "Bob"

    legacy_response = client.get("/api/users")
    assert legacy_response.status_code == 200
    legacy_data = legacy_response.get_json()
    assert isinstance(legacy_data, list)
    legacy_item = next(user for user in legacy_data if user["username"] == "business_user")
    assert legacy_item["subject_company"] == "Beta Media"


def test_user_management_page_shows_business_field_controls(tmp_path, monkeypatch) -> None:
    client = _setup_test_app(tmp_path, monkeypatch)
    _login_admin(client)

    response = client.get("/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "主体公司" in html
    assert "负责人" in html
    assert "视频号名称" in html
