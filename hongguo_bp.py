# -*- coding: utf-8 -*-
"""红果短剧 API + 密钥管理 (Flask Blueprint, 并入 short-vedio-manage 统一管理)。

- 数据接口(搜索/榜单/今日上新/取直链/下载/封面): 鉴权=平台登录会话 或 有效 api_key。
- 密钥管理(生成/吊销/删除): 仅管理员(session role=admin)。
- 数据接口逻辑复用 hongguo_core/(导入 hongguo 模块), 签名经环境变量 SIGN_SERVER 连签名后端。

在 app.py 注册: from hongguo_bp import hongguo_bp; app.register_blueprint(hongguo_bp)
依赖环境变量: SIGN_SERVER=http://<签名后端>:8001 (默认 https://hongguo.momotools.top 不可用于签名,
            务必指向真实 sign_server)
"""
import os, sys, io, time, sqlite3, secrets, functools
from flask import (Blueprint, request, jsonify, session, render_template,
                   Response, abort, redirect, stream_with_context, make_response)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "hongguo_core"))
import hongguo as H  # noqa: E402  数据API逻辑

import requests, urllib3  # noqa: E402
urllib3.disable_warnings()

DATABASE = os.path.join(HERE, "data", "dramas.db")

# 封面 HEIC->JPEG
try:
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()
    _IMG_OK = True
except Exception:
    _IMG_OK = False
_img_cache = {}
_IMG_HOSTS = ("fqnovelpic.com", "byteimg.com", "qznovelvod.com", "douyinpic.com", "pstatp.com")

hongguo_bp = Blueprint("hongguo", __name__)


# ============ 密钥存储(dramas.db) ============
# 受统计/额度管控的数据接口(endpoint 短名 -> 中文标签)。/img 免鉴权不计入。
ENDPOINT_LABELS = {
    "search": "搜索",
    "rank": "榜单",
    "latest": "最新上架",
    "episodes": "剧集列表",
    "play": "批量直链",
    "video_url": "单集直链",
    "stream": "在线播放",
    "download": "下载任务",
    "download_status": "下载状态",
}


def _db():
    c = sqlite3.connect(DATABASE)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS hg_api_keys(
        key TEXT PRIMARY KEY, note TEXT, enabled INTEGER DEFAULT 1,
        created TEXT, last_used TEXT, created_by TEXT)""")
    # 接口用量/额度: 按 密钥×接口 记累计调用量(used)与额度(quota, 0=不限)
    c.execute("""CREATE TABLE IF NOT EXISTS hg_key_usage(
        key TEXT NOT NULL, endpoint TEXT NOT NULL,
        used INTEGER DEFAULT 0, quota INTEGER DEFAULT 0, updated TEXT,
        PRIMARY KEY(key, endpoint))""")
    return c


def key_valid(key):
    if not key:
        return False
    c = _db()
    try:
        r = c.execute("SELECT enabled FROM hg_api_keys WHERE key=?", (key,)).fetchone()
        if r and r["enabled"]:
            c.execute("UPDATE hg_api_keys SET last_used=? WHERE key=?",
                      (time.strftime("%Y-%m-%d %H:%M:%S"), key))
            c.commit()
            return True
        return False
    finally:
        c.close()


def key_valid_exists(key):
    """密钥是否存在(不论启用与否),用于额度/用量管理。"""
    if not key:
        return False
    c = _db()
    try:
        return c.execute("SELECT 1 FROM hg_api_keys WHERE key=?", (key,)).fetchone() is not None
    finally:
        c.close()


def key_list():
    c = _db()
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM hg_api_keys ORDER BY created DESC")]
        # 附带每个密钥的总调用量与是否配过额度
        agg = {r["key"]: (r["u"], r["q"]) for r in c.execute(
            "SELECT key, COALESCE(SUM(used),0) u, COALESCE(SUM(quota),0) q FROM hg_key_usage GROUP BY key")}
        for r in rows:
            u, q = agg.get(r["key"], (0, 0))
            r["used_total"] = u
            r["has_quota"] = q > 0
        return rows
    finally:
        c.close()


def key_gen(note, by):
    key = "hg_" + secrets.token_hex(16)
    c = _db()
    try:
        c.execute("INSERT INTO hg_api_keys(key,note,enabled,created,last_used,created_by) VALUES(?,?,1,?,'',?)",
                  (key, note or "", time.strftime("%Y-%m-%d %H:%M:%S"), by or ""))
        c.commit()
        return key
    finally:
        c.close()


def key_set_enabled(key, en):
    c = _db()
    try:
        c.execute("UPDATE hg_api_keys SET enabled=? WHERE key=?", (1 if en else 0, key))
        c.commit()
        return c.total_changes > 0
    finally:
        c.close()


def key_delete(key):
    c = _db()
    try:
        c.execute("DELETE FROM hg_api_keys WHERE key=?", (key,))
        c.execute("DELETE FROM hg_key_usage WHERE key=?", (key,))
        c.commit()
        return c.total_changes > 0
    finally:
        c.close()


# ============ 接口用量/额度 ============
def usage_precheck(key, ep):
    """额度校验(只读, 不计数)。返回 (allowed, message)。
    quota=0 表示不限; used>=quota 时拒绝。
    """
    if ep not in ENDPOINT_LABELS:
        return True, ""  # 未纳入统计的接口直接放行
    c = _db()
    try:
        r = c.execute("SELECT used, quota FROM hg_key_usage WHERE key=? AND endpoint=?",
                      (key, ep)).fetchone()
        used = r["used"] if r else 0
        quota = r["quota"] if r else 0
        if quota and used >= quota:
            return False, f"接口「{ENDPOINT_LABELS[ep]}」额度已用尽({used}/{quota}),请联系管理员重置或加额度"
        return True, ""
    finally:
        c.close()


def usage_incr(key, ep):
    """调用成功后计数 used+1(仅受管接口)。"""
    if ep not in ENDPOINT_LABELS:
        return
    c = _db()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""INSERT INTO hg_key_usage(key,endpoint,used,quota,updated) VALUES(?,?,1,0,?)
                     ON CONFLICT(key,endpoint) DO UPDATE SET used=used+1, updated=excluded.updated""",
                  (key, ep, now))
        c.commit()
    finally:
        c.close()


