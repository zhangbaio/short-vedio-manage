import datetime
import hashlib
import json
import math
import os
import secrets
import sqlite3
import uuid
from functools import wraps
from io import BytesIO

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl import Workbook, load_workbook
from itsdangerous import BadSignature, BadTimeSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATABASE = os.path.join(DATA_DIR, "dramas.db")
REMOTE_UPLOAD_DIR = os.path.join(DATA_DIR, "remote_uploads")

ALLOWED_FLAGS = {"是", "否"}
SORTABLE_FIELDS = {
    "date",
    "original_name",
    "new_name",
    "episodes",
    "duration",
    "review_passed",
    "uploaded",
    "company",
    "created_at",
}
DEFAULT_SORT_FIELD = "date"
DEFAULT_SORT_DIR = "desc"
LICENSE_STATUS_VALUES = {"active", "disabled", "expired"}
LICENSE_EDITION_VALUES = {"basic", "pro", "enterprise"}
LICENSE_LIST_SORTABLE_FIELDS = {
    "license_key": "license_key",
    "licensee": "COALESCE(licensee, '')",
    "edition": "edition",
    "max_activations": "max_activations",
    "active_activations": "(SELECT COUNT(*) FROM license_activations la WHERE la.license_id = licenses.id AND (la.revoked_at IS NULL OR la.revoked_at = ''))",
    "expires_at": "COALESCE(expires_at, '9999-12-31')",
    "last_verified_at": "(SELECT COALESCE(MAX(la.last_verified_at), '') FROM license_activations la WHERE la.license_id = licenses.id)",
    "status": "status",
    "created_at": "created_at",
    "updated_at": "updated_at",
}
LICENSE_LIST_DEFAULT_SORT_FIELD = "created_at"
LICENSE_LIST_DEFAULT_SORT_DIR = "desc"
LICENSE_TOKEN_SALT = "desktop-license"
LICENSE_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
REMOTE_MESSAGE_STATUS_VALUES = {"pending", "sent", "running", "success", "failed", "canceled"}
REMOTE_MESSAGE_TYPE_VALUES = {"text", "command", "image", "status", "log"}
REMOTE_SENDER_TYPE_VALUES = {"user", "client", "system"}

HEADER_MAP = {
    "日期": "date",
    "原剧名": "original_name",
    "新剧名": "new_name",
    "集数": "episodes",
    "时间(分钟)": "duration",
    "是否审核通过": "review_passed",
    "是否上传": "uploaded",
    "素材": "materials",
    "推广语": "promo_text",
    "简介": "description",
    "公司": "company",
    "备注一": "remark1",
    "备注二": "remark2",
    "备注三": "remark3",
}
EXPORT_HEADERS = [
    "日期",
    "原剧名",
    "新剧名",
    "集数",
    "时间(分钟)",
    "是否审核通过",
    "是否上传",
    "素材",
    "推广语",
    "简介",
    "公司",
    "上传者",
    "备注一",
    "备注二",
    "备注三",
]
LICENSE_EXPORT_HEADERS = [
    "激活码",
    "掩码",
    "授权对象",
    "版本",
    "状态",
    "最大设备数",
    "当前绑定设备数",
    "累计绑定记录数",
    "到期时间",
    "最近校验",
    "备注",
    "创建时间",
    "更新时间",
    "删除时间",
]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", uuid.uuid4().hex)
app.config["JSON_AS_ASCII"] = False
app.config["JSON_SORT_KEYS"] = False
app.config["LICENSE_SIGNING_KEY"] = os.environ.get(
    "LICENSE_SIGNING_KEY",
    app.config["SECRET_KEY"],
)


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REMOTE_UPLOAD_DIR, exist_ok=True)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        ensure_data_dir()
        conn = sqlite3.connect(DATABASE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        g.db = conn
    return g.db


def close_db(_: Exception | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


app.teardown_appcontext(close_db)


def init_db() -> None:
    ensure_data_dir()
    with app.app_context():
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dramas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                original_name TEXT NOT NULL,
                new_name TEXT NOT NULL,
                episodes INTEGER,
                duration INTEGER,
                review_passed TEXT NOT NULL DEFAULT '否',
                uploaded TEXT NOT NULL DEFAULT '否',
                materials TEXT,
                promo_text TEXT,
                description TEXT,
                company TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(original_name, new_name)
            );

            CREATE TABLE IF NOT EXISTS licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_key TEXT UNIQUE NOT NULL,
                license_key_masked TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                edition TEXT NOT NULL DEFAULT 'pro',
                licensee TEXT,
                max_activations INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS license_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_id INTEGER NOT NULL,
                machine_id TEXT NOT NULL,
                app_name TEXT,
                app_version TEXT,
                token_hash TEXT NOT NULL,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked_at TEXT,
                FOREIGN KEY (license_id) REFERENCES licenses(id) ON DELETE CASCADE,
                UNIQUE(license_id, machine_id)
            );

            CREATE TABLE IF NOT EXISTS remote_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT UNIQUE NOT NULL,
                client_name TEXT NOT NULL,
                client_token_hash TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL,
                machine_id TEXT,
                device_name TEXT,
                app_version TEXT,
                workspace_path TEXT,
                status TEXT NOT NULL DEFAULT 'offline',
                last_seen_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS remote_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                remote_client_id INTEGER NOT NULL,
                owner_user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (remote_client_id) REFERENCES remote_clients(id) ON DELETE CASCADE,
                FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS remote_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_type TEXT NOT NULL,
                sender_user_id INTEGER,
                remote_client_id INTEGER,
                message_type TEXT NOT NULL,
                content_text TEXT,
                payload_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES remote_conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (sender_user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (remote_client_id) REFERENCES remote_clients(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS remote_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                original_name TEXT,
                stored_path TEXT NOT NULL,
                content_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (message_id) REFERENCES remote_messages(id) ON DELETE CASCADE
            );
            """
        )
        # Migrate: add source column if missing
        try:
            db.execute("ALTER TABLE dramas ADD COLUMN source TEXT DEFAULT NULL")
            db.commit()
        except Exception:
            pass  # column already exists
        for col_def in [
            "ALTER TABLE dramas ADD COLUMN uploader TEXT DEFAULT NULL",
            "ALTER TABLE dramas ADD COLUMN remark1 TEXT DEFAULT NULL",
            "ALTER TABLE dramas ADD COLUMN remark2 TEXT DEFAULT NULL",
            "ALTER TABLE dramas ADD COLUMN remark3 TEXT DEFAULT NULL",
            "ALTER TABLE licenses ADD COLUMN deleted_at TEXT DEFAULT NULL",
            "ALTER TABLE licenses ADD COLUMN deleted_by INTEGER DEFAULT NULL",
        ]:
            try:
                db.execute(col_def)
                db.commit()
            except Exception:
                pass
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_license_activations_license_id ON license_activations(license_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_license_activations_machine_id ON license_activations(machine_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_licenses_deleted_at ON licenses(deleted_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_remote_clients_owner_user_id ON remote_clients(owner_user_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_remote_conversations_client_id ON remote_conversations(remote_client_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_remote_messages_conversation_id ON remote_messages(conversation_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_remote_messages_status ON remote_messages(status)"
        )
        seed_default_users(db)
        db.commit()


def seed_default_users(db: sqlite3.Connection) -> None:
    count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count:
        return
    users = [
        ("admin", generate_password_hash("admin123"), "admin"),
        ("user1", generate_password_hash("user123"), "user"),
    ]
    db.executemany(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", users
    )


init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "需要登录"}), 401
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "权限不足"}), 403
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def get_license_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.config["LICENSE_SIGNING_KEY"])


def mask_license_key(license_key: str) -> str:
    value = str(license_key or "").strip()
    if len(value) <= 8:
        return value
    return f"{value[:4]}****{value[-4:]}"


def generate_license_key() -> str:
    parts = [
        "WXA",
        str(datetime.date.today().year),
        secrets.token_hex(2).upper(),
        secrets.token_hex(2).upper(),
        secrets.token_hex(2).upper(),
    ]
    return "-".join(parts)


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def issue_license_token(*, license_row: sqlite3.Row, machine_id: str) -> str:
    serializer = get_license_serializer()
    payload = {
        "license_id": license_row["id"],
        "license_key": license_row["license_key"],
        "machine_id": machine_id,
        "edition": license_row["edition"],
        "expires_at": license_row["expires_at"],
        "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return serializer.dumps(payload, salt=LICENSE_TOKEN_SALT)


def verify_license_token(token: str) -> dict:
    serializer = get_license_serializer()
    try:
        return serializer.loads(
            token,
            salt=LICENSE_TOKEN_SALT,
            max_age=LICENSE_TOKEN_MAX_AGE_SECONDS,
        )
    except (BadSignature, BadTimeSignature, SignatureExpired):
        raise ValueError("授权 token 无效或已过期")


def parse_iso_datetime(value: str | None) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def is_license_expired(license_row: sqlite3.Row) -> bool:
    expires_at = parse_iso_datetime(license_row["expires_at"])
    if not expires_at:
        return False
    return expires_at <= datetime.datetime.now()


def current_active_activation_count(db: sqlite3.Connection, license_id: int) -> int:
    row = db.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM license_activations
        WHERE license_id = ? AND (revoked_at IS NULL OR revoked_at = '')
        """,
        (license_id,),
    ).fetchone()
    return int(row["cnt"] if row else 0)


