"""TikTok 短剧助手客户端升级。

OSS 密钥只保存在管理系统。安装包上传到对象前缀 client-releases/tiktok，
客户端读取公网目录后自行从 OSS 下载。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import sqlite3
import time
from urllib import parse

from flask import jsonify, render_template, request

DEFAULT_OBJECT_PREFIX = "client-releases/tiktok"
_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_FILE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

_SETTING_ENDPOINT = "client_update_oss_endpoint"
_SETTING_BUCKET = "client_update_oss_bucket"
_SETTING_ACCESS_KEY_ID = "client_update_oss_access_key_id"
_SETTING_ACCESS_KEY_SECRET = "client_update_oss_access_key_secret"
_SETTING_PUBLIC_BASE_URL = "client_update_oss_public_base_url"
_SETTING_OBJECT_PREFIX = "client_update_oss_object_prefix"


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS client_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            setup_file TEXT NOT NULL DEFAULT '',
            is_current INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )


def normalize_endpoint(value: object) -> str:
    text = str(value or "").strip()
    for prefix in ("https://", "http://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    return text.strip().strip("/")


def normalize_public_base_url(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    return text


def normalize_object_prefix(value: object) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        return DEFAULT_OBJECT_PREFIX
    if ".." in text or not _PREFIX_PATTERN.fullmatch(text):
        raise ValueError("对象前缀只能包含字母、数字、点、下划线、斜杠和横线。")
    return text


def safe_release_file_name(value: object) -> str:
    name = str(value or "").strip().replace("\\", "/")
    if "/" in name or name in {"", ".", ".."}:
        raise ValueError("文件名不能包含路径。")
    if not _FILE_PATTERN.fullmatch(name):
        raise ValueError("文件名只能包含字母、数字、点、下划线和横线。")
    lowered = name.lower()
    if lowered == "releases.win.json" or lowered.endswith(".nupkg") or lowered.endswith(".exe"):
        return name
    raise ValueError("只接受 releases.win.json、nupkg 和安装包 exe。")


def build_feed_url(public_base_url: str, object_prefix: str) -> str:
    base = normalize_public_base_url(public_base_url).rstrip("/")
    prefix = normalize_object_prefix(object_prefix).strip("/")
    return f"{base}/{prefix}/"


def build_presigned_put_url(
    *,
    endpoint: str,
    bucket: str,
    access_key_id: str,
    access_key_secret: str,
    object_key: str,
    expires_at: int,
    content_type: str = "application/octet-stream",
) -> str:
    host = normalize_endpoint(endpoint)
    canonical = f"/{bucket}/{object_key}"
    string_to_sign = "\n".join(["PUT", "", content_type, str(expires_at), canonical])
    digest = hmac.new(access_key_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    signature = base64.b64encode(digest).decode("ascii")
    query = parse.urlencode(
        {
            "OSSAccessKeyId": access_key_id,
            "Expires": str(expires_at),
            "Signature": signature,
        }
    )
    quoted_key = parse.quote(object_key, safe="/")
    return f"https://{bucket}.{host}/{quoted_key}?{query}"


def object_key_for(prefix: str, file_name: str) -> str:
    return f"{normalize_object_prefix(prefix).strip('/')}/{safe_release_file_name(file_name)}"


def register_routes(flask_app) -> None:
    @flask_app.route("/client-update", methods=["GET"])
    def client_update_page():
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _page():
            return render_template("client_update.html")

        return _page()

    @flask_app.route("/api/client-update/settings", methods=["GET"])
    def get_client_update_settings():
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _get():
            return jsonify(_public_settings(manage_app.get_db(), manage_app.mask_secret_value))

        return _get()

    @flask_app.route("/api/client-update/settings", methods=["PUT"])
    def put_client_update_settings():
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _put():
            data = request.get_json(silent=True) or {}
            try:
                saved = _save_settings(manage_app, data)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify(saved)

        return _put()

    @flask_app.route("/api/client-update/upload-ticket", methods=["POST"])
    def create_client_update_upload_ticket():
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _ticket():
            data = request.get_json(silent=True) or {}
            try:
                ticket = _build_upload_ticket(manage_app.get_db(), data)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify(ticket)

        return _ticket()

    @flask_app.route("/api/client-update/releases", methods=["GET"])
    def list_client_update_releases():
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _list():
            db = manage_app.get_db()
            settings = _load_settings(db)
            return jsonify({"releases": [_release_row(db, row, settings) for row in _list_rows(db)]})

        return _list()

    @flask_app.route("/api/client-update/releases", methods=["POST"])
    def publish_client_update_release():
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _publish():
            data = request.get_json(silent=True) or {}
            try:
                release = _publish_release(manage_app, data)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify(release)

        return _publish()

    @flask_app.route("/api/client-update/releases/<int:release_id>/current", methods=["POST"])
    def set_current_client_update_release(release_id: int):
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _current():
            try:
                release = _mark_current(manage_app, release_id)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 404
            return jsonify(release)

        return _current()

    @flask_app.route("/api/client-update/current", methods=["GET"])
    def client_update_current():
        import app as manage_app

        db = manage_app.get_db()
        settings = _load_settings(db)
        row = _current_row(db)
        if row is None or not _settings_ready(settings):
            return jsonify({"available": False})
        return jsonify(_release_payload(row, settings, available=True))


def _save_settings(manage_app, data: dict) -> dict:
    endpoint = normalize_endpoint(data.get("endpoint"))
    bucket = str(data.get("bucket") or "").strip()
    access_key_id = str(data.get("accessKeyId") or "").strip()
    secret = str(data.get("accessKeySecret") or "").strip()
    public_base = normalize_public_base_url(data.get("publicBaseUrl"))
    prefix = normalize_object_prefix(data.get("objectPrefix"))
    if not endpoint or not bucket or not access_key_id or not public_base:
        raise ValueError("Endpoint、Bucket、AccessKeyId 和公网 URL 前缀都要填写。")
    if ".." in bucket or "/" in bucket:
        raise ValueError("Bucket 不正确。")

    db = manage_app.get_db()
    existing = _load_settings(db)
    if not secret:
        secret = existing["access_key_secret"]
    if not secret:
        raise ValueError("请填写 AccessKeySecret。")

    updated_by = manage_app.session.get("user_id")
    manage_app.set_app_setting(_SETTING_ENDPOINT, endpoint, is_secret=False, updated_by=updated_by)
    manage_app.set_app_setting(_SETTING_BUCKET, bucket, is_secret=False, updated_by=updated_by)
    manage_app.set_app_setting(_SETTING_ACCESS_KEY_ID, access_key_id, is_secret=False, updated_by=updated_by)
    manage_app.set_app_setting(_SETTING_ACCESS_KEY_SECRET, secret, is_secret=True, updated_by=updated_by)
    manage_app.set_app_setting(_SETTING_PUBLIC_BASE_URL, public_base, is_secret=False, updated_by=updated_by)
    manage_app.set_app_setting(_SETTING_OBJECT_PREFIX, prefix, is_secret=False, updated_by=updated_by)
    db.commit()
    return _public_settings(db, manage_app.mask_secret_value)


def _build_upload_ticket(db: sqlite3.Connection, data: dict) -> dict:
    settings = _require_ready_settings(db)
    file_name = safe_release_file_name(data.get("fileName"))
    object_key = object_key_for(settings["object_prefix"], file_name)
    expires_at = int(time.time()) + 2 * 60 * 60
    url = build_presigned_put_url(
        endpoint=settings["endpoint"],
        bucket=settings["bucket"],
        access_key_id=settings["access_key_id"],
        access_key_secret=settings["access_key_secret"],
        object_key=object_key,
        expires_at=expires_at,
    )
    feed = build_feed_url(settings["public_base_url"], settings["object_prefix"])
    return {
        "url": url,
        "method": "PUT",
        "contentType": "application/octet-stream",
        "objectKey": object_key,
        "publicUrl": f"{feed}{parse.quote(file_name)}",
        "expiresAt": expires_at,
    }


def _publish_release(manage_app, data: dict) -> dict:
    version = str(data.get("version") or "").strip()
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError("版本号要用 3.24.0 这种格式。")
    notes = str(data.get("notes") or "").strip()
    setup_file = str(data.get("setupFile") or "").strip()
    if setup_file:
        setup_file = safe_release_file_name(setup_file)
        if not setup_file.lower().endswith(".exe"):
            raise ValueError("安装包必须是 exe。")
    db = manage_app.get_db()
    if not _settings_ready(_load_settings(db)):
        raise ValueError("请先保存阿里云 OSS 配置。")
    created_at = manage_app.now_iso()
    db.execute("UPDATE client_releases SET is_current = 0 WHERE is_current = 1")
    db.execute(
        """
        INSERT INTO client_releases (version, notes, setup_file, is_current, created_at)
        VALUES (?, ?, ?, 1, ?)
        """,
        (version, notes, setup_file, created_at),
    )
    db.commit()
    row = _current_row(db)
    return _release_row(db, row, _load_settings(db))


def _mark_current(manage_app, release_id: int) -> dict:
    db = manage_app.get_db()
    row = db.execute("SELECT * FROM client_releases WHERE id = ?", (release_id,)).fetchone()
    if row is None:
        raise ValueError("版本不存在。")
    db.execute("UPDATE client_releases SET is_current = 0 WHERE is_current = 1")
    db.execute("UPDATE client_releases SET is_current = 1 WHERE id = ?", (release_id,))
    db.commit()
    current = _current_row(db)
    return _release_row(db, current, _load_settings(db))


def _load_settings(db: sqlite3.Connection) -> dict:
    rows = db.execute(
        "SELECT setting_key, setting_value FROM app_settings WHERE setting_key LIKE 'client_update_oss_%'"
    ).fetchall()
    values = {row["setting_key"]: str(row["setting_value"] or "").strip() for row in rows}
    prefix = values.get(_SETTING_OBJECT_PREFIX) or DEFAULT_OBJECT_PREFIX
    return {
        "endpoint": values.get(_SETTING_ENDPOINT, ""),
        "bucket": values.get(_SETTING_BUCKET, ""),
        "access_key_id": values.get(_SETTING_ACCESS_KEY_ID, ""),
        "access_key_secret": values.get(_SETTING_ACCESS_KEY_SECRET, ""),
        "public_base_url": values.get(_SETTING_PUBLIC_BASE_URL, ""),
        "object_prefix": prefix,
    }


def _settings_ready(settings: dict) -> bool:
    return all(
        str(settings.get(key) or "").strip()
        for key in ("endpoint", "bucket", "access_key_id", "access_key_secret", "public_base_url", "object_prefix")
    )


def _require_ready_settings(db: sqlite3.Connection) -> dict:
    settings = _load_settings(db)
    if not _settings_ready(settings):
        raise ValueError("请先保存阿里云 OSS 配置。")
    return settings


def _public_settings(db: sqlite3.Connection, mask_secret) -> dict:
    settings = _load_settings(db)
    secret = settings["access_key_secret"]
    feed = ""
    if settings["public_base_url"] and settings["object_prefix"]:
        try:
            feed = build_feed_url(settings["public_base_url"], settings["object_prefix"])
        except ValueError:
            feed = ""
    return {
        "endpoint": settings["endpoint"],
        "bucket": settings["bucket"],
        "accessKeyId": settings["access_key_id"],
        "hasSecret": bool(secret),
        "accessKeySecretMasked": mask_secret(secret) if secret else "",
        "publicBaseUrl": settings["public_base_url"],
        "objectPrefix": settings["object_prefix"],
        "feedUrl": feed,
        "ready": _settings_ready(settings),
    }


def _list_rows(db: sqlite3.Connection):
    return db.execute("SELECT * FROM client_releases ORDER BY id DESC").fetchall()


def _current_row(db: sqlite3.Connection):
    return db.execute(
        "SELECT * FROM client_releases WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _release_row(db: sqlite3.Connection, row, settings: dict) -> dict:
    del db
    return _release_payload(row, settings, available=True)


def _release_payload(row, settings: dict, *, available: bool) -> dict:
    feed = ""
    setup_url = ""
    if _settings_ready(settings):
        feed = build_feed_url(settings["public_base_url"], settings["object_prefix"])
        setup_file = str(row["setup_file"] or "").strip()
        if setup_file:
            setup_url = f"{feed}{parse.quote(setup_file)}"
    return {
        "available": available,
        "id": row["id"],
        "version": row["version"],
        "notes": row["notes"] or "",
        "setupFile": row["setup_file"] or "",
        "isCurrent": bool(row["is_current"]),
        "createdAt": row["created_at"],
        "feedUrl": feed,
        "setupUrl": setup_url,
    }
