"""云帆软件授权账号。

和 TikTok 助手的 tt_users 分开：单独的用户表、设备表和登录凭证盐。
TikTok 的登录凭证不能用来打开云帆。
"""

from __future__ import annotations

import sqlite3

from flask import jsonify, request
from werkzeug.security import generate_password_hash

KS_ACCOUNT_TOKEN_SALT = "yunfan-account"


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ks_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT,
            email TEXT,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            max_devices INTEGER NOT NULL DEFAULT 1,
            edition TEXT NOT NULL DEFAULT 'pro',
            expires_at TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ks_user_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ks_user_id INTEGER NOT NULL,
            machine_id TEXT NOT NULL,
            device_name TEXT,
            app_name TEXT,
            app_version TEXT,
            token_hash TEXT NOT NULL,
            logged_in_at TEXT,
            last_verified_at TEXT,
            login_ip TEXT,
            login_ip_region TEXT,
            last_ip TEXT,
            last_ip_region TEXT,
            revoked_at TEXT,
            FOREIGN KEY (ks_user_id) REFERENCES ks_users(id) ON DELETE CASCADE,
            UNIQUE(ks_user_id, machine_id)
        )
        """
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ks_users_email_unique "
        "ON ks_users(lower(email)) WHERE email IS NOT NULL AND email != ''"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ks_user_devices_user_id ON ks_user_devices(ks_user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ks_user_devices_machine_id ON ks_user_devices(machine_id)"
    )


def issue_ks_account_token(*, user_row: sqlite3.Row, machine_id: str) -> str:
    import datetime

    import app as manage_app

    serializer = manage_app.get_license_serializer()
    payload = {
        "ks_user_id": user_row["id"],
        "username": user_row["username"],
        "email": user_row["email"] or "",
        "machine_id": machine_id,
        "edition": user_row["edition"],
        "expires_at": user_row["expires_at"] or "",
        "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return serializer.dumps(payload, salt=KS_ACCOUNT_TOKEN_SALT)


def verify_ks_account_token(token: str) -> dict:
    import app as manage_app

    serializer = manage_app.get_license_serializer()
    try:
        from itsdangerous import BadSignature, BadTimeSignature, SignatureExpired

        return serializer.loads(
            token,
            salt=KS_ACCOUNT_TOKEN_SALT,
            max_age=manage_app.ACCOUNT_TOKEN_MAX_AGE_SECONDS,
        )
    except (BadSignature, BadTimeSignature, SignatureExpired):
        raise ValueError("快手授权账号登录凭证无效或已过期")


def register_routes(flask_app) -> None:
    @flask_app.route("/client-api/ks/account/login", methods=["POST"])
    @flask_app.route("/ks/account/login", methods=["POST"])
    def client_login_ks_account():
        import app as manage_app

        data = request.get_json(silent=True) or {}
        payload, error = manage_app.validate_account_auth_payload(data)
        if error:
            return jsonify({"ok": False, "message": error}), 400
        if not payload["password"]:
            return jsonify({"ok": False, "message": "请输入密码"}), 400

        db = manage_app.get_db()
        user_row = _get_user_by_account(db, payload["account"])
        if not user_row or not manage_app.check_password_hash(user_row["password_hash"], payload["password"]):
            return jsonify({"ok": False, "message": "用户名、邮箱或密码不正确"}), 401

        client_ip, client_ip_region = manage_app.get_request_ip_context(db)
        try:
            result = activate_ks_account_for_machine(
                db,
                user_row=user_row,
                machine_id=payload["machine_id"],
                device_name=payload["device_name"],
                app_name=payload["app_name"],
                app_version=payload["app_version"],
                force_login=payload["force_login"],
                client_ip=client_ip,
                client_ip_region=client_ip_region,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        return jsonify({"ok": True, "data": result})

    @flask_app.route("/client-api/ks/account/logout", methods=["POST"])
    @flask_app.route("/ks/account/logout", methods=["POST"])
    def client_logout_ks_account():
        import app as manage_app

        data = request.get_json(silent=True) or {}
        machine_id = str(data.get("machine_id") or "").strip()
        token = str(data.get("token") or "").strip()
        account = str(data.get("account") or data.get("username") or data.get("email") or "").strip()
        if not machine_id:
            return jsonify({"ok": False, "message": "机器码不能为空"}), 400
        if not token:
            return jsonify({"ok": False, "message": "登录凭证不能为空"}), 400

        try:
            token_payload = verify_ks_account_token(token)
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        if token_payload.get("machine_id") != machine_id:
            return jsonify({"ok": False, "message": "登录凭证与当前机器不匹配"}), 400

        db = manage_app.get_db()
        user_row = db.execute(
            "SELECT * FROM ks_users WHERE id = ?",
            (token_payload.get("ks_user_id"),),
        ).fetchone()
        if not user_row:
            return jsonify({"ok": False, "message": "快手授权账号不存在"}), 404
        if account and account not in {user_row["username"], user_row["email"] or ""}:
            return jsonify({"ok": False, "message": "登录凭证与当前快手授权账号不匹配"}), 400

        try:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                """
                UPDATE ks_user_devices
                SET revoked_at = ?
                WHERE ks_user_id = ?
                  AND machine_id = ?
                  AND token_hash = ?
                  AND (revoked_at IS NULL OR revoked_at = '')
                """,
                (manage_app.now_iso(), user_row["id"], machine_id, manage_app.hash_token(token)),
            )
            if result.rowcount == 0:
                db.rollback()
                return jsonify({"ok": False, "message": "当前机器未登录或登录凭证已失效"}), 400
            db.execute(
                "UPDATE ks_users SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_row["id"],),
            )
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise
        return jsonify(
            {
                "ok": True,
                "message": "退出登录成功",
                "data": {"username": user_row["username"], "machine_id": machine_id},
            }
        )

    @flask_app.route("/client-api/ks/account/verify", methods=["POST"])
    @flask_app.route("/ks/account/verify", methods=["POST"])
    def client_verify_ks_account():
        import app as manage_app

        data = request.get_json(silent=True) or {}
        payload, error = manage_app.validate_account_auth_payload(data)
        if error:
            return jsonify({"ok": False, "message": error}), 400
        if not payload["token"]:
            return jsonify({"ok": False, "message": "登录凭证不能为空"}), 400

        try:
            token_payload = verify_ks_account_token(payload["token"])
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        if token_payload.get("machine_id") != payload["machine_id"]:
            return jsonify({"ok": False, "message": "登录凭证与当前机器不匹配"}), 400

        db = manage_app.get_db()
        user_row = db.execute(
            "SELECT * FROM ks_users WHERE id = ?",
            (token_payload.get("ks_user_id"),),
        ).fetchone()
        if not user_row:
            return jsonify({"ok": False, "message": "快手授权账号不存在"}), 404
        if payload["account"] and payload["account"] not in {user_row["username"], user_row["email"] or ""}:
            return jsonify({"ok": False, "message": "登录凭证与当前快手授权账号不匹配"}), 400

        device_row = db.execute(
            """
            SELECT *
            FROM ks_user_devices
            WHERE ks_user_id = ? AND machine_id = ? AND (revoked_at IS NULL OR revoked_at = '')
            """,
            (user_row["id"], payload["machine_id"]),
        ).fetchone()
        if not device_row:
            return jsonify({"ok": False, "message": "当前机器未登录"}), 400
        if device_row["token_hash"] != manage_app.hash_token(payload["token"]):
            return jsonify({"ok": False, "message": "登录凭证已失效，请重新登录"}), 400

        client_ip, client_ip_region = manage_app.get_request_ip_context(db)
        try:
            result = activate_ks_account_for_machine(
                db,
                user_row=user_row,
                machine_id=payload["machine_id"],
                device_name=payload["device_name"],
                app_name=payload["app_name"],
                app_version=payload["app_version"],
                client_ip=client_ip,
                client_ip_region=client_ip_region,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        return jsonify({"ok": True, "data": result})

    @flask_app.route("/api/ks-users", methods=["GET"])
    @manage_login_required
    def list_ks_users():
        import app as manage_app

        db = manage_app.get_db()
        rows = db.execute(
            """
            SELECT
                u.id, u.username, u.full_name, u.email, 'ks_user' AS role, u.status, u.max_devices,
                u.edition, u.expires_at, u.created_at,
                (SELECT COUNT(*) FROM ks_user_devices d WHERE d.ks_user_id = u.id AND (d.revoked_at IS NULL OR d.revoked_at = '')) AS active_devices,
                (SELECT COUNT(*) FROM ks_user_devices d WHERE d.ks_user_id = u.id) AS total_devices,
                (SELECT COALESCE(MAX(d.last_verified_at), '') FROM ks_user_devices d WHERE d.ks_user_id = u.id) AS last_verified_at
            FROM ks_users u
            ORDER BY u.created_at DESC
            """
        ).fetchall()
        items = []
        changed = False
        for row in rows:
            item = dict(row)
            ip, region, row_changed = manage_app.latest_user_device_ip_context(
                db,
                table_name="ks_user_devices",
                owner_column="ks_user_id",
                owner_id=int(item["id"]),
            )
            item["ip"] = ip
            item["ip_region"] = region
            items.append(item)
            changed = changed or row_changed
        if changed:
            db.commit()
        return jsonify(items)

    @flask_app.route("/api/ks-users", methods=["POST"])
    @manage_login_required
    def create_ks_user():
        import app as manage_app

        data = request.get_json(silent=True) or {}
        payload, error = manage_app.sanitize_tt_user_payload(data, require_password=True)
        if error:
            return jsonify({"error": error}), 400
        db = manage_app.get_db()
        try:
            db.execute(
                """
                INSERT INTO ks_users (
                    username, full_name, email, password_hash, status,
                    max_devices, edition, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    payload["username"],
                    payload["full_name"],
                    payload["email"] or None,
                    generate_password_hash(payload["password"]),
                    payload["status"],
                    payload["max_devices"],
                    payload["edition"],
                    payload["expires_at"],
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "快手授权账号或邮箱已存在"}), 400
        return jsonify({"message": "创建快手授权账号成功"}), 201

    @flask_app.route("/api/ks-users/<int:user_id>", methods=["PUT"])
    @manage_login_required
    def update_ks_user(user_id: int):
        import app as manage_app

        data = request.get_json(silent=True) or {}
        payload, error = manage_app.sanitize_tt_user_payload(data)
        if error:
            return jsonify({"error": error}), 400
        db = manage_app.get_db()
        try:
            result = db.execute(
                """
                UPDATE ks_users
                SET username = ?, full_name = ?, email = ?, status = ?, max_devices = ?,
                    edition = ?, expires_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    payload["username"],
                    payload["full_name"],
                    payload["email"] or None,
                    payload["status"],
                    payload["max_devices"],
                    payload["edition"],
                    payload["expires_at"],
                    user_id,
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "快手授权账号或邮箱已存在"}), 400
        if result.rowcount == 0:
            return jsonify({"error": "未找到该快手授权账号"}), 404
        return jsonify({"message": "快手授权账号已更新"})

    @flask_app.route("/api/ks-users/<int:user_id>/devices", methods=["GET"])
    @manage_login_required
    def list_ks_user_devices(user_id: int):
        import app as manage_app

        db = manage_app.get_db()
        user = db.execute(
            "SELECT id, username, full_name, email, status, max_devices, edition, expires_at FROM ks_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            return jsonify({"error": "未找到该快手授权账号"}), 404
        rows = db.execute(
            """
            SELECT id, machine_id, device_name, app_name, app_version, logged_in_at, last_verified_at,
                   login_ip, login_ip_region, last_ip, last_ip_region, revoked_at
            FROM ks_user_devices
            WHERE ks_user_id = ?
            ORDER BY COALESCE(last_verified_at, logged_in_at, '') DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        devices = []
        changed = False
        for row in rows:
            item, row_changed = manage_app.enrich_device_ip_region(db, "ks_user_devices", row)
            devices.append(item)
            changed = changed or row_changed
        if changed:
            db.commit()
        return jsonify({"user": dict(user), "devices": devices})

    @flask_app.route("/api/ks-users/<int:user_id>/devices/<int:device_id>/revoke", methods=["POST"])
    @manage_login_required
    def revoke_ks_user_device(user_id: int, device_id: int):
        import app as manage_app

        db = manage_app.get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                """
                UPDATE ks_user_devices
                SET revoked_at = ?
                WHERE id = ? AND ks_user_id = ? AND (revoked_at IS NULL OR revoked_at = '')
                """,
                (manage_app.now_iso(), device_id, user_id),
            )
            if result.rowcount == 0:
                db.rollback()
                return jsonify({"error": "未找到可解绑的快手授权设备"}), 404
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise
        return jsonify({"message": "快手授权设备已解绑"})

    @flask_app.route("/api/ks-users/<int:user_id>", methods=["DELETE"])
    @manage_login_required
    def delete_ks_user(user_id: int):
        import app as manage_app

        db = manage_app.get_db()
        result = db.execute("DELETE FROM ks_users WHERE id = ?", (user_id,))
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "未找到该快手授权账号"}), 404
        return jsonify({"message": "删除成功"})

    @flask_app.route("/api/ks-users/<int:user_id>/password", methods=["PUT"])
    @manage_login_required
    def change_ks_user_password(user_id: int):
        import app as manage_app

        data = request.get_json(silent=True) or {}
        new_password = (data.get("new_password") or "").strip()
        if len(new_password) < 6:
            return jsonify({"error": "新密码至少6位"}), 400
        db = manage_app.get_db()
        result = db.execute(
            "UPDATE ks_users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )
        db.commit()
        if result.rowcount == 0:
            return jsonify({"error": "未找到该快手授权账号"}), 404
        return jsonify({"message": "快手授权账号密码已更新"})


def manage_login_required(view):
    import app as manage_app

    return manage_app.admin_required(manage_app.login_required(view))


def activate_ks_account_for_machine(
    db: sqlite3.Connection,
    *,
    user_row: sqlite3.Row,
    machine_id: str,
    device_name: str,
    app_name: str,
    app_version: str,
    force_login: bool = False,
    client_ip: str = "",
    client_ip_region: str = "",
) -> dict:
    import app as manage_app

    ok, error = _ensure_can_login(user_row)
    if not ok:
        raise ValueError(error)

    device_row = db.execute(
        """
        SELECT *
        FROM ks_user_devices
        WHERE ks_user_id = ? AND machine_id = ?
        """,
        (user_row["id"], machine_id),
    ).fetchone()

    token = issue_ks_account_token(user_row=user_row, machine_id=machine_id)
    token_hash = manage_app.hash_token(token)
    now_value = manage_app.now_iso()
    normalized_client_ip = manage_app.normalize_ip_address(client_ip)
    normalized_client_region = str(client_ip_region or "").strip()
    replaced_device_count = 0

    active_current_row = db.execute(
        """
        SELECT id
        FROM ks_user_devices
        WHERE ks_user_id = ? AND machine_id = ? AND (revoked_at IS NULL OR revoked_at = '')
        """,
        (user_row["id"], machine_id),
    ).fetchone()
    if active_current_row is not None:
        ok, device_limit_error = _ensure_device_limit(db, user_row, machine_id=machine_id)
        if not ok:
            raise ValueError(device_limit_error)
    if active_current_row is None:
        active_count = _active_device_count(db, user_row["id"])
        max_devices = max(1, int(user_row["max_devices"] or manage_app.ACCOUNT_DEFAULT_MAX_DEVICES))
        overflow_count = active_count - max_devices + 1
        if overflow_count > 0:
            if not force_login:
                raise ValueError("快手授权账号已在其他电脑登录")
            old_rows = db.execute(
                """
                SELECT id
                FROM ks_user_devices
                WHERE ks_user_id = ?
                  AND machine_id != ?
                  AND (revoked_at IS NULL OR revoked_at = '')
                ORDER BY COALESCE(last_verified_at, logged_in_at, '') ASC, id ASC
                LIMIT ?
                """,
                (user_row["id"], machine_id, overflow_count),
            ).fetchall()
            for old_row in old_rows:
                db.execute(
                    """
                    UPDATE ks_user_devices
                    SET revoked_at = ?
                    WHERE id = ? AND (revoked_at IS NULL OR revoked_at = '')
                    """,
                    (now_value, old_row["id"]),
                )
            replaced_device_count = len(old_rows)

    if device_row:
        db.execute(
            """
            UPDATE ks_user_devices
            SET token_hash = ?, device_name = ?, app_name = ?, app_version = ?,
                last_verified_at = ?,
                login_ip = COALESCE(NULLIF(login_ip, ''), ?),
                login_ip_region = COALESCE(NULLIF(login_ip_region, ''), ?),
                last_ip = ?,
                last_ip_region = ?,
                revoked_at = NULL
            WHERE id = ?
            """,
            (
                token_hash,
                device_name or None,
                app_name or None,
                app_version or None,
                now_value,
                normalized_client_ip or None,
                normalized_client_region or None,
                normalized_client_ip or None,
                normalized_client_region or None,
                device_row["id"],
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO ks_user_devices (
                ks_user_id, machine_id, device_name, app_name, app_version,
                token_hash, logged_in_at, last_verified_at,
                login_ip, login_ip_region, last_ip, last_ip_region
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_row["id"],
                machine_id,
                device_name or None,
                app_name or None,
                app_version or None,
                token_hash,
                now_value,
                now_value,
                normalized_client_ip or None,
                normalized_client_region or None,
                normalized_client_ip or None,
                normalized_client_region or None,
            ),
        )

    db.execute(
        "UPDATE ks_users SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (user_row["id"],),
    )
    db.commit()
    return _build_auth_response(
        user_row,
        machine_id=machine_id,
        token=token,
        logged_in_at=(
            str(device_row["logged_in_at"])
            if device_row and device_row["logged_in_at"]
            else now_value
        ),
        last_verified_at=now_value,
        replaced_device_count=replaced_device_count,
        app_name=app_name,
        app_version=app_version,
    )


def _get_user_by_account(db: sqlite3.Connection, account: str) -> sqlite3.Row | None:
    value = str(account or "").strip()
    if not value:
        return None
    return db.execute(
        """
        SELECT *
        FROM ks_users
        WHERE username = ? OR lower(COALESCE(email, '')) = lower(?)
        """,
        (value, value),
    ).fetchone()


def _ensure_can_login(user_row: sqlite3.Row) -> tuple[bool, str]:
    import app as manage_app

    if str(user_row["status"] or "active") != "active":
        return False, "快手授权账号已停用"
    if manage_app.is_user_account_expired(user_row):
        return False, "快手授权账号已过期"
    return True, ""


def _active_device_count(db: sqlite3.Connection, ks_user_id: int) -> int:
    row = db.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM ks_user_devices
        WHERE ks_user_id = ? AND (revoked_at IS NULL OR revoked_at = '')
        """,
        (ks_user_id,),
    ).fetchone()
    return int(row["cnt"] if row else 0)


def _ensure_device_limit(db: sqlite3.Connection, user_row: sqlite3.Row, *, machine_id: str) -> tuple[bool, str]:
    import app as manage_app

    active_count = _active_device_count(db, user_row["id"])
    max_devices = max(1, int(user_row["max_devices"] or manage_app.ACCOUNT_DEFAULT_MAX_DEVICES))
    if active_count <= max_devices:
        return True, ""
    retained_rows = db.execute(
        """
        SELECT machine_id
        FROM ks_user_devices
        WHERE ks_user_id = ? AND (revoked_at IS NULL OR revoked_at = '')
        ORDER BY COALESCE(last_verified_at, logged_in_at, '') DESC, id DESC
        LIMIT ?
        """,
        (user_row["id"], max_devices),
    ).fetchall()
    retained_machine_ids = {str(row["machine_id"] or "").strip() for row in retained_rows}
    if str(machine_id or "").strip() in retained_machine_ids:
        return True, ""
    return (
        False,
        f"快手授权账号登录设备已超过上限（{active_count}/{max_devices}），请在管理后台解绑设备后重新登录",
    )


def _build_auth_response(
    user_row: sqlite3.Row,
    *,
    machine_id: str,
    token: str,
    logged_in_at: str | None = None,
    last_verified_at: str | None = None,
    replaced_device_count: int = 0,
    app_name: str = "",
    app_version: str = "",
) -> dict:
    import datetime

    import app as manage_app

    verified_at = last_verified_at or datetime.datetime.now().isoformat(timespec="seconds")
    username = str(user_row["username"] or "")
    response = {
        "username": username,
        "account_username": username,
        "email": user_row["email"] or "",
        "license_key_masked": username,
        "machine_id": machine_id,
        "token": token,
        "activated_at": logged_in_at or verified_at,
        "last_verified_at": verified_at,
        "expires_at": user_row["expires_at"] or "",
        "edition": user_row["edition"],
        "licensee": username,
        "max_devices": int(user_row["max_devices"] or manage_app.ACCOUNT_DEFAULT_MAX_DEVICES),
        "replaced_device_count": int(replaced_device_count or 0),
    }
    response["authorization_ticket"] = manage_app.issue_tt_authorization_ticket(
        user_row=user_row,
        machine_id=machine_id,
        token=token,
        app_name=app_name,
        app_version=app_version,
        verified_at=verified_at,
    )
    return response