def current_total_activation_count(db: sqlite3.Connection, license_id: int) -> int:
    row = db.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM license_activations
        WHERE license_id = ?
        """,
        (license_id,),
    ).fetchone()
    return int(row["cnt"] if row else 0)


def latest_license_verification_at(db: sqlite3.Connection, license_id: int) -> str:
    row = db.execute(
        """
        SELECT MAX(last_verified_at) AS last_verified_at
        FROM license_activations
        WHERE license_id = ?
        """,
        (license_id,),
    ).fetchone()
    if not row:
        return ""
    return str(row["last_verified_at"] or "")


def serialize_license_row(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item["active_activations"] = current_active_activation_count(db, row["id"])
    item["total_activations"] = current_total_activation_count(db, row["id"])
    item["last_verified_at"] = latest_license_verification_at(db, row["id"])
    item["is_deleted"] = bool(item.get("deleted_at"))
    return item


def serialize_activation_row(row: sqlite3.Row) -> dict:
    return dict(row)


def get_license_row(
    db: sqlite3.Connection,
    license_id: int,
    *,
    include_deleted: bool = False,
) -> sqlite3.Row | None:
    sql = "SELECT * FROM licenses WHERE id = ?"
    params: list[object] = [license_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    return db.execute(sql, params).fetchone()


def get_license_rows_by_ids(
    db: sqlite3.Connection,
    license_ids: list[int],
    *,
    include_deleted: bool = False,
) -> list[sqlite3.Row]:
    if not license_ids:
        return []
    placeholders = ",".join(["?"] * len(license_ids))
    sql = f"SELECT * FROM licenses WHERE id IN ({placeholders})"
    params: list[object] = list(license_ids)
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY id DESC"
    return db.execute(sql, params).fetchall()


def build_license_filter_clause(args) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    keyword = str(args.get("keyword") or "").strip()
    if keyword:
        like = f"%{keyword}%"
        clauses.append(
            "(license_key LIKE ? OR license_key_masked LIKE ? OR COALESCE(licensee, '') LIKE ? OR COALESCE(notes, '') LIKE ?)"
        )
        params.extend([like, like, like, like])

    edition = str(args.get("edition") or "").strip().lower()
    if edition in LICENSE_EDITION_VALUES:
        clauses.append("edition = ?")
        params.append(edition)

    status = str(args.get("status") or "").strip().lower()
    show_deleted = str(args.get("show_deleted") or "").strip() == "1"
    if status == "deleted":
        clauses.append("deleted_at IS NOT NULL")
    else:
        if not show_deleted:
            clauses.append("deleted_at IS NULL")
        if status in LICENSE_STATUS_VALUES:
            clauses.append("status = ?")
            params.append(status)

    return clauses, params


def parse_license_ids_from_payload(data: dict) -> tuple[list[int], str | None]:
    raw_ids = data.get("ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return [], "请选择至少一条授权码"
    ids: list[int] = []
    for item in raw_ids:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    ids = list(dict.fromkeys(ids))
    if not ids:
        return [], "请选择至少一条有效的授权码"
    return ids, None


def soft_delete_license_row(db: sqlite3.Connection, row: sqlite3.Row, *, deleted_by: int | None) -> tuple[bool, str]:
    if row["deleted_at"]:
        return False, "该激活码已删除"
    active_activations = current_active_activation_count(db, row["id"])
    if row["status"] == "active" or active_activations > 0:
        return False, f"{row['license_key_masked']} 请先停用并解绑所有设备后删除"
    db.execute(
        """
        UPDATE licenses
        SET status = 'disabled', deleted_at = ?, deleted_by = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (now_iso(), deleted_by, row["id"]),
    )
    return True, ""


def restore_license_row(db: sqlite3.Connection, row: sqlite3.Row) -> tuple[bool, str]:
    if not row["deleted_at"]:
        return False, "该激活码未删除，无需恢复"
    db.execute(
        """
        UPDATE licenses
        SET deleted_at = NULL, deleted_by = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (row["id"],),
    )
    return True, ""


def update_license_status_row(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    status: str,
) -> tuple[bool, str]:
    if row["deleted_at"]:
        return False, f"{row['license_key_masked']} 已删除，不能修改状态"
    if status not in LICENSE_STATUS_VALUES:
        return False, "无效的授权码状态"
    if row["status"] == status:
        return True, ""
    db.execute(
        "UPDATE licenses SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, row["id"]),
    )
    return True, ""


def generate_remote_client_id() -> str:
    return f"rc_{secrets.token_hex(8)}"


def generate_remote_client_token() -> str:
    return secrets.token_urlsafe(24)


def hash_remote_client_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def serialize_remote_client(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "client_name": row["client_name"],
        "owner_user_id": row["owner_user_id"],
        "machine_id": row["machine_id"] or "",
        "device_name": row["device_name"] or "",
        "app_version": row["app_version"] or "",
        "workspace_path": row["workspace_path"] or "",
        "status": row["status"] or "offline",
        "last_seen_at": row["last_seen_at"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def serialize_remote_conversation(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "remote_client_id": row["remote_client_id"],
        "owner_user_id": row["owner_user_id"],
        "title": row["title"] or "",
        "status": row["status"] or "active",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def serialize_remote_message(row: sqlite3.Row, attachments: list[dict] | None = None) -> dict:
    payload = None
    result = None
    try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else None
    except Exception:
        payload = None
    try:
        result = json.loads(row["result_json"]) if row["result_json"] else None
    except Exception:
        result = None
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "sender_type": row["sender_type"],
        "sender_user_id": row["sender_user_id"],
        "remote_client_id": row["remote_client_id"],
        "message_type": row["message_type"],
        "content_text": row["content_text"] or "",
        "payload": payload,
        "status": row["status"] or "pending",
        "result": result,
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        "attachments": attachments or [],
    }


def serialize_remote_attachment(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "file_type": row["file_type"],
        "original_name": row["original_name"] or "",
        "stored_path": row["stored_path"],
        "content_type": row["content_type"] or "",
        "created_at": row["created_at"] or "",
        "download_url": url_for("download_remote_attachment", attachment_id=row["id"]),
    }


def get_remote_client_by_public_id(db: sqlite3.Connection, client_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM remote_clients WHERE client_id = ?",
        (str(client_id or "").strip(),),
    ).fetchone()


def authenticate_remote_client(db: sqlite3.Connection, client_id: str, client_token: str) -> sqlite3.Row | None:
    row = get_remote_client_by_public_id(db, client_id)
    if not row:
        return None
    if hash_remote_client_token(client_token) != row["client_token_hash"]:
        return None
    return row


def require_remote_client() -> tuple[sqlite3.Connection, sqlite3.Row] | tuple[sqlite3.Connection, None]:
    db = get_db()
    data = request.get_json(silent=True) or {}
    client_id = request.headers.get("X-Remote-Client-Id") or data.get("client_id") or request.args.get("client_id") or ""
    client_token = request.headers.get("X-Remote-Client-Token") or data.get("client_token") or request.args.get("client_token") or ""
    row = authenticate_remote_client(db, str(client_id).strip(), str(client_token).strip())
    return db, row


def ensure_remote_conversation_access(db: sqlite3.Connection, conversation_id: int, user_id: int, role: str) -> sqlite3.Row | None:
    row = db.execute(
        """
        SELECT rc.owner_user_id, c.*
        FROM remote_conversations c
        JOIN remote_clients rc ON rc.id = c.remote_client_id
        WHERE c.id = ?
        """,
        (conversation_id,),
    ).fetchone()
    if not row:
        return None
    if role != "admin" and int(row["owner_user_id"]) != int(user_id):
        return None
    return row


def get_or_create_remote_conversation(db: sqlite3.Connection, remote_client_row: sqlite3.Row, *, title: str = "") -> sqlite3.Row:
    row = db.execute(
        """
        SELECT *
        FROM remote_conversations
        WHERE remote_client_id = ? AND status = 'active'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (remote_client_row["id"],),
    ).fetchone()
    if row:
        return row
    db.execute(
        """
        INSERT INTO remote_conversations (remote_client_id, owner_user_id, title, status, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?)
        """,
        (
            remote_client_row["id"],
            remote_client_row["owner_user_id"],
            title or f"{remote_client_row['client_name']} 会话",
            now_iso(),
            now_iso(),
        ),
    )
    db.commit()
    return db.execute(
        "SELECT * FROM remote_conversations WHERE id = last_insert_rowid()"
    ).fetchone()


