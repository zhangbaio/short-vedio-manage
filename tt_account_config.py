"""按 TT 用户同步 TikTok 助手账号配置、登录态和印章。

密码、代理密码、登录态和印章加密后保存。本机路径不进入同步数据。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import jsonify, request

ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
SECRET_KEYS = ("tiktokLoginPassword", "tiktokProxyPassword")
LOCAL_ONLY_KEYS = {
    "id",
    "profiledir",
    "profile_dir",
    "tiktokstoragestatepath",
    "tiktok_storage_state_path",
    "lastworkspace",
    "last_workspace",
    "lastdownloadworkspace",
    "last_download_workspace",
    "tiktokuploadprofilepath",
    "tiktok_upload_profile_path",
    "tiktokarchiverootdir",
    "tiktok_archive_root_dir",
    "tiktokexcelreportpath",
    "tiktok_excel_report_path",
    "tiktokproofsealpath",
    "tiktok_proof_seal_path",
    "tiktokexternalbrowsercdpendpoint",
    "tiktok_external_browser_cdp_endpoint",
    "tiktokfingerprintbrowsercdpendpoint",
    "tiktok_fingerprint_browser_cdp_endpoint",
    "tiktokcopyrightmaterialfilepath",
    "tiktok_copyright_material_file_path",
    "status",
    "updatedat",
    "updated_at",
}
MAX_CONFIG_BYTES = 400_000
MAX_SECRETS_BYTES = 8_000
MAX_LOGIN_STATE_BYTES = 4_000_000
MAX_SEAL_BYTES = 8_000_000
MAX_ACCOUNTS = 200
_MISSING = object()


class ConfigSyncError(Exception):
    def __init__(
        self,
        message: str,
        status: int = 400,
        code: str = "bad_request",
        payload: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


def ensure_schema(db) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tt_account_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tt_user_id INTEGER NOT NULL,
            client_account_id TEXT NOT NULL,
            tiktok_username TEXT NOT NULL,
            username_key TEXT NOT NULL,
            config_json TEXT NOT NULL,
            secrets_ciphertext TEXT NOT NULL,
            login_state_ciphertext TEXT,
            login_state_sha256 TEXT NOT NULL DEFAULT '',
            login_state_updated_at TEXT,
            seal_ciphertext TEXT,
            seal_filename TEXT NOT NULL DEFAULT '',
            seal_sha256 TEXT NOT NULL DEFAULT '',
            seal_updated_at TEXT,
            revision INTEGER NOT NULL DEFAULT 1,
            deleted_at TEXT,
            updated_by_machine TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (owner_tt_user_id) REFERENCES tt_users(id) ON DELETE CASCADE,
            UNIQUE(owner_tt_user_id, client_account_id)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tt_account_configs_owner
        ON tt_account_configs(owner_tt_user_id, revision)
        """
    )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tt_account_configs_live_username
        ON tt_account_configs(owner_tt_user_id, username_key)
        WHERE deleted_at IS NULL OR deleted_at = ''
        """
    )


def register_routes(app) -> None:
    @app.route("/client-api/tt/account-configs", methods=["GET"])
    def client_list_tt_account_configs():
        return _client_json(_list_for_request)

    @app.route("/client-api/tt/account-configs/<client_account_id>", methods=["PUT"])
    def client_put_tt_account_config(client_account_id: str):
        body = request.get_json(silent=True)
        return _client_json(lambda db, user, machine_id: upsert_account_config(
            db,
            owner_tt_user_id=int(user["id"]),
            machine_id=machine_id,
            client_account_id=client_account_id,
            body=body if isinstance(body, dict) else None,
        ))

    @app.route("/client-api/tt/account-configs/<client_account_id>", methods=["DELETE"])
    def client_delete_tt_account_config(client_account_id: str):
        return _client_json(lambda db, user, machine_id: delete_account_config(
            db,
            owner_tt_user_id=int(user["id"]),
            machine_id=machine_id,
            client_account_id=client_account_id,
        ))

    @app.route("/client-api/tt/account-configs/<client_account_id>/login-state", methods=["GET"])
    def client_get_tt_account_login_state(client_account_id: str):
        return _client_blob(client_account_id, "login_state_ciphertext")

    @app.route("/client-api/tt/account-configs/<client_account_id>/seal", methods=["GET"])
    def client_get_tt_account_seal(client_account_id: str):
        return _client_blob(client_account_id, "seal_ciphertext")

    @app.route("/api/tt-users/<int:user_id>/account-configs", methods=["GET"])
    def admin_list_tt_account_configs(user_id: int):
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _view():
            db = manage_app.get_db()
            user = db.execute("SELECT id FROM tt_users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return jsonify({"error": "未找到该TT用户"}), 404
            rows = db.execute(
                """
                SELECT * FROM tt_account_configs
                WHERE owner_tt_user_id = ?
                ORDER BY CASE WHEN deleted_at IS NULL OR deleted_at = '' THEN 0 ELSE 1 END,
                         updated_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
            return jsonify([admin_view(row) for row in rows])

        return _view()

    @app.route(
        "/api/tt-users/<int:user_id>/account-configs/<client_account_id>",
        methods=["DELETE"],
    )
    def admin_delete_tt_account_config(user_id: int, client_account_id: str):
        import app as manage_app

        @manage_app.login_required
        @manage_app.admin_required
        def _view():
            db = manage_app.get_db()
            user = db.execute("SELECT id FROM tt_users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return jsonify({"error": "未找到该TT用户"}), 404
            try:
                db.execute("BEGIN IMMEDIATE")
                result = delete_account_config(
                    db,
                    owner_tt_user_id=user_id,
                    machine_id="admin",
                    client_account_id=client_account_id,
                )
                db.commit()
            except ConfigSyncError as exc:
                db.rollback()
                return jsonify({"error": str(exc)}), exc.status
            except Exception:
                db.rollback()
                raise
            return jsonify({"message": "已删除云端账号", "data": result})

        return _view()


def _client_json(action):
    import app as manage_app

    db = manage_app.get_db()
    machine_id = _machine_id()
    if not machine_id:
        return jsonify({"ok": False, "message": "机器码不能为空"}), 400
    try:
        db.execute("BEGIN IMMEDIATE")
        user = manage_app._authenticate_tt_account_from_request(db)
        if user is None:
            db.rollback()
            return jsonify({"ok": False, "message": "鉴权失败：TT 登录态无效"}), 401
        result = action(db, user, machine_id)
        db.commit()
    except ConfigSyncError as exc:
        db.rollback()
        body = {"ok": False, "code": exc.code, "message": str(exc)}
        if exc.payload is not None:
            body["data"] = exc.payload
        return jsonify(body), exc.status
    except Exception:
        db.rollback()
        manage_app.app.logger.exception("同步 TikTok 账号配置失败")
        return jsonify({"ok": False, "message": "同步 TikTok 账号配置失败"}), 500
    return jsonify({"ok": True, "data": result})


def _client_blob(client_account_id: str, column: str):
    import app as manage_app

    db = manage_app.get_db()
    machine_id = _machine_id()
    if not machine_id:
        return jsonify({"ok": False, "message": "机器码不能为空"}), 400
    user = manage_app._authenticate_tt_account_from_request(db)
    if user is None:
        return jsonify({"ok": False, "message": "鉴权失败：TT 登录态无效"}), 401
    if not ACCOUNT_ID_RE.fullmatch(client_account_id or ""):
        return jsonify({"ok": False, "message": "账号 ID 无效"}), 400
    row = db.execute(
        f"""
        SELECT {column}, deleted_at
        FROM tt_account_configs
        WHERE owner_tt_user_id = ? AND client_account_id = ?
        """,
        (int(user["id"]), client_account_id),
    ).fetchone()
    if not row or _is_deleted(row) or not row[column]:
        return jsonify({"ok": False, "message": "没有可下载的文件"}), 404
    try:
        payload = decrypt_bytes(row[column])
    except Exception:
        manage_app.app.logger.exception("解密 TikTok 账号同步文件失败")
        return jsonify({"ok": False, "message": "同步文件无法解密"}), 500
    return app_response_bytes(payload)


def app_response_bytes(payload: bytes):
    from flask import Response

    return Response(payload, mimetype="application/octet-stream")


def _list_for_request(db, user, _machine_id: str) -> dict[str, Any]:
    rows = db.execute(
        """
        SELECT * FROM tt_account_configs
        WHERE owner_tt_user_id = ?
        ORDER BY client_account_id
        """,
        (int(user["id"]),),
    ).fetchall()
    return {"accounts": [client_view(row) for row in rows]}


def upsert_account_config(
    db,
    *,
    owner_tt_user_id: int,
    machine_id: str,
    client_account_id: str,
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    import app as manage_app

    if not ACCOUNT_ID_RE.fullmatch(client_account_id or ""):
        raise ConfigSyncError("账号 ID 无效")
    if not isinstance(body, dict):
        raise ConfigSyncError("请求体必须是 JSON 对象")
    if "base_revision" not in body:
        raise ConfigSyncError("缺少 base_revision")
    try:
        base_revision = int(body.get("base_revision"))
    except (TypeError, ValueError):
        raise ConfigSyncError("base_revision 无效") from None
    if base_revision < 0:
        raise ConfigSyncError("base_revision 无效")

    username, username_key = manage_app.normalize_tt_client_username(body.get("tiktok_username"))
    if not username or len(username) > 200:
        raise ConfigSyncError("TikTok 用户名不能为空")
    config = _clean_config(body.get("config"))
    secrets = _clean_secrets(body.get("secrets"))
    login_state = _decode_blob(
        body,
        "login_state_base64",
        MAX_LOGIN_STATE_BYTES,
        "登录态",
    )
    seal = _decode_blob(body, "seal_base64", MAX_SEAL_BYTES, "印章")
    seal_filename = _seal_filename(body.get("seal_filename") or "")
    if seal is not None and seal[0] and not seal_filename:
        raise ConfigSyncError("印章文件名无效")

    _ensure_account_capacity(db, owner_tt_user_id, client_account_id, username_key)
    target, adopted = _resolve_target(
        db,
        owner_tt_user_id=owner_tt_user_id,
        client_account_id=client_account_id,
        username_key=username_key,
    )
    now_value = manage_app.now_iso()
    if target is None:
        revision = 1
        login_sha, login_cipher, login_at = _new_blob(login_state, now_value)
        seal_sha, seal_cipher, seal_at, stored_name = _new_seal(seal, seal_filename, now_value)
        db.execute(
            """
            INSERT INTO tt_account_configs (
                owner_tt_user_id, client_account_id, tiktok_username, username_key,
                config_json, secrets_ciphertext, login_state_ciphertext, login_state_sha256,
                login_state_updated_at, seal_ciphertext, seal_filename, seal_sha256,
                seal_updated_at, revision, deleted_at, updated_by_machine, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                owner_tt_user_id,
                client_account_id,
                username,
                username_key,
                canonical_json(config),
                encrypt_json(secrets),
                login_cipher,
                login_sha,
                login_at,
                seal_cipher,
                stored_name,
                seal_sha,
                seal_at,
                revision,
                machine_id,
                now_value,
                now_value,
            ),
        )
        return {
            "client_account_id": client_account_id,
            "revision": revision,
            "applied": True,
            "adopted": False,
        }

    current_config = json.loads(target["config_json"])
    current_secrets = decrypt_json(target["secrets_ciphertext"])
    same_config = canonical_json(config) == canonical_json(current_config)
    same_secrets = canonical_json(secrets) == canonical_json(current_secrets)
    same_login = login_state is None or login_state[1] == (target["login_state_sha256"] or "")
    same_seal = seal is None or seal[1] == (target["seal_sha256"] or "")
    if same_config and same_secrets and same_login and same_seal and not _is_deleted(target):
        return {
            "client_account_id": target["client_account_id"],
            "revision": int(target["revision"]),
            "applied": True,
            "adopted": adopted,
        }
    if int(target["revision"]) != base_revision:
        raise ConfigSyncError(
            "账号配置已在其他电脑更新",
            409,
            "revision_conflict",
            client_view(target),
        )

    login_sha = target["login_state_sha256"] or ""
    login_cipher = target["login_state_ciphertext"]
    login_at = target["login_state_updated_at"]
    if login_state is not None:
        login_sha, login_cipher, login_at = _new_blob(login_state, now_value)
    seal_sha = target["seal_sha256"] or ""
    seal_cipher = target["seal_ciphertext"]
    seal_at = target["seal_updated_at"]
    stored_name = target["seal_filename"] or ""
    if seal is not None:
        seal_sha, seal_cipher, seal_at, stored_name = _new_seal(seal, seal_filename, now_value)
    revision = int(target["revision"]) + 1
    db.execute(
        """
        UPDATE tt_account_configs
        SET tiktok_username = ?, username_key = ?, config_json = ?, secrets_ciphertext = ?,
            login_state_ciphertext = ?, login_state_sha256 = ?, login_state_updated_at = ?,
            seal_ciphertext = ?, seal_filename = ?, seal_sha256 = ?, seal_updated_at = ?,
            revision = ?, deleted_at = NULL, updated_by_machine = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            username,
            username_key,
            canonical_json(config),
            encrypt_json(secrets),
            login_cipher,
            login_sha,
            login_at,
            seal_cipher,
            stored_name,
            seal_sha,
            seal_at,
            revision,
            machine_id,
            now_value,
            int(target["id"]),
        ),
    )
    return {
        "client_account_id": target["client_account_id"],
        "revision": revision,
        "applied": True,
        "adopted": adopted,
    }


def delete_account_config(
    db,
    *,
    owner_tt_user_id: int,
    machine_id: str,
    client_account_id: str,
) -> dict[str, Any]:
    import app as manage_app

    if not ACCOUNT_ID_RE.fullmatch(client_account_id or ""):
        raise ConfigSyncError("账号 ID 无效")
    row = db.execute(
        """
        SELECT * FROM tt_account_configs
        WHERE owner_tt_user_id = ? AND client_account_id = ?
        """,
        (owner_tt_user_id, client_account_id),
    ).fetchone()
    if not row or _is_deleted(row):
        return {
            "client_account_id": client_account_id,
            "deleted": True,
            "revision": int(row["revision"]) if row else 0,
        }
    now_value = manage_app.now_iso()
    revision = int(row["revision"]) + 1
    db.execute(
        """
        UPDATE tt_account_configs
        SET config_json = ?, secrets_ciphertext = ?, login_state_ciphertext = NULL,
            login_state_sha256 = '', login_state_updated_at = NULL,
            seal_ciphertext = NULL, seal_filename = '', seal_sha256 = '', seal_updated_at = NULL,
            revision = ?, deleted_at = ?, updated_by_machine = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            "{}",
            encrypt_json({}),
            revision,
            now_value,
            machine_id,
            now_value,
            int(row["id"]),
        ),
    )
    return {"client_account_id": client_account_id, "deleted": True, "revision": revision}


def client_view(row) -> dict[str, Any]:
    deleted = _is_deleted(row)
    return {
        "client_account_id": row["client_account_id"],
        "tiktok_username": "" if deleted else row["tiktok_username"],
        "revision": int(row["revision"]),
        "deleted": deleted,
        "updated_at": row["updated_at"] or "",
        "updated_by_machine": row["updated_by_machine"] or "",
        "config": {} if deleted else json.loads(row["config_json"]),
        "secrets": {} if deleted else decrypt_json(row["secrets_ciphertext"]),
        "login_state_sha256": "" if deleted else (row["login_state_sha256"] or ""),
        "login_state_updated_at": "" if deleted else (row["login_state_updated_at"] or ""),
        "seal_sha256": "" if deleted else (row["seal_sha256"] or ""),
        "seal_filename": "" if deleted else (row["seal_filename"] or ""),
        "seal_updated_at": "" if deleted else (row["seal_updated_at"] or ""),
    }


def admin_view(row) -> dict[str, Any]:
    deleted = _is_deleted(row)
    return {
        "client_account_id": row["client_account_id"],
        "tiktok_username": row["tiktok_username"] or "",
        "revision": int(row["revision"]),
        "deleted": deleted,
        "updated_at": row["updated_at"] or "",
        "updated_by_machine": row["updated_by_machine"] or "",
        "has_login_state": bool(row["login_state_sha256"]) and not deleted,
        "has_seal": bool(row["seal_sha256"]) and not deleted,
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encrypt_json(value: dict[str, Any]) -> str:
    return encrypt_bytes(canonical_json(value).encode("utf-8"))


def decrypt_json(value: str) -> dict[str, Any]:
    parsed = json.loads(decrypt_bytes(value).decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ConfigSyncError("同步密钥数据无效", 500, "decrypt_failed")
    return parsed


def encrypt_bytes(raw: bytes) -> str:
    nonce = _random_bytes(12)
    ciphertext = AESGCM(_encryption_key()).encrypt(nonce, raw, None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_bytes(value: str) -> bytes:
    raw = base64.b64decode(value)
    if len(raw) < 13:
        raise ConfigSyncError("同步数据无效", 500, "decrypt_failed")
    return AESGCM(_encryption_key()).decrypt(raw[:12], raw[12:], None)


def _encryption_key() -> bytes:
    import app as manage_app

    configured = str(manage_app.app.config.get("TT_CONFIG_ENCRYPTION_KEY") or "").strip()
    material = configured or str(manage_app.app.config.get("LICENSE_SIGNING_KEY") or "").strip()
    if not material:
        raise ConfigSyncError("账号同步加密密钥未配置", 500, "key_missing")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"tt-account-config",
        info=b"tt-account-config-v1",
    ).derive(material.encode("utf-8"))


def _random_bytes(size: int) -> bytes:
    import secrets

    return secrets.token_bytes(size)


def _clean_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigSyncError("config 必须是 JSON 对象")
    cleaned = {
        str(key): item
        for key, item in value.items()
        if _key_id(key) not in LOCAL_ONLY_KEYS and str(key) not in SECRET_KEYS
    }
    encoded = canonical_json(cleaned).encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ConfigSyncError("账号配置过大")
    return cleaned


def _clean_secrets(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigSyncError("secrets 必须是 JSON 对象")
    cleaned = {
        key: str(value.get(key) or "")
        for key in SECRET_KEYS
    }
    if len(canonical_json(cleaned).encode("utf-8")) > MAX_SECRETS_BYTES:
        raise ConfigSyncError("账号密钥过大")
    return cleaned


def _decode_blob(body: dict[str, Any], key: str, limit: int, label: str):
    if key not in body:
        return None
    raw = body.get(key)
    if raw in (None, ""):
        return b"", hashlib.sha256(b"").hexdigest()
    if not isinstance(raw, str):
        raise ConfigSyncError(f"{label}格式无效")
    try:
        payload = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ConfigSyncError(f"{label}格式无效") from exc
    if len(payload) > limit:
        raise ConfigSyncError(f"{label}过大")
    return payload, hashlib.sha256(payload).hexdigest()


def _new_blob(blob, now_value: str):
    if blob is None or not blob[0]:
        return "", None, None
    return blob[1], encrypt_bytes(blob[0]), now_value


def _new_seal(blob, filename: str, now_value: str):
    if blob is None or not blob[0]:
        return "", None, None, ""
    return blob[1], encrypt_bytes(blob[0]), now_value, filename


def _seal_filename(value: str) -> str:
    name = str(value or "").replace("\\", "/").split("/")[-1].strip()
    if not name or name in {".", ".."} or len(name) > 80:
        return ""
    if not re.fullmatch(r"[^\\/:*?\"<>|\r\n]+", name):
        return ""
    return name


def _ensure_account_capacity(db, owner_tt_user_id: int, client_account_id: str, username_key: str) -> None:
    count = db.execute(
        """
        SELECT COUNT(*) FROM tt_account_configs
        WHERE owner_tt_user_id = ? AND (deleted_at IS NULL OR deleted_at = '')
        """,
        (owner_tt_user_id,),
    ).fetchone()[0]
    existing = db.execute(
        """
        SELECT 1 FROM tt_account_configs
        WHERE owner_tt_user_id = ?
          AND (client_account_id = ? OR username_key = ?)
          AND (deleted_at IS NULL OR deleted_at = '')
        """,
        (owner_tt_user_id, client_account_id, username_key),
    ).fetchone()
    if existing is None and int(count) >= MAX_ACCOUNTS:
        raise ConfigSyncError("同步账号数量超过 200 个")


def _resolve_target(db, *, owner_tt_user_id: int, client_account_id: str, username_key: str):
    by_id = db.execute(
        """
        SELECT * FROM tt_account_configs
        WHERE owner_tt_user_id = ? AND client_account_id = ?
        """,
        (owner_tt_user_id, client_account_id),
    ).fetchone()
    by_name = db.execute(
        """
        SELECT * FROM tt_account_configs
        WHERE owner_tt_user_id = ? AND username_key = ?
          AND (deleted_at IS NULL OR deleted_at = '')
        """,
        (owner_tt_user_id, username_key),
    ).fetchone()
    if by_name and (by_id is None or by_id["client_account_id"] != by_name["client_account_id"]):
        if by_id is not None and not _is_deleted(by_id):
            raise ConfigSyncError(
                "该 TikTok 账号已绑定另一个配置",
                400,
                "username_taken",
                client_view(by_name),
            )
        return by_name, by_name["client_account_id"] != client_account_id
    return by_id, False


def _machine_id() -> str:
    raw = request.get_json(silent=True)
    body = raw if isinstance(raw, dict) else {}
    machine_id = str(request.headers.get("X-TT-Machine-Id") or body.get("tt_machine_id") or "").strip()
    if len(machine_id) > 200 or any(ord(char) < 32 for char in machine_id):
        return ""
    return machine_id


def _is_deleted(row) -> bool:
    return bool(str(row["deleted_at"] or "").strip())


def _key_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