def usage_for_key(key):
    """返回该密钥所有受管接口的 {endpoint,label,used,quota}(无记录的补 0)。"""
    c = _db()
    try:
        have = {r["endpoint"]: r for r in c.execute(
            "SELECT endpoint, used, quota, updated FROM hg_key_usage WHERE key=?", (key,))}
        out = []
        for ep, label in ENDPOINT_LABELS.items():
            r = have.get(ep)
            out.append({"endpoint": ep, "label": label,
                        "used": (r["used"] if r else 0),
                        "quota": (r["quota"] if r else 0),
                        "updated": (r["updated"] if r else "")})
        return out
    finally:
        c.close()


def quota_set(key, ep, quota):
    """设置某密钥某接口额度(ep='*' 表示全部接口统一设置)。quota<0 视为 0(不限)。"""
    quota = max(0, int(quota))
    eps = list(ENDPOINT_LABELS) if ep == "*" else ([ep] if ep in ENDPOINT_LABELS else [])
    if not eps:
        return False
    c = _db()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for e in eps:
            c.execute("""INSERT INTO hg_key_usage(key,endpoint,used,quota,updated) VALUES(?,?,0,?,?)
                         ON CONFLICT(key,endpoint) DO UPDATE SET quota=excluded.quota, updated=excluded.updated""",
                      (key, e, quota, now))
        c.commit()
        return True
    finally:
        c.close()


def usage_reset(key, ep=None):
    """重置调用量为 0(ep 为空或 '*' 重置该密钥全部接口),保留额度配置。"""
    c = _db()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if ep and ep != "*":
            c.execute("UPDATE hg_key_usage SET used=0, updated=? WHERE key=? AND endpoint=?", (now, key, ep))
        else:
            c.execute("UPDATE hg_key_usage SET used=0, updated=? WHERE key=?", (now, key))
        c.commit()
        return c.total_changes > 0
    finally:
        c.close()


# ============ 鉴权装饰器 ============
def data_auth(view):
    """数据接口: 平台登录会话 或 有效 api_key 之一即可。
    经 api_key 访问时按「密钥×接口」统计调用量并校验额度(平台登录会话不计不限)。
    """
    @functools.wraps(view)
    def w(*a, **k):
        if session.get("user_id"):
            return view(*a, **k)
        key = request.headers.get("x-api-key") or request.args.get("api_key") or ""
        if not key_valid(key):
            return jsonify({"detail": "缺少或无效的 api_key(请配置本地链路密钥)"}), 401
        ep = (request.endpoint or "").split(".")[-1]
        allowed, msg = usage_precheck(key, ep)
        if not allowed:
            return jsonify({"detail": msg, "quota_exceeded": True}), 429
        resp = make_response(view(*a, **k))
        if resp.status_code < 400:  # 仅调用成功才计入额度(上游失败不扣)
            usage_incr(key, ep)
        return resp
    return w