def validate_client_license_payload(data: dict) -> tuple[dict, str | None]:
    payload = {
        "license_key": str(data.get("license_key") or "").strip(),
        "machine_id": str(data.get("machine_id") or "").strip(),
        "app_name": str(data.get("app_name") or "").strip(),
        "app_version": str(data.get("app_version") or "").strip(),
        "token": str(data.get("token") or "").strip(),
    }
    if not payload["license_key"]:
        return payload, "激活码不能为空"
    if not payload["machine_id"]:
        return payload, "机器码不能为空"
    return payload, None


def build_client_license_response(
    license_row: sqlite3.Row,
    *,
    machine_id: str,
    token: str,
    activated_at: str | None = None,
    last_verified_at: str | None = None,
) -> dict:
    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    return {
        "license_key_masked": license_row["license_key_masked"],
        "machine_id": machine_id,
        "token": token,
        "activated_at": activated_at or last_verified_at or now_iso,
        "last_verified_at": last_verified_at or now_iso,
        "expires_at": license_row["expires_at"] or "",
        "edition": license_row["edition"],
        "licensee": license_row["licensee"] or "",
    }


def activate_license_for_machine(
    db: sqlite3.Connection,
    *,
    license_row: sqlite3.Row,
    machine_id: str,
    app_name: str,
    app_version: str,
) -> dict:
    active_row = db.execute(
        """
        SELECT *
        FROM license_activations
        WHERE license_id = ? AND machine_id = ? AND (revoked_at IS NULL OR revoked_at = '')
        """,
        (license_row["id"], machine_id),
    ).fetchone()

    if not active_row:
        active_count = current_active_activation_count(db, license_row["id"])
        if active_count >= int(license_row["max_activations"] or 1):
            raise ValueError("该激活码已达到最大设备绑定数量")

    token = issue_license_token(license_row=license_row, machine_id=machine_id)
    token_hash = hash_token(token)
    now_iso = datetime.datetime.now().isoformat(timespec="seconds")

    if active_row:
        db.execute(
            """
            UPDATE license_activations
            SET token_hash = ?, app_name = ?, app_version = ?, last_verified_at = ?, revoked_at = NULL
            WHERE id = ?
            """,
            (token_hash, app_name or None, app_version or None, now_iso, active_row["id"]),
        )
    else:
        db.execute(
            """
            INSERT INTO license_activations (
                license_id, machine_id, app_name, app_version, token_hash, activated_at, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (license_row["id"], machine_id, app_name or None, app_version or None, token_hash, now_iso, now_iso),
        )

    db.execute(
        "UPDATE licenses SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (license_row["id"],),
    )
    db.commit()
    return build_client_license_response(
        license_row,
        machine_id=machine_id,
        token=token,
        activated_at=(
            str(active_row["activated_at"])
            if active_row and active_row["activated_at"]
            else now_iso
        ),
        last_verified_at=now_iso,
    )


def sanitize_license_payload(data: dict) -> tuple[dict, str | None]:
    license_key = str(data.get("license_key") or "").strip().upper()
    edition = str(data.get("edition") or "pro").strip().lower()
    status = str(data.get("status") or "active").strip().lower()
    licensee = str(data.get("licensee") or "").strip()
    notes = str(data.get("notes") or "").strip()
    expires_at = str(data.get("expires_at") or "").strip()
    try:
        max_activations = int(data.get("max_activations") or 1)
    except (TypeError, ValueError):
        return {}, "最大激活数必须是正整数"
    if max_activations < 1:
        return {}, "最大激活数必须是正整数"
    if edition not in LICENSE_EDITION_VALUES:
        edition = "pro"
    if status not in LICENSE_STATUS_VALUES:
        status = "active"
    if expires_at:
        try:
            datetime.datetime.fromisoformat(expires_at)
        except ValueError:
            return {}, "到期时间格式不正确，请使用 YYYY-MM-DD 或 ISO 日期时间"
    if not license_key:
        license_key = generate_license_key()
    return {
        "license_key": license_key,
        "license_key_masked": mask_license_key(license_key),
        "status": status,
        "edition": edition,
        "licensee": licensee or None,
        "max_activations": max_activations,
        "expires_at": expires_at or None,
        "notes": notes or None,
    }, None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = get_db()
        user = db.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("index"))
        error = "用户名或密码错误"
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/licenses")
@login_required
@admin_required
def license_management():
    return render_template("licenses.html")


@app.route("/api/me", methods=["GET"])
@login_required
def api_me():
    return jsonify(
        {
            "user_id": session.get("user_id"),
            "username": session.get("username", ""),
            "role": session.get("role", "user"),
        }
    )


@app.route("/client-api/licenses/activate", methods=["POST"])
@app.route("/client-api/license/activate", methods=["POST"])
@app.route("/license/activate", methods=["POST"])
def client_activate_license():
    data = request.get_json(silent=True) or {}
    payload, error = validate_client_license_payload(data)
    if error:
        return jsonify({"ok": False, "message": error}), 400

    db = get_db()
    license_row = db.execute(
        "SELECT * FROM licenses WHERE license_key = ? AND deleted_at IS NULL",
        (payload["license_key"],),
    ).fetchone()
    if not license_row:
        return jsonify({"ok": False, "message": "激活码无效"}), 404
    if license_row["status"] != "active":
        return jsonify({"ok": False, "message": "该激活码已被停用"}), 400
    if is_license_expired(license_row):
        db.execute(
            "UPDATE licenses SET status = 'expired', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (license_row["id"],),
        )
        db.commit()
        return jsonify({"ok": False, "message": "该激活码已过期"}), 400

    try:
        result = activate_license_for_machine(
            db,
            license_row=license_row,
            machine_id=payload["machine_id"],
            app_name=payload["app_name"],
            app_version=payload["app_version"],
        )
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    return jsonify({"ok": True, "data": result})


@app.route("/client-api/licenses/verify", methods=["POST"])
@app.route("/client-api/license/verify", methods=["POST"])
@app.route("/license/verify", methods=["POST"])
def client_verify_license():
    data = request.get_json(silent=True) or {}
    payload, error = validate_client_license_payload(data)
    if error:
        return jsonify({"ok": False, "message": error}), 400
    if not payload["token"]:
        return jsonify({"ok": False, "message": "token 不能为空"}), 400

    db = get_db()
    license_row = db.execute(
        "SELECT * FROM licenses WHERE license_key = ? AND deleted_at IS NULL",
        (payload["license_key"],),
    ).fetchone()
    if not license_row:
        return jsonify({"ok": False, "message": "激活码无效"}), 404
    if license_row["status"] != "active":
        return jsonify({"ok": False, "message": "该激活码已被停用"}), 400
    if is_license_expired(license_row):
        db.execute(
            "UPDATE licenses SET status = 'expired', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (license_row["id"],),
        )
        db.commit()
        return jsonify({"ok": False, "message": "该激活码已过期"}), 400

    try:
        token_payload = verify_license_token(payload["token"])
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    if token_payload.get("license_key") != license_row["license_key"]:
        return jsonify({"ok": False, "message": "授权 token 与激活码不匹配"}), 400
    if token_payload.get("machine_id") != payload["machine_id"]:
        return jsonify({"ok": False, "message": "授权 token 与当前机器不匹配"}), 400

    activation_row = db.execute(
        """
        SELECT *
        FROM license_activations
        WHERE license_id = ? AND machine_id = ? AND (revoked_at IS NULL OR revoked_at = '')
        """,
        (license_row["id"], payload["machine_id"]),
    ).fetchone()
    if not activation_row:
        return jsonify({"ok": False, "message": "当前机器未绑定该激活码"}), 400
    if activation_row["token_hash"] != hash_token(payload["token"]):
        return jsonify({"ok": False, "message": "授权 token 已失效，请重新激活"}), 400

    result = activate_license_for_machine(
        db,
        license_row=license_row,
        machine_id=payload["machine_id"],
        app_name=payload["app_name"],
        app_version=payload["app_version"],
    )
    return jsonify({"ok": True, "data": result})


@app.route("/api/dramas", methods=["GET"])
@login_required
def list_dramas():
    page = max(1, int(request.args.get("page", 1) or 1))
    page_size = int(request.args.get("page_size", 20) or 20)
    page_size = min(100, max(1, page_size))
    sort_by = request.args.get("sort_by", DEFAULT_SORT_FIELD)
    sort_dir = request.args.get("sort_dir", DEFAULT_SORT_DIR).lower()
    if sort_by not in SORTABLE_FIELDS:
        sort_by = DEFAULT_SORT_FIELD
    if sort_dir not in {"asc", "desc"}:
        sort_dir = DEFAULT_SORT_DIR

    clauses, params = build_filter_clause(request.args)
    where_sql = " AND ".join(["1=1"] + clauses)

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) as cnt FROM dramas WHERE {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = db.execute(
        f"SELECT * FROM dramas WHERE {where_sql} ORDER BY {sort_by} {sort_dir.upper()} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    pages = math.ceil(total / page_size) if total else 0

    items = [row_to_dict(row) for row in rows]
    return jsonify(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }
    )


@app.route("/api/dramas", methods=["POST"])
@login_required
@admin_required
def create_drama():
    data = request.get_json(silent=True) or {}
    payload, error = sanitize_drama_payload(data)
    if error:
        return jsonify({"error": error}), 400
    db = get_db()
    try:
        placeholders = ", ".join([f":{k}" for k in payload.keys()])
        columns = ", ".join(payload.keys())
        db.execute(f"INSERT INTO dramas ({columns}) VALUES ({placeholders})", payload)
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "已存在相同的原剧名和新剧名组合"}), 400
    return jsonify({"message": "创建成功"}), 201


@app.route("/api/dramas/<int:drama_id>", methods=["PUT"])
@login_required
@admin_required
def update_drama(drama_id: int):
    data = request.get_json(silent=True) or {}
    payload, error = sanitize_drama_payload(data)
    if error:
        return jsonify({"error": error}), 400
    db = get_db()
    set_clause = ", ".join([f"{key} = :{key}" for key in payload.keys()])
    payload["id"] = drama_id
    try:
        result = db.execute(
            f"UPDATE dramas SET {set_clause} WHERE id = :id",
            payload,
        )
        if result.rowcount == 0:
            return jsonify({"error": "未找到该短剧"}), 404
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "已存在相同的原剧名和新剧名组合"}), 400
    return jsonify({"message": "更新成功"})


@app.route("/api/dramas/<int:drama_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_drama(drama_id: int):
    db = get_db()
    result = db.execute("DELETE FROM dramas WHERE id = ?", (drama_id,))
    db.commit()
    if result.rowcount == 0:
        return jsonify({"error": "未找到该短剧"}), 404
    return jsonify({"message": "删除成功"})


@app.route("/api/dramas/batch-delete", methods=["POST"])
@login_required
@admin_required
def batch_delete():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "请选择要删除的短剧"}), 400
    placeholders = ",".join(["?"] * len(ids))
    db = get_db()
    db.execute(f"DELETE FROM dramas WHERE id IN ({placeholders})", ids)
    db.commit()
    return jsonify({"message": "批量删除完成"})


@app.route("/api/dramas/quick-add", methods=["POST"])
@login_required
@admin_required
def quick_add_dramas():
    data = request.get_json(silent=True) or {}
    names = data.get("names") or []
    company = (data.get("company") or "").strip() or None
    if not isinstance(names, list) or not names:
        return jsonify({"error": "请输入至少一个剧名"}), 400

    today = datetime.date.today().isoformat()
    db = get_db()
    success_count = 0
    duplicates = []

    for raw_name in names:
        name = (raw_name or "").strip()
        if not name:
            continue
        try:
            db.execute(
                "INSERT INTO dramas (date, original_name, new_name, review_passed, uploaded, company, source) VALUES (?, ?, ?, '否', '否', ?, 'quick_add')",
                (today, name, name, company),
            )
            db.commit()
            success_count += 1
        except sqlite3.IntegrityError:
            duplicates.append(name)

    return jsonify({"success_count": success_count, "duplicates": duplicates})


@app.route("/api/dramas/<int:drama_id>/upload", methods=["PATCH"])
@login_required
def toggle_upload(drama_id: int):
    db = get_db()
    row = db.execute(
        "SELECT uploaded FROM dramas WHERE id = ?",
        (drama_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "未找到该短剧"}), 404
    new_value = "否" if row["uploaded"] == "是" else "是"
    uploader_value = session.get("username") if new_value == "是" else None
    db.execute(
        "UPDATE dramas SET uploaded = ?, uploader = ? WHERE id = ?",
        (new_value, uploader_value, drama_id),
    )
    db.commit()
    return jsonify({"id": drama_id, "uploaded": new_value, "uploader": uploader_value})


@app.route("/api/companies", methods=["GET"])
@login_required
def list_companies():
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT company FROM dramas WHERE company IS NOT NULL AND company <> '' ORDER BY company ASC"
    ).fetchall()
    return jsonify([row["company"] for row in rows])


@app.route("/api/export", methods=["GET"])
@login_required
def export_excel():
    clauses, params = build_filter_clause(request.args)
    where_sql = " AND ".join(["1=1"] + clauses)
    db = get_db()
    rows = db.execute(
        f"SELECT * FROM dramas WHERE {where_sql} ORDER BY date DESC",
        params,
    ).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "短剧数据"
    ws.append(EXPORT_HEADERS)

    for row in rows:
        ws.append(
            [
                row["date"],
                row["original_name"],
                row["new_name"],
                row["episodes"],
                row["duration"],
                row["review_passed"],
                row["uploaded"],
                row["materials"],
                row["promo_text"],
                row["description"],
                row["company"],
                row["uploader"],
                row["remark1"],
                row["remark2"],
                row["remark3"],
            ]
        )

    for column_cells in ws.columns:
        max_length = 0
        column = column_cells[0].column_letter
        for cell in column_cells:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except ValueError:
                continue
        ws.column_dimensions[column].width = min(max_length + 2, 40)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    filename = f"短剧数据_{datetime.date.today().isoformat()}.xlsx"
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/import", methods=["POST"])
@login_required
@admin_required
def import_excel():
    uploaded_file = request.files.get("file")
    if not uploaded_file:
        return jsonify({"error": "请上传Excel文件"}), 400
    try:
        workbook = load_workbook(uploaded_file, data_only=True)
    except Exception:
        return jsonify({"error": "无法读取Excel文件"}), 400

    sheet = workbook.active
    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), [])
    header_map = {}
    for idx, header in enumerate(header_row, start=1):
        header_text = (header or "").strip()
        if header_text in HEADER_MAP:
            header_map[idx] = HEADER_MAP[header_text]
    if "original_name" not in header_map.values() or "new_name" not in header_map.values():
        return jsonify({"error": "Excel缺少必要的原剧名或新剧名列"}), 400

    db = get_db()
    existing_rows = db.execute("SELECT original_name, new_name FROM dramas").fetchall()
    existing_pairs = {(row["original_name"], row["new_name"]) for row in existing_rows}
    existing_new_names: dict[str, set[str]] = {}
    for row in existing_rows:
        existing_new_names.setdefault(row["original_name"], set()).add(row["new_name"])

    new_count = 0
    duplicate_count = 0
    conflicts: list[dict[str, str]] = []

    for row in sheet.iter_rows(min_row=2, values_only=True):
        row_data = {}
        empty = True
        for idx, value in enumerate(row, start=1):
            if idx in header_map:
                row_data[header_map[idx]] = value
                if value not in (None, ""):
                    empty = False
        if empty:
            continue
        normalized = normalize_row(row_data)
        original_name = normalized.get("original_name")
        new_name = normalized.get("new_name")
        if not original_name or not new_name:
            continue
        pair = (original_name, new_name)
        if pair in existing_pairs:
            duplicate_count += 1
            continue
        if (
            original_name in existing_new_names
            and new_name not in existing_new_names[original_name]
        ):
            conflicts.append(
                {
                    "original_name": original_name,
                    "new_name": new_name,
                    "existing_new_name": next(iter(existing_new_names[original_name])),
                }
            )
            continue
        insert_payload = {
            "date": normalized.get("date"),
            "original_name": original_name,
            "new_name": new_name,
            "episodes": normalized.get("episodes"),
            "duration": normalized.get("duration"),
            "review_passed": normalized.get("review_passed", "否"),
            "uploaded": normalized.get("uploaded", "否"),
            "materials": normalized.get("materials"),
            "promo_text": normalized.get("promo_text"),
            "description": normalized.get("description"),
            "company": normalized.get("company"),
            "remark1": normalized.get("remark1"),
            "remark2": normalized.get("remark2"),
            "remark3": normalized.get("remark3"),
        }
        placeholders = ", ".join([f":{k}" for k in insert_payload.keys()])
        columns = ", ".join(insert_payload.keys())
        db.execute(
            f"INSERT INTO dramas ({columns}) VALUES ({placeholders})",
            insert_payload,
        )
        db.commit()
        existing_pairs.add(pair)
        existing_new_names.setdefault(original_name, set()).add(new_name)
        new_count += 1

    return jsonify(
        {
            "new_count": new_count,
            "duplicate_count": duplicate_count,
            "conflicts": conflicts,
        }
    )


@app.route("/api/users", methods=["GET"])
@login_required
@admin_required
def list_users():
    db = get_db()
    rows = db.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/users", methods=["POST"])
@login_required
@admin_required
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") or "user"
    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if role not in {"admin", "user"}:
        role = "user"
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), role),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "用户名已存在"}), 400
    return jsonify({"message": "创建用户成功"}), 201


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_user(user_id: int):
    if session.get("user_id") == user_id:
        return jsonify({"error": "不能删除当前登录用户"}), 400
    db = get_db()
    result = db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    if result.rowcount == 0:
        return jsonify({"error": "未找到该用户"}), 404
    return jsonify({"message": "删除成功"})


@app.route("/api/users/<int:user_id>/password", methods=["PUT"])
@login_required
@admin_required
def change_password(user_id: int):
    data = request.get_json(silent=True) or {}
    new_password = (data.get("new_password") or "").strip()
    if len(new_password) < 4:
        return jsonify({"error": "新密码至少4位"}), 400
    db = get_db()
    result = db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), user_id),
    )
    db.commit()
    if result.rowcount == 0:
        return jsonify({"error": "未找到该用户"}), 404
    return jsonify({"message": "密码已更新"})


@app.route("/api/licenses", methods=["GET"])
@login_required
@admin_required
def list_licenses():
    page = max(1, int(request.args.get("page", 1) or 1))
    page_size = int(request.args.get("page_size", 10) or 10)
    page_size = min(100, max(1, page_size))
    sort_by = str(
        request.args.get("sort_by", LICENSE_LIST_DEFAULT_SORT_FIELD) or LICENSE_LIST_DEFAULT_SORT_FIELD
    ).strip()
    sort_dir = str(
        request.args.get("sort_dir", LICENSE_LIST_DEFAULT_SORT_DIR) or LICENSE_LIST_DEFAULT_SORT_DIR
    ).strip().lower()
    if sort_by not in LICENSE_LIST_SORTABLE_FIELDS:
        sort_by = LICENSE_LIST_DEFAULT_SORT_FIELD
    if sort_dir not in {"asc", "desc"}:
        sort_dir = LICENSE_LIST_DEFAULT_SORT_DIR

    clauses, params = build_license_filter_clause(request.args)
    where_sql = " AND ".join(["1=1"] + clauses)

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS cnt FROM licenses WHERE {where_sql}",
        params,
    ).fetchone()[0]
    offset = (page - 1) * page_size
    rows = db.execute(
        f"""
        SELECT *
        FROM licenses
        WHERE {where_sql}
        ORDER BY {LICENSE_LIST_SORTABLE_FIELDS[sort_by]} {sort_dir.upper()}, id DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()
    pages = math.ceil(total / page_size) if total else 1
    return jsonify(
        {
            "items": [serialize_license_row(db, row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }
    )


@app.route("/api/licenses", methods=["POST"])
@login_required
@admin_required
def create_license():
    data = request.get_json(silent=True) or {}
    payload, error = sanitize_license_payload(data)
    if error:
        return jsonify({"error": error}), 400
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO licenses (
                license_key, license_key_masked, status, edition, licensee, max_activations, expires_at, notes
            ) VALUES (
                :license_key, :license_key_masked, :status, :edition, :licensee, :max_activations, :expires_at, :notes
            )
            """,
            payload,
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "激活码已存在，请更换后重试"}), 400

    row = db.execute(
        "SELECT * FROM licenses WHERE license_key = ?",
        (payload["license_key"],),
    ).fetchone()
    return jsonify(
        {
            "message": "激活码创建成功",
            "item": serialize_license_row(db, row),
        }
    ), 201


@app.route("/api/licenses/export", methods=["GET"])
@login_required
@admin_required
def export_licenses():
    clauses, params = build_license_filter_clause(request.args)
    where_sql = " AND ".join(["1=1"] + clauses)
    db = get_db()
    rows = db.execute(
        f"""
        SELECT *
        FROM licenses
        WHERE {where_sql}
        ORDER BY created_at DESC, id DESC
        """,
        params,
    ).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "授权码"
    ws.append(LICENSE_EXPORT_HEADERS)

    for row in rows:
        item = serialize_license_row(db, row)
        status_text = "已删除" if item.get("deleted_at") else item.get("status") or ""
        ws.append(
            [
                item.get("license_key") or "",
                item.get("license_key_masked") or "",
                item.get("licensee") or "",
                item.get("edition") or "",
                status_text,
                item.get("max_activations") or 0,
                item.get("active_activations") or 0,
                item.get("total_activations") or 0,
                item.get("expires_at") or "",
                item.get("last_verified_at") or "",
                item.get("notes") or "",
                item.get("created_at") or "",
                item.get("updated_at") or "",
                item.get("deleted_at") or "",
            ]
        )

    for column_cells in ws.columns:
        max_length = 0
        column = column_cells[0].column_letter
        for cell in column_cells:
            try:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            except ValueError:
                continue
        ws.column_dimensions[column].width = min(max_length + 2, 40)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    filename = f"授权码数据_{datetime.date.today().isoformat()}.xlsx"
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/licenses/<int:license_id>/activations", methods=["GET"])
@login_required
@admin_required
def list_license_activations(license_id: int):
    db = get_db()
    license_row = get_license_row(db, license_id, include_deleted=True)
    if not license_row:
        return jsonify({"error": "未找到该激活码"}), 404
    rows = db.execute(
        """
        SELECT *
        FROM license_activations
        WHERE license_id = ?
        ORDER BY activated_at DESC, id DESC
        """,
        (license_id,),
    ).fetchall()
    return jsonify(
        {
            "license": serialize_license_row(db, license_row),
            "items": [serialize_activation_row(row) for row in rows],
        }
    )


@app.route("/api/licenses/<int:license_id>/secret", methods=["GET"])
@login_required
@admin_required
def get_license_secret(license_id: int):
    db = get_db()
    row = get_license_row(db, license_id, include_deleted=True)
    if not row:
        return jsonify({"error": "未找到该激活码"}), 404
    return jsonify(
        {
            "id": row["id"],
            "license_key": row["license_key"],
            "license_key_masked": row["license_key_masked"],
            "licensee": row["licensee"] or "",
            "edition": row["edition"] or "",
            "status": row["status"] or "",
            "deleted_at": row["deleted_at"] or "",
        }
    )


@app.route("/api/licenses/<int:license_id>/disable", methods=["POST"])
@login_required
@admin_required
def disable_license(license_id: int):
    db = get_db()
    row = get_license_row(db, license_id, include_deleted=True)
    if not row:
        return jsonify({"error": "未找到该激活码"}), 404
    ok, message = update_license_status_row(db, row, status="disabled")
    if not ok:
        return jsonify({"error": message}), 400
    db.commit()
    return jsonify({"message": "激活码已停用"})


@app.route("/api/licenses/<int:license_id>/enable", methods=["POST"])
@login_required
@admin_required
def enable_license(license_id: int):
    db = get_db()
    row = get_license_row(db, license_id, include_deleted=True)
    if not row:
        return jsonify({"error": "未找到该激活码"}), 404
    ok, message = update_license_status_row(db, row, status="active")
    if not ok:
        return jsonify({"error": message}), 400
    db.commit()
    return jsonify({"message": "激活码已启用"})


@app.route("/api/licenses/batch-disable", methods=["POST"])
@login_required
@admin_required
def batch_disable_licenses():
    data = request.get_json(silent=True) or {}
    license_ids, error = parse_license_ids_from_payload(data)
    if error:
        return jsonify({"error": error}), 400

    db = get_db()
    rows = get_license_rows_by_ids(db, license_ids, include_deleted=True)
    found_ids = {int(row["id"]) for row in rows}
    missing_ids = [license_id for license_id in license_ids if license_id not in found_ids]
    if missing_ids:
        return jsonify({"error": f"存在未找到的授权码：{', '.join(map(str, missing_ids))}"}), 404

    for row in rows:
        ok, message = update_license_status_row(db, row, status="disabled")
        if not ok:
            db.rollback()
            return jsonify({"error": message}), 400

    db.commit()
    return jsonify({"message": f"已停用 {len(rows)} 条授权码"})


@app.route("/api/licenses/batch-enable", methods=["POST"])
@login_required
@admin_required
def batch_enable_licenses():
    data = request.get_json(silent=True) or {}
    license_ids, error = parse_license_ids_from_payload(data)
    if error:
        return jsonify({"error": error}), 400

    db = get_db()
    rows = get_license_rows_by_ids(db, license_ids, include_deleted=True)
    found_ids = {int(row["id"]) for row in rows}
    missing_ids = [license_id for license_id in license_ids if license_id not in found_ids]
    if missing_ids:
        return jsonify({"error": f"存在未找到的授权码：{', '.join(map(str, missing_ids))}"}), 404

    for row in rows:
        ok, message = update_license_status_row(db, row, status="active")
        if not ok:
            db.rollback()
            return jsonify({"error": message}), 400

    db.commit()
    return jsonify({"message": f"已启用 {len(rows)} 条授权码"})


@app.route("/api/licenses/<int:license_id>/unbind", methods=["POST"])
@login_required
@admin_required
def unbind_license_machine(license_id: int):
    data = request.get_json(silent=True) or {}
    machine_id = str(data.get("machine_id") or "").strip()
    if not machine_id:
        return jsonify({"error": "machine_id 不能为空"}), 400
    db = get_db()
    if not get_license_row(db, license_id):
        return jsonify({"error": "未找到该激活码"}), 404
    result = db.execute(
        """
        UPDATE license_activations
        SET revoked_at = ?
        WHERE license_id = ? AND machine_id = ? AND (revoked_at IS NULL OR revoked_at = '')
        """,
        (
            datetime.datetime.now().isoformat(timespec="seconds"),
            license_id,
            machine_id,
        ),
    )
    db.commit()
    if result.rowcount == 0:
        return jsonify({"error": "未找到可解绑的设备记录"}), 404
    return jsonify({"message": "设备解绑成功"})


@app.route("/api/licenses/<int:license_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_license(license_id: int):
    db = get_db()
    row = get_license_row(db, license_id, include_deleted=True)
    if not row:
        return jsonify({"error": "未找到该激活码"}), 404
    ok, message = soft_delete_license_row(db, row, deleted_by=session.get("user_id"))
    if not ok:
        return jsonify({"error": message}), 400
    db.commit()
    return jsonify({"message": "激活码已删除"})


@app.route("/api/licenses/batch-delete", methods=["POST"])
@login_required
@admin_required
def batch_delete_licenses():
    data = request.get_json(silent=True) or {}
    license_ids, error = parse_license_ids_from_payload(data)
    if error:
        return jsonify({"error": error}), 400

    db = get_db()
    rows = get_license_rows_by_ids(db, license_ids, include_deleted=True)
    found_ids = {int(row["id"]) for row in rows}
    missing_ids = [license_id for license_id in license_ids if license_id not in found_ids]
    if missing_ids:
        return jsonify({"error": f"存在未找到的授权码：{', '.join(map(str, missing_ids))}"}), 404

    for row in rows:
        ok, message = soft_delete_license_row(db, row, deleted_by=session.get("user_id"))
        if not ok:
            db.rollback()
            return jsonify({"error": message}), 400

    db.commit()
    return jsonify({"message": f"已删除 {len(rows)} 条授权码"})


@app.route("/api/licenses/<int:license_id>/restore", methods=["POST"])
@login_required
@admin_required
def restore_license(license_id: int):
    db = get_db()
    row = get_license_row(db, license_id, include_deleted=True)
    if not row:
        return jsonify({"error": "未找到该激活码"}), 404
    ok, message = restore_license_row(db, row)
    if not ok:
        return jsonify({"error": message}), 400
    db.commit()
    return jsonify({"message": "激活码已恢复"})


@app.route("/api/licenses/batch-restore", methods=["POST"])
@login_required
@admin_required
def batch_restore_licenses():
    data = request.get_json(silent=True) or {}
    license_ids, error = parse_license_ids_from_payload(data)
    if error:
        return jsonify({"error": error}), 400

    db = get_db()
    rows = get_license_rows_by_ids(db, license_ids, include_deleted=True)
    found_ids = {int(row["id"]) for row in rows}
    missing_ids = [license_id for license_id in license_ids if license_id not in found_ids]
    if missing_ids:
        return jsonify({"error": f"存在未找到的授权码：{', '.join(map(str, missing_ids))}"}), 404

    for row in rows:
        ok, message = restore_license_row(db, row)
        if not ok:
            db.rollback()
            return jsonify({"error": message}), 400

    db.commit()
    return jsonify({"message": f"已恢复 {len(rows)} 条授权码"})


@app.route("/api/remote/clients", methods=["GET"])
@login_required
def list_remote_clients():
    db = get_db()
    user_id = int(session["user_id"])
    role = str(session.get("role") or "user")
    if role == "admin":
        rows = db.execute(
            "SELECT * FROM remote_clients ORDER BY created_at DESC, id DESC"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM remote_clients WHERE owner_user_id = ? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
    return jsonify([serialize_remote_client(row) for row in rows])


@app.route("/api/remote/clients", methods=["POST"])
@login_required
def create_remote_client():
    data = request.get_json(silent=True) or {}
    client_name = str(data.get("client_name") or "").strip() or "默认设备"
    db = get_db()
    client_id = generate_remote_client_id()
    client_token = generate_remote_client_token()
    now = now_iso()
    db.execute(
        """
        INSERT INTO remote_clients (
            client_id, client_name, client_token_hash, owner_user_id, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'offline', ?, ?)
        """,
        (
            client_id,
            client_name,
            hash_remote_client_token(client_token),
            int(session["user_id"]),
            now,
            now,
        ),
    )
    db.commit()
    row = get_remote_client_by_public_id(db, client_id)
    return jsonify(
        {
            "item": serialize_remote_client(row),
            "client_token": client_token,
        }
    ), 201


@app.route("/api/remote/conversations", methods=["GET"])
@login_required
def list_remote_conversations():
    db = get_db()
    user_id = int(session["user_id"])
    role = str(session.get("role") or "user")
    client_id = str(request.args.get("client_id") or "").strip()
    params: list[Any] = []
    sql = (
        "SELECT c.*, rc.client_id AS public_client_id, rc.client_name AS client_name "
        "FROM remote_conversations c "
        "JOIN remote_clients rc ON rc.id = c.remote_client_id "
    )
    conditions: list[str] = []
    if role != "admin":
        conditions.append("c.owner_user_id = ?")
        params.append(user_id)
    if client_id:
        conditions.append("rc.client_id = ?")
        params.append(client_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY c.updated_at DESC, c.id DESC"
    rows = db.execute(sql, params).fetchall()
    items = []
    for row in rows:
        item = serialize_remote_conversation(row)
        item["client_id"] = row["public_client_id"]
        item["client_name"] = row["client_name"]
        items.append(item)
    return jsonify(items)


@app.route("/api/remote/conversations", methods=["POST"])
@login_required
def create_remote_conversation():
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"error": "client_id 不能为空"}), 400
    db = get_db()
    client_row = get_remote_client_by_public_id(db, client_id)
    if not client_row:
        return jsonify({"error": "未找到对应客户端"}), 404
    user_id = int(session["user_id"])
    role = str(session.get("role") or "user")
    if role != "admin" and int(client_row["owner_user_id"]) != user_id:
        return jsonify({"error": "权限不足"}), 403
    title = str(data.get("title") or "").strip() or f"{client_row['client_name']} 会话"
    now = now_iso()
    db.execute(
        """
        INSERT INTO remote_conversations (remote_client_id, owner_user_id, title, status, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?)
        """,
        (client_row["id"], client_row["owner_user_id"], title, now, now),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM remote_conversations WHERE id = last_insert_rowid()"
    ).fetchone()
    return jsonify(serialize_remote_conversation(row)), 201


@app.route("/api/remote/conversations/<int:conversation_id>/messages", methods=["GET"])
@login_required
def list_remote_messages(conversation_id: int):
    db = get_db()
    user_id = int(session["user_id"])
    role = str(session.get("role") or "user")
    conversation_row = ensure_remote_conversation_access(db, conversation_id, user_id, role)
    if not conversation_row:
        return jsonify({"error": "未找到会话或权限不足"}), 404
    message_rows = db.execute(
        """
        SELECT *
        FROM remote_messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,),
    ).fetchall()
    items = []
    for row in message_rows:
        attachments = db.execute(
            "SELECT * FROM remote_attachments WHERE message_id = ? ORDER BY id ASC",
            (row["id"],),
        ).fetchall()
        items.append(serialize_remote_message(row, [serialize_remote_attachment(item) for item in attachments]))
    return jsonify(items)


@app.route("/api/remote/conversations/<int:conversation_id>/messages", methods=["POST"])
@login_required
def create_remote_message(conversation_id: int):
    db = get_db()
    user_id = int(session["user_id"])
    role = str(session.get("role") or "user")
    conversation_row = ensure_remote_conversation_access(db, conversation_id, user_id, role)
    if not conversation_row:
        return jsonify({"error": "未找到会话或权限不足"}), 404
    data = request.get_json(silent=True) or {}
    message_type = str(data.get("message_type") or "text").strip().lower()
    if message_type not in REMOTE_MESSAGE_TYPE_VALUES:
        return jsonify({"error": "message_type 不支持"}), 400
    content_text = str(data.get("content_text") or "").strip()
    payload_json = json.dumps(data.get("payload") or {}, ensure_ascii=False) if data.get("payload") is not None else None
    status = "pending" if message_type == "command" else "success"
    now = now_iso()
    db.execute(
        """
        INSERT INTO remote_messages (
            conversation_id, sender_type, sender_user_id, remote_client_id, message_type, content_text, payload_json, status, created_at, updated_at
        ) VALUES (?, 'user', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            user_id,
            conversation_row["remote_client_id"],
            message_type,
            content_text or None,
            payload_json,
            status,
            now,
            now,
        ),
    )
    db.execute(
        "UPDATE remote_conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM remote_messages WHERE id = last_insert_rowid()"
    ).fetchone()
    return jsonify(serialize_remote_message(row)), 201


@app.route("/client-api/remote/register", methods=["POST"])
def client_register_remote():
    db, client_row = require_remote_client()
    if client_row is None:
        return jsonify({"ok": False, "message": "client_id 或 client_token 无效"}), 401
    data = request.get_json(silent=True) or {}
    now = now_iso()
    db.execute(
        """
        UPDATE remote_clients
        SET machine_id = ?, device_name = ?, app_version = ?, workspace_path = ?, status = 'online', last_seen_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            str(data.get("machine_id") or "").strip() or None,
            str(data.get("device_name") or "").strip() or None,
            str(data.get("app_version") or "").strip() or None,
            str(data.get("workspace_path") or "").strip() or None,
            now,
            now,
            client_row["id"],
        ),
    )
    db.commit()
    refreshed = get_remote_client_by_public_id(db, client_row["client_id"])
    return jsonify({"ok": True, "data": serialize_remote_client(refreshed)})


