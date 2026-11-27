"""Client features and account-scoped cloud configuration for the management server."""
import hashlib
import hmac
import io
import json
import os
import posixpath
import sqlite3
import zipfile
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import abort, jsonify, request, session
from werkzeug.exceptions import RequestEntityTooLarge

MAX_PACKAGE = 64 << 20
MODULES = dict(download="下载", edit="剪辑", tiktok="TikTok", kuaishou="快手", weixin="视频号", analytics="统计")
WEB = {
    "video_channel_dramas": "视频号短剧管理", "kuaishou_dramas": "快手短剧管理",
    "tt_dramas": "TT 短剧管理", "upload_records": "上传记录", "hongguo": "红果短剧",
}
TABLES = {"general": "users", "tt": "tt_users", "ks": "ks_users"}


def init_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS account_feature_restrictions (
          scope TEXT NOT NULL, owner_id INTEGER NOT NULL, disabled_modules TEXT NOT NULL,
          disabled_web TEXT NOT NULL, updated_by INTEGER NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY (scope, owner_id)
        );
        CREATE TABLE IF NOT EXISTS account_config_backups (
          scope TEXT NOT NULL, owner_id INTEGER NOT NULL, revision INTEGER NOT NULL,
          updated_at TEXT NOT NULL, source_machine_id TEXT NOT NULL, source_device_name TEXT NOT NULL,
          sha256 TEXT NOT NULL, summary TEXT NOT NULL, encrypted_payload BLOB NOT NULL,
          PRIMARY KEY (scope, owner_id)
        );
    """)


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _manifest(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set()
        total = 0
        for entry in archive.infolist():
            filename = entry.filename.rstrip("/")
            if (not filename or filename == ".." or filename.startswith(("/", "../"))
                    or posixpath.normpath(filename) != filename or set(filename) & {":", chr(92)}
                    or filename.casefold() in names or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("配置包目录无效")
            names.add(filename.casefold())
            total += entry.file_size
            if total > 256 << 20:
                raise ValueError("配置包解压后过大")
        with archive.open("manifest.json") as stream:
            raw = stream.read((1 << 20) + 1)
        if len(raw) > 1 << 20:
            raise ValueError("配置清单过大")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("scope") != "config":
        raise ValueError("仅支持客户端配置包")
    accounts = value.get("accounts")
    if not isinstance(accounts, list) or not all(isinstance(account, dict) for account in accounts):
        raise ValueError("账号清单无效")
    return {
        "account_count": len(accounts),
        "has_app_settings": bool(value.get("hasAppSettings") or value.get("hasAppDatabase")),
        "login_state_count": sum(bool(item.get("hasLoginState")) for item in accounts),
        "seal_count": sum(bool(item.get("hasSeal")) for item in accounts),
    }


def register(app, manage):
    def encryption():
        secret = app.config.get("LICENSE_SIGNING_KEY")
        if not secret:
            raise ValueError("配置备份密钥不可用")
        key = hmac.digest(str(secret).encode(), b"short-vedio-manage/config-backup/v1", "sha256")
        return AESGCM(key)

    def client_identity(account, machine, token):
        if not machine or not token:
            return None
        db = manage.get_db()
        if row := manage._authenticate_tt_account_from_request(db, (account, machine, token)):
            return "tt", row["id"]
        row, _, error = manage.resolve_user_from_account_token_payload({
            "account": account, "machine_id": machine, "token": token,
        })
        if not error:
            return "general", row["id"]
        from ks_account_auth import verify_ks_account_token
        try:
            payload = verify_ks_account_token(token)
        except ValueError:
            return None
        if payload.get("machine_id") != machine:
            return None
        row = db.execute("""
            SELECT u.* FROM ks_users u JOIN ks_user_devices d ON d.ks_user_id=u.id
            WHERE u.id=? AND d.machine_id=? AND d.token_hash=? AND COALESCE(d.revoked_at,'')=''
        """, (payload.get("ks_user_id"), machine, manage.hash_token(token))).fetchone()
        if row and manage.ensure_account_can_login(row)[0] and (not account or account in (row["username"], row["email"])):
            return "ks", row["id"]
        return None

    def restrictions(scope, owner):
        row = manage.get_db().execute(
            "SELECT disabled_modules,disabled_web FROM account_feature_restrictions WHERE scope=? AND owner_id=?",
            (scope, owner),
        ).fetchone()
        return (json.loads(row[0]), json.loads(row[1])) if row else ([], [])

    @app.get("/client-api/account/features")
    def client_features():
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        identity = client_identity(
            request.headers.get("X-Xinge-Account", "").strip(),
            request.headers.get("X-Xinge-Machine-ID", "").strip(),
            token.strip() if scheme.lower() == "bearer" else "",
        )
        if not identity:
            return jsonify(ok=False, message="软件账号或设备登录状态无效"), 401
        return jsonify(ok=True, disabled_modules=restrictions(*identity)[0]), 200, {"Cache-Control": "no-store"}

    @app.route("/client-api/tt/config", methods=["GET", "PUT"])
    @app.get("/client-api/tt/config/meta")
    def cloud_config():
        machine = request.headers.get("X-TT-Machine-Id", "").strip()
        identity = client_identity(request.headers.get("X-TT-Account", "").strip(), machine,
                                   request.headers.get("X-TT-Token", "").strip())
        if not identity or identity[0] not in ("general", "tt"):
            return jsonify(ok=False, message="软件账号或设备登录状态无效"), 401
        db = manage.get_db()
        associated = f"{identity[0]}:{identity[1]}".encode()
        if request.method == "PUT":
            if request.content_length is not None and request.content_length > MAX_PACKAGE:
                return jsonify(ok=False, message="配置包不得超过 64 MiB"), 413
            try:
                data = request.stream.read(MAX_PACKAGE + 1)
            except RequestEntityTooLarge:
                return jsonify(ok=False, message="配置包不得超过 64 MiB"), 413
            if len(data) > MAX_PACKAGE:
                return jsonify(ok=False, message="配置包不得超过 64 MiB"), 413
            try:
                summary = _manifest(data)
            except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
                return jsonify(ok=False, message="配置包格式无效"), 400
            try:
                nonce = os.urandom(12)
                encrypted = nonce + encryption().encrypt(nonce, data, associated)
                db.execute("""
                  INSERT INTO account_config_backups VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(scope,owner_id) DO UPDATE SET revision=revision+1,
                    updated_at=excluded.updated_at, source_machine_id=excluded.source_machine_id,
                    source_device_name=excluded.source_device_name, sha256=excluded.sha256,
                    summary=excluded.summary, encrypted_payload=excluded.encrypted_payload
                """, (*identity, _utc(), machine, request.headers.get("X-TT-Device-Name", "").strip()[:200],
                      hashlib.sha256(data).hexdigest(), json.dumps(summary), encrypted))
                db.commit()
            except (ValueError, sqlite3.Error):
                db.rollback()
                return jsonify(ok=False, message="云端备份暂不可用"), 503
            return jsonify(ok=True), 200, {"Cache-Control": "no-store"}

        meta_only = request.path.endswith("/meta")
        columns = "revision, updated_at, source_machine_id, source_device_name, sha256, summary"
        if not meta_only:
            columns += ", encrypted_payload"
        row = db.execute(f"SELECT {columns} FROM account_config_backups WHERE scope=? AND owner_id=?", identity).fetchone()
        if not row:
            if meta_only:
                return jsonify(ok=True, data=None), 200, {"Cache-Control": "no-store"}
            return jsonify(ok=False, message="云端还没有配置"), 404
        if meta_only:
            meta = dict(row)
            meta["summary"] = json.loads(meta["summary"])
            return jsonify(ok=True, data=meta), 200, {"Cache-Control": "no-store"}
        encrypted = row["encrypted_payload"]
        try:
            data = encryption().decrypt(encrypted[:12], encrypted[12:], associated)
        except (InvalidTag, ValueError):
            return jsonify(ok=False, message="读取云端配置失败"), 503
        return data, 200, {
            "Content-Type": "application/zip", "Cache-Control": "no-store",
            "X-TT-Config-Revision": str(row["revision"]), "X-TT-Config-Sha256": row["sha256"],
        }

    @app.route("/api/managed-accounts/<scope>/<int:owner>/features", methods=["GET", "PUT"])
    @manage.admin_required
    def account_features(scope, owner):
        db = manage.get_db()
        if scope not in TABLES or not db.execute(f"SELECT 1 FROM {TABLES[scope]} WHERE id=?", (owner,)).fetchone():
            return jsonify(error="账号不存在"), 404
        web_options = WEB if scope in ("general", "tt") else {}
        if request.method == "PUT":
            value = request.get_json(silent=True)
            if not isinstance(value, dict):
                return jsonify(error="权限配置无效"), 400
            modules, web = value.get("modules"), value.get("web")
            for keys, allowed in ((modules, MODULES), (web, web_options)):
                if not isinstance(keys, list) or len(keys) > len(allowed) or any(
                        not isinstance(key, str) or key not in allowed for key in keys):
                    return jsonify(error="权限配置无效"), 400
            db.execute("""
                INSERT INTO account_feature_restrictions VALUES(?,?,?,?,?,?)
                ON CONFLICT(scope,owner_id) DO UPDATE SET disabled_modules=excluded.disabled_modules,
                disabled_web=excluded.disabled_web,updated_by=excluded.updated_by,updated_at=excluded.updated_at
            """, (scope, owner, json.dumps(modules), json.dumps(web), session["user_id"], _utc()))
            db.commit()
            app.logger.info("Feature permissions updated actor=%s scope=%s owner=%s", session["user_id"], scope, owner)
        modules, web = restrictions(scope, owner)
        return jsonify(modules=modules, web=web, module_options=MODULES, web_options=web_options)

    @app.get("/api/managed-accounts/<scope>/<int:owner>/cloud-config")
    @manage.admin_required
    def admin_cloud_config(scope, owner):
        db = manage.get_db()
        if scope not in ("general", "tt") or not db.execute(f"SELECT 1 FROM {TABLES[scope]} WHERE id=?", (owner,)).fetchone():
            return jsonify(error="账号不存在"), 404
        row = db.execute("""
           SELECT revision,updated_at,source_device_name FROM account_config_backups WHERE scope=? AND owner_id=?
        """, (scope, owner)).fetchone()
        return jsonify(exists=bool(row), **(dict(row) if row else {})), 200, {"Cache-Control": "no-store"}

    def web_allowed(key):
        owner = session.get("user_id")
        if not owner:
            return True
        scope = "tt" if session.get("user_type") == "tt" else "general"
        return key not in restrictions(scope, owner)[1]

    app.jinja_env.globals["web_feature_allowed"] = web_allowed

    @app.before_request
    def enforce_web_features():
        key = {
            "video_channel_dramas_page": "video_channel_dramas",
            "kuaishou_dramas_page": "kuaishou_dramas",
            "tt_dramas_page": "tt_dramas",
            "upload_records_page": "upload_records",
            "list_upload_records": "upload_records",
            "hongguo.ui_page": "hongguo",
        }.get(request.endpoint)
        if request.endpoint == "list_platform_dramas":
            platform = manage.normalize_upload_record_platform(request.args.get("platform") or "video_channel")
            key = {"video_channel": "video_channel_dramas", "kuaishou": "kuaishou_dramas", "tt": "tt_dramas"}.get(platform)
        if key and not web_allowed(key):
            if request.path.startswith("/api/"):
                return jsonify(error="当前用户无此功能权限"), 403
            abort(403)