def admin_only(view):
    """密钥管理: 仅管理员。"""
    @functools.wraps(view)
    def w(*a, **k):
        if session.get("role") != "admin":
            return jsonify({"error": "权限不足,仅管理员可管理密钥"}), 403
        return view(*a, **k)
    return w


def page_login(view):
    """页面: 未登录跳转登录页。"""
    @functools.wraps(view)
    def w(*a, **k):
        if not session.get("user_id"):
            return redirect("/login?next=" + request.path)
        return view(*a, **k)
    return w


# ============ 数据接口 ============
def _range(ep, total):
    import re
    if not ep or ep == "all":
        return list(range(1, total + 1))
    m = re.match(r"(\d+)-(\d+)$", ep)
    if m:
        return list(range(int(m.group(1)), int(m.group(2)) + 1))
    return [int(ep)] if ep.isdigit() else []


@hongguo_bp.get("/search")
@data_auth
def search():
    q = request.args.get("q", "")
    try:
        limit = max(1, min(120, int(request.args.get("limit", 40))))
    except ValueError:
        limit = 40
    return jsonify({"query": q, "results": H.search(q, max_items=limit)})


@hongguo_bp.get("/rank")
@data_auth
def rank():
    board = request.args.get("board", "recommend")
    if board not in H.RANK_BOARDS:
        return jsonify({"detail": f"board必须是 {list(H.RANK_BOARDS)}"}), 400
    try:
        limit = max(1, min(200, int(request.args.get("limit", 100))))
    except ValueError:
        limit = 100
    return jsonify({"board": board, "name": H.RANK_NAMES.get(board), "items": H.rank(board, limit)})


@hongguo_bp.get("/latest")
@data_auth
def latest():
    genre = request.args.get("genre", "short_play")
    if genre not in H.GENRES:
        return jsonify({"detail": f"genre必须是 {list(H.GENRES)}"}), 400
    only_today = request.args.get("only_today", "true").lower() != "false"
    limit = int(request.args.get("limit", 120))
    items = H.latest(genre, only_today=only_today, max_items=limit)
    mode = ("今日上新" if only_today else "最新上架") if genre == "short_play" else "7天内上新·最新上架"
    return jsonify({"genre": genre, "name": H.GENRE_NAMES.get(genre), "mode": mode,
                    "only_today": only_today, "count": len(items), "items": items})


@hongguo_bp.get("/episodes")
@data_auth
def episodes():
    meta, eps = H.get_episodes(request.args.get("series_id", ""))
    return jsonify({"meta": meta, "episodes": eps})


@hongguo_bp.get("/play")
@data_auth
def play():
    sid = request.args.get("series_id", "")
    ep = request.args.get("ep", "all")
    meta, eps = H.get_episodes(sid)
    want = set(_range(ep, len(eps)))
    sel = [e for e in eps if (e["index"] or 0) in want]
    urls = H.get_video_urls([e["vid"] for e in sel])
    out = []
    for e in sel:
        info = urls.get(e["vid"], {})
        out.append({"index": e["index"], "vid": e["vid"], "title": e["title"],
                    "duration": e["duration"], "url": info.get("url"),
                    "size": info.get("size"), "definition": info.get("definition")})
    return jsonify({"series_id": sid, "title": meta["title"], "episodes": out})


@hongguo_bp.get("/video_url")
@data_auth
def video_url():
    vid = request.args.get("vid", "")
    info = H.get_video_urls([vid]).get(str(vid)) or {}
    if not info.get("url"):
        return jsonify({"detail": "无直链"}), 404
    return jsonify({"vid": vid, "url": info.get("url"), "backup": info.get("backup"),
                    "size": info.get("size"), "definition": info.get("definition")})