@app.route("/client-api/remote/poll", methods=["GET"])
def client_poll_remote():
    db, client_row = require_remote_client()
    if client_row is None:
        return jsonify({"ok": False, "message": "client_id 或 client_token 无效"}), 401
    now = now_iso()
    db.execute(
        "UPDATE remote_clients SET status = 'online', last_seen_at = ?, updated_at = ? WHERE id = ?",
        (now, now, client_row["id"]),
    )
    db.commit()
    row = db.execute(
        """
        SELECT m.*
        FROM remote_messages m
        JOIN remote_conversations c ON c.id = m.conversation_id
        WHERE c.remote_client_id = ? AND m.message_type = 'command' AND m.status = 'pending'
        ORDER BY m.id ASC
        LIMIT 1
        """,
        (client_row["id"],),
    ).fetchone()
    if not row:
        return jsonify({"ok": True, "data": None})
    db.execute(
        "UPDATE remote_messages SET status = 'sent', updated_at = ? WHERE id = ?",
        (now, row["id"]),
    )
    db.execute(
        "UPDATE remote_conversations SET updated_at = ? WHERE id = ?",
        (now, row["conversation_id"]),
    )
    db.commit()
    attachments = db.execute(
        "SELECT * FROM remote_attachments WHERE message_id = ? ORDER BY id ASC",
        (row["id"],),
    ).fetchall()
    refreshed = db.execute("SELECT * FROM remote_messages WHERE id = ?", (row["id"],)).fetchone()
    return jsonify({"ok": True, "data": serialize_remote_message(refreshed, [serialize_remote_attachment(item) for item in attachments])})


