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
                   Response, abort, redirect, stream_with_context)

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
def _db():
    c = sqlite3.connect(DATABASE)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS hg_api_keys(
        key TEXT PRIMARY KEY, note TEXT, enabled INTEGER DEFAULT 1,
        created TEXT, last_used TEXT, created_by TEXT)""")
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


def key_list():
    c = _db()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM hg_api_keys ORDER BY created DESC")]
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
        c.commit()
        return c.total_changes > 0
    finally:
        c.close()


# ============ 鉴权装饰器 ============
def data_auth(view):
    """数据接口: 平台登录会话 或 有效 api_key 之一即可。"""
    @functools.wraps(view)
    def w(*a, **k):
        if session.get("user_id"):
            return view(*a, **k)
        key = request.headers.get("x-api-key") or request.args.get("api_key") or ""
        if key_valid(key):
            return view(*a, **k)
        return jsonify({"detail": "缺少或无效的 api_key(请配置本地链路密钥)"}), 401
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
    return jsonify({"query": q, "results": H.search(q)})


@hongguo_bp.get("/rank")
@data_auth
def rank():
    board = request.args.get("board", "recommend")
    if board not in H.RANK_BOARDS:
        return jsonify({"detail": f"board必须是 {list(H.RANK_BOARDS)}"}), 400
    limit = int(request.args.get("limit", 30))
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