@hongguo_bp.get("/stream")
@data_auth
def stream():
    sid = request.args.get("series_id", "")
    ep = request.args.get("ep", "1")
    meta, eps = H.get_episodes(sid)
    idx = int(ep) if ep.isdigit() else 1
    target = next((e for e in eps if (e["index"] or 0) == idx), None)
    if not target:
        return jsonify({"detail": "集号不存在"}), 404
    info = H.get_video_urls([target["vid"]]).get(target["vid"])
    if not info or not info.get("url"):
        return jsonify({"detail": "无直链"}), 404
    up = requests.get(info["url"], stream=True, verify=False, timeout=60)
    from urllib.parse import quote
    fname = quote(f"{H.sanitize(meta['title'])}_第{idx:03d}集.mp4")
    return Response(stream_with_context(up.iter_content(262144)), mimetype="video/mp4",
                    headers={"Content-Disposition": f"attachment; filename=ep{idx:03d}.mp4; filename*=UTF-8''{fname}"})


@hongguo_bp.get("/download")
@data_auth
def download():
    sid = request.args.get("series_id", "")
    ep = request.args.get("ep", "all")
    cov = request.args.get("ep_covers", "false").lower() == "true"
    tid = H.manager().submit(sid, ep, cov)
    return jsonify({"task_id": tid})


@hongguo_bp.get("/download/status")
@data_auth
def download_status():
    return jsonify(H.manager().status(request.args.get("task_id")))


@hongguo_bp.get("/img")
def img():
    """封面代理(免鉴权,仅字节图片域名)。"""
    from urllib.parse import urlparse
    url = request.args.get("url", "")
    host = urlparse(url).hostname or ""
    if not any(host.endswith(h) for h in _IMG_HOSTS):
        abort(400)
    if url in _img_cache:
        return Response(_img_cache[url], mimetype="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    try:
        raw = requests.get(url, timeout=20, verify=False).content
        if _IMG_OK:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=82); raw = buf.getvalue()
        if len(_img_cache) < 1000:
            _img_cache[url] = raw
        return Response(raw, mimetype="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    except Exception:
        abort(404)


# ============ 页面 ============
@hongguo_bp.get("/ui")
@page_login
def ui_page():
    return render_template("hongguo_browse.html")


@hongguo_bp.get("/hg-keys")
@page_login
def keys_page():
    if session.get("role") != "admin":
        abort(403)
    return render_template("hongguo_keys.html")


# ============ 密钥管理接口(仅管理员) ============
@hongguo_bp.get("/hg/keys/list")
@admin_only
def keys_list():
    return jsonify({"keys": key_list()})


@hongguo_bp.post("/hg/keys/gen")
@admin_only
def keys_gen():
    note = request.form.get("note") or request.args.get("note") or ""
    return jsonify({"ok": True, "key": key_gen(note, session.get("username", ""))})


@hongguo_bp.post("/hg/keys/revoke")
@admin_only
def keys_revoke():
    key = request.form.get("key") or request.args.get("key") or ""
    enable = (request.form.get("enable") or request.args.get("enable") or "false").lower() == "true"
    return jsonify({"ok": key_set_enabled(key, enable)})


@hongguo_bp.post("/hg/keys/delete")
@admin_only
def keys_del():
    key = request.form.get("key") or request.args.get("key") or ""
    return jsonify({"ok": key_delete(key)})


# ============ 接口用量/额度管理(仅管理员) ============
@hongguo_bp.get("/hg/keys/usage")
@admin_only
def keys_usage():
    """某密钥各接口的调用量与额度。"""
    key = request.args.get("key") or ""
    usage = usage_for_key(key)
    return jsonify({"key": key,
                    "usage": usage,
                    "used_total": sum(u["used"] for u in usage)})


@hongguo_bp.post("/hg/keys/quota")
@admin_only
def keys_quota():
    """配置额度: key + endpoint(单接口或 '*' 全部统一) + quota(0=不限)。"""
    key = request.form.get("key") or request.args.get("key") or ""
    ep = request.form.get("endpoint") or request.args.get("endpoint") or ""
    quota = request.form.get("quota") or request.args.get("quota") or "0"
    try:
        quota = int(quota)
    except ValueError:
        return jsonify({"ok": False, "error": "额度须为整数"}), 400
    if not key or not key_valid_exists(key):
        return jsonify({"ok": False, "error": "密钥不存在"}), 404
    return jsonify({"ok": quota_set(key, ep, quota)})


@hongguo_bp.post("/hg/keys/usage/reset")
@admin_only
def keys_usage_reset():
    """重置调用量: key + endpoint(留空或 '*' 重置全部)。额度配置保留。"""
    key = request.form.get("key") or request.args.get("key") or ""
    ep = request.form.get("endpoint") or request.args.get("endpoint") or "*"
    usage_reset(key, ep)
    return jsonify({"ok": True})