@app.route("/client-api/remote/messages/<int:message_id>/complete", methods=["POST"])
def client_complete_remote_message(message_id: int):
    db, client_row = require_remote_client()
    if client_row is None:
        return jsonify({"ok": False, "message": "client_id 或 client_token 无效"}), 401
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "success").strip().lower()
    if status not in REMOTE_MESSAGE_STATUS_VALUES:
        status = "success"
    result_json = json.dumps(data.get("result") or {}, ensure_ascii=False)
    row = db.execute(
        """
        SELECT m.*, c.remote_client_id
        FROM remote_messages m
        JOIN remote_conversations c ON c.id = m.conversation_id
        WHERE m.id = ?
        """,
        (message_id,),
    ).fetchone()
    if not row or int(row["remote_client_id"]) != int(client_row["id"]):
        return jsonify({"ok": False, "message": "未找到消息"}), 404
    now = now_iso()
    db.execute(
        "UPDATE remote_messages SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
        (status, result_json, now, message_id),
    )
    db.execute(
        "UPDATE remote_conversations SET updated_at = ? WHERE id = ?",
        (now, row["conversation_id"]),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/client-api/remote/upload-image", methods=["POST"])
def client_upload_remote_image():
    db, client_row = require_remote_client()
    if client_row is None:
        return jsonify({"ok": False, "message": "client_id 或 client_token 无效"}), 401
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "message": "缺少图片文件"}), 400
    message_text = str(request.form.get("message") or "").strip()
    conversation = get_or_create_remote_conversation(db, client_row, title=f"{client_row['client_name']} 远程会话")
    now = now_iso()
    db.execute(
        """
        INSERT INTO remote_messages (
            conversation_id, sender_type, sender_user_id, remote_client_id, message_type, content_text, payload_json, status, created_at, updated_at
        ) VALUES (?, 'client', NULL, ?, 'image', ?, NULL, 'success', ?, ?)
        """,
        (conversation["id"], client_row["id"], message_text or None, now, now),
    )
    message_row = db.execute("SELECT * FROM remote_messages WHERE id = last_insert_rowid()").fetchone()
    ext = os.path.splitext(upload.filename or "")[1] or ".png"
    stored_name = f"{message_row['id']}_{secrets.token_hex(4)}{ext}"
    stored_path = os.path.join(REMOTE_UPLOAD_DIR, stored_name)
    upload.save(stored_path)
    db.execute(
        """
        INSERT INTO remote_attachments (message_id, file_type, original_name, stored_path, content_type)
        VALUES (?, 'image', ?, ?, ?)
        """,
        (message_row["id"], upload.filename, stored_path, upload.mimetype or "image/png"),
    )
    db.execute(
        "UPDATE remote_conversations SET updated_at = ? WHERE id = ?",
        (now, conversation["id"]),
    )
    db.commit()
    attachment_row = db.execute("SELECT * FROM remote_attachments WHERE id = last_insert_rowid()").fetchone()
    return jsonify(
        {
            "ok": True,
            "data": {
                "message": serialize_remote_message(message_row, [serialize_remote_attachment(attachment_row)]),
            },
        }
    )


@app.route("/api/remote/attachments/<int:attachment_id>", methods=["GET"])
@login_required
def download_remote_attachment(attachment_id: int):
    db = get_db()
    user_id = int(session["user_id"])
    role = str(session.get("role") or "user")
    row = db.execute(
        """
        SELECT a.*, c.owner_user_id
        FROM remote_attachments a
        JOIN remote_messages m ON m.id = a.message_id
        JOIN remote_conversations c ON c.id = m.conversation_id
        WHERE a.id = ?
        """,
        (attachment_id,),
    ).fetchone()
    if not row:
        return jsonify({"error": "未找到附件"}), 404
    if role != "admin" and int(row["owner_user_id"]) != user_id:
        return jsonify({"error": "权限不足"}), 403
    return send_file(
        row["stored_path"],
        mimetype=row["content_type"] or "application/octet-stream",
        as_attachment=False,
        download_name=row["original_name"] or os.path.basename(row["stored_path"]),
    )


@app.route("/api/profile/password", methods=["POST"])
@login_required
def update_profile_password():
    data = request.get_json(silent=True) or {}
    current_password = (data.get("current_password") or "").strip()
    new_password = (data.get("new_password") or "").strip()
    if not current_password:
        return jsonify({"error": "当前密码不能为空"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "新密码至少6位"}), 400
    user_id = session.get("user_id")
    db = get_db()
    user = db.execute(
        "SELECT id, password_hash FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not user or not check_password_hash(user["password_hash"], current_password):
        return jsonify({"error": "当前密码错误"}), 400
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), user["id"]),
    )
    db.commit()
    return jsonify({"message": "密码修改成功"})


def build_filter_clause(args):
    clauses: list[str] = []
    params: list[object] = []
    drama_id = (args.get("id") or "").strip()
    if drama_id.isdigit():
        clauses.append("id = ?")
        params.append(int(drama_id))
    search = (args.get("search") or "").strip()
    if search:
        like = f"%{search}%"
        clauses.append("(original_name LIKE ? OR new_name LIKE ?)")
        params.extend([like, like])
    company = (args.get("company") or "").strip()
    if company:
        clauses.append("company = ?")
        params.append(company)
    review_passed = (args.get("review_passed") or "").strip()
    if review_passed in ALLOWED_FLAGS:
        clauses.append("review_passed = ?")
        params.append(review_passed)
    uploaded = (args.get("uploaded") or "").strip()
    if uploaded in ALLOWED_FLAGS:
        clauses.append("uploaded = ?")
        params.append(uploaded)
    date_from = (args.get("date_from") or "").strip()
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    date_to = (args.get("date_to") or "").strip()
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    hide_quick_add = (args.get("hide_quick_add") or "").strip()
    if hide_quick_add == "1":
        clauses.append("(source IS NULL OR source != 'quick_add')")
    return clauses, params


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def sanitize_drama_payload(data: dict) -> tuple[dict, str | None]:
    payload: dict[str, object | None] = {}
    original_name = (data.get("original_name") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    if not original_name or not new_name:
        return payload, "原剧名和新剧名不能为空"

    payload["original_name"] = original_name
    payload["new_name"] = new_name

    date_value = data.get("date")
    if isinstance(date_value, (datetime.date, datetime.datetime)):
        payload["date"] = date_value.strftime("%Y-%m-%d")
    else:
        payload["date"] = (date_value or "").strip() or None

    payload["episodes"] = to_int_or_none(data.get("episodes"))
    payload["duration"] = to_int_or_none(data.get("duration"))
    payload["review_passed"] = normalize_flag(data.get("review_passed"))
    payload["uploaded"] = normalize_flag(data.get("uploaded"))
    payload["materials"] = (data.get("materials") or "").strip() or None
    payload["promo_text"] = (data.get("promo_text") or "").strip() or None
    payload["description"] = (data.get("description") or "").strip() or None
    payload["company"] = (data.get("company") or "").strip() or None
    payload["remark1"] = (data.get("remark1") or "").strip() or None
    payload["remark2"] = (data.get("remark2") or "").strip() or None
    payload["remark3"] = (data.get("remark3") or "").strip() or None
    for key in ("remark1", "remark2", "remark3"):
        if payload[key] and len(payload[key]) > 200:
            payload[key] = payload[key][:200]
    return payload, None


def normalize_row(row_data: dict) -> dict:
    normalized = {}
    date_value = row_data.get("date")
    if isinstance(date_value, datetime.datetime):
        normalized["date"] = date_value.strftime("%Y-%m-%d")
    elif isinstance(date_value, datetime.date):
        normalized["date"] = date_value.strftime("%Y-%m-%d")
    else:
        normalized["date"] = (str(date_value).strip() if date_value else None)
    normalized["original_name"] = (row_data.get("original_name") or "").strip()
    normalized["new_name"] = (row_data.get("new_name") or "").strip()
    normalized["episodes"] = to_int_or_none(row_data.get("episodes"))
    normalized["duration"] = to_int_or_none(row_data.get("duration"))
    normalized["review_passed"] = normalize_flag(row_data.get("review_passed"))
    normalized["uploaded"] = normalize_flag(row_data.get("uploaded"))
    normalized["materials"] = normalize_text(row_data.get("materials"))
    normalized["promo_text"] = normalize_text(row_data.get("promo_text"))
    normalized["description"] = normalize_text(row_data.get("description"))
    normalized["company"] = normalize_text(row_data.get("company"))
    normalized["remark1"] = normalize_text(row_data.get("remark1"))
    normalized["remark2"] = normalize_text(row_data.get("remark2"))
    normalized["remark3"] = normalize_text(row_data.get("remark3"))
    for key in ("remark1", "remark2", "remark3"):
        if normalized[key] and len(normalized[key]) > 200:
            normalized[key] = normalized[key][:200]
    return normalized


def normalize_text(value):
    if value is None:
        return None
    return str(value).strip() or None


def normalize_flag(value):
    if isinstance(value, str) and value.strip() == "是":
        return "是"
    return "否"


def to_int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
