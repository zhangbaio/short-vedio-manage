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
    # 榜单变动监控: 当前快照状态 / 变动日志 / 配置
    c.execute("""CREATE TABLE IF NOT EXISTS hg_rank_state(
        board TEXT NOT NULL, series_id TEXT NOT NULL, rank INTEGER, title TEXT,
        PRIMARY KEY(board, series_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS hg_rank_change(
        id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT, checked_at TEXT,
        change_type TEXT, series_id TEXT, title TEXT, old_rank INTEGER, new_rank INTEGER)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rank_change ON hg_rank_change(board, checked_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS hg_rank_cfg(
        id INTEGER PRIMARY KEY CHECK(id=1), interval_min INTEGER DEFAULT 120,
        enabled INTEGER DEFAULT 1, last_check TEXT, last_status TEXT)""")
    c.execute("INSERT OR IGNORE INTO hg_rank_cfg(id,interval_min,enabled,last_check,last_status) VALUES(1,120,1,'','')")
    # 上新监控: 按 体裁×剧 存7天内上新剧集; is_new=1且first_seen为今日 => 今日上新
    c.execute("""CREATE TABLE IF NOT EXISTS hg_new_seen(
        genre TEXT NOT NULL, series_id TEXT NOT NULL, title TEXT, cover TEXT,
        episode_cnt INTEGER, score TEXT, play_cnt INTEGER, category TEXT, intro TEXT,
        first_seen TEXT, last_seen TEXT, in_window INTEGER DEFAULT 1, is_new INTEGER DEFAULT 0,
        PRIMARY KEY(genre, series_id))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_new_seen ON hg_new_seen(genre, in_window, first_seen)")
    c.execute("""CREATE TABLE IF NOT EXISTS hg_new_cfg(
        id INTEGER PRIMARY KEY CHECK(id=1), interval_min INTEGER DEFAULT 3,
        enabled INTEGER DEFAULT 1, last_check TEXT, last_status TEXT)""")
    c.execute("INSERT OR IGNORE INTO hg_new_cfg(id,interval_min,enabled,last_check,last_status) VALUES(1,3,1,'','')")
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


# ============ 榜单变动监控 ============
RANK_SNAPSHOT_LIMIT = 100  # 每次快照取的榜单条数


def rank_cfg_get():
    c = _db()
    try:
        r = c.execute("SELECT interval_min, enabled, last_check, last_status FROM hg_rank_cfg WHERE id=1").fetchone()
        return dict(r) if r else {"interval_min": 120, "enabled": 1, "last_check": "", "last_status": ""}
    finally:
        c.close()


def rank_cfg_set(interval_min=None, enabled=None):
    c = _db()
    try:
        if interval_min is not None:
            c.execute("UPDATE hg_rank_cfg SET interval_min=? WHERE id=1", (max(5, int(interval_min)),))
        if enabled is not None:
            c.execute("UPDATE hg_rank_cfg SET enabled=? WHERE id=1", (1 if enabled else 0,))
        c.commit()
        return True
    finally:
        c.close()


def _rank_check_board(board, conn, now):
    """快照单个榜单并与上次状态对比, 记录变动。返回各类变动计数。"""
    items = H.rank(board, limit=RANK_SNAPSHOT_LIMIT)
    cur = {}  # series_id -> (rank, title)
    for i, it in enumerate(items):
        sid = str(it.get("series_id") or "")
        if sid:
            cur[sid] = (i + 1, it.get("title", ""))
    prev = {r["series_id"]: (r["rank"], r["title"])
            for r in conn.execute("SELECT series_id, rank, title FROM hg_rank_state WHERE board=?", (board,))}
    counts = {"new": 0, "up": 0, "down": 0, "drop": 0}
    is_baseline = not prev  # 首次无历史: 仅建立基线, 不刷一堆"新进"
    if not is_baseline:
        for sid, (rk, title) in cur.items():
            if sid not in prev:
                conn.execute("INSERT INTO hg_rank_change(board,checked_at,change_type,series_id,title,old_rank,new_rank) VALUES(?,?,?,?,?,?,?)",
                             (board, now, "new", sid, title, None, rk))
                counts["new"] += 1
            else:
                old_rk = prev[sid][0]
                if rk < old_rk:
                    conn.execute("INSERT INTO hg_rank_change(board,checked_at,change_type,series_id,title,old_rank,new_rank) VALUES(?,?,?,?,?,?,?)",
                                 (board, now, "up", sid, title, old_rk, rk))
                    counts["up"] += 1
                elif rk > old_rk:
                    conn.execute("INSERT INTO hg_rank_change(board,checked_at,change_type,series_id,title,old_rank,new_rank) VALUES(?,?,?,?,?,?,?)",
                                 (board, now, "down", sid, title, old_rk, rk))
                    counts["down"] += 1
        for sid, (rk, title) in prev.items():
            if sid not in cur:
                conn.execute("INSERT INTO hg_rank_change(board,checked_at,change_type,series_id,title,old_rank,new_rank) VALUES(?,?,?,?,?,?,?)",
                             (board, now, "drop", sid, title, rk, None))
                counts["drop"] += 1
    # 用当前快照替换状态
    conn.execute("DELETE FROM hg_rank_state WHERE board=?", (board,))
    conn.executemany("INSERT INTO hg_rank_state(board,series_id,rank,title) VALUES(?,?,?,?)",
                     [(board, sid, rk, t) for sid, (rk, t) in cur.items()])
    counts["total"] = len(cur)
    counts["baseline"] = is_baseline
    return counts


def rank_run_checks(boards=None):
    """对指定(默认全部)榜单执行一次检查; 写变动日志并更新状态/配置时间。"""
    boards = boards or list(H.RANK_BOARDS)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    summary = {}
    c = _db()
    try:
        for b in boards:
            try:
                summary[b] = _rank_check_board(b, c, now)
                c.commit()
            except Exception as e:  # 单榜失败不影响其它
                summary[b] = {"error": str(e)[:200]}
        c.execute("UPDATE hg_rank_cfg SET last_check=?, last_status=? WHERE id=1",
                  (now, "ok"))
        c.commit()
    finally:
        c.close()
    return {"checked_at": now, "summary": summary}


def rank_changes(board=None, limit=100):
    c = _db()
    try:
        if board:
            rows = c.execute("SELECT * FROM hg_rank_change WHERE board=? ORDER BY id DESC LIMIT ?",
                             (board, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM hg_rank_change ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ============ 上新监控(7天内上新 + 今日上新派生) ============
NEW_SNAPSHOT_LIMIT = 200  # 每体裁每次抓取的上新条数上限


def new_cfg_get():
    c = _db()
    try:
        r = c.execute("SELECT interval_min, enabled, last_check, last_status FROM hg_new_cfg WHERE id=1").fetchone()
        return dict(r) if r else {"interval_min": 3, "enabled": 1, "last_check": "", "last_status": ""}
    finally:
        c.close()


def new_cfg_set(interval_min=None, enabled=None):
    c = _db()
    try:
        if interval_min is not None:
            c.execute("UPDATE hg_new_cfg SET interval_min=? WHERE id=1", (max(1, int(interval_min)),))
        if enabled is not None:
            c.execute("UPDATE hg_new_cfg SET enabled=? WHERE id=1", (1 if enabled else 0,))
        c.commit()
        return True
    finally:
        c.close()


def _new_check_genre(genre, conn, now):
    """增量抓取该体裁7天内上新。优化: 列表按上线时间倒序, 传 stop_ids=已监控集合,
    一旦命中已存剧即停止翻页(省签名请求)。只插入全新剧并持久化(已监控的不再请求/不再处理)。
    首跑为基线(抓满列表, is_new=0); 之后新增 is_new=1(=新上架)。返回 {fetched,new,baseline}。"""
    existing = {r["series_id"] for r in conn.execute("SELECT series_id FROM hg_new_seen WHERE genre=?", (genre,))}
    baseline = not existing
    items = H.latest(genre, only_today=False, max_items=NEW_SNAPSHOT_LIMIT,
                     stop_ids=(existing if existing else None))
    new_cnt = 0
    for it in items:  # 增量模式下 items 仅为命中已监控剧之前的"全新"剧
        sid = str(it.get("series_id") or "")
        if not sid or sid in existing:
            continue  # 双保险(理论上已被 stop_ids 截断)
        is_new = 0 if baseline else 1
        conn.execute("""INSERT INTO hg_new_seen(genre,series_id,title,cover,episode_cnt,score,play_cnt,
            category,intro,first_seen,last_seen,in_window,is_new) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (genre, sid, it.get("title", ""), it.get("cover", ""), it.get("episode_cnt", 0) or 0,
             str(it.get("score", "")), it.get("play_cnt", 0) or 0, it.get("category", ""),
             (it.get("intro") or ""), now, now, is_new))
        existing.add(sid)
        if is_new:
            new_cnt += 1
    return {"fetched": len(items), "new": new_cnt, "baseline": baseline}


def new_run_checks(genres=None):
    genres = genres or list(H.GENRES)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    summary = {}
    c = _db()
    try:
        for g in genres:
            try:
                summary[g] = _new_check_genre(g, c, now)
                c.commit()
            except Exception as e:
                summary[g] = {"error": str(e)[:200]}
        c.execute("UPDATE hg_new_cfg SET last_check=?, last_status='ok' WHERE id=1", (now,))
        c.commit()
    finally:
        c.close()
    return {"checked_at": now, "summary": summary}


def _new_row_to_item(r, today):
    return {"series_id": r["series_id"], "title": r["title"], "cover": r["cover"],
            "episode_cnt": r["episode_cnt"], "score": r["score"], "play_cnt": r["play_cnt"],
            "category": r["category"], "intro": r["intro"], "first_seen": r["first_seen"],
            "today": bool(r["is_new"] and (r["first_seen"] or "")[:10] == today)}


def new_serve(genre, only_today=False, limit=120):
    """优先查库: 已监控的直接返回; 该体裁从未监控过则现抓一次入库再返回。
    only_today=True 返回今日新增(is_new且first_seen=今日);
    否则返回近7天内首次监控到的(按 first_seen 窗口, 增量模式下不再全量扫描)。
    """
    import datetime
    today = time.strftime("%Y-%m-%d")
    cutoff = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    c = _db()
    try:
        has = c.execute("SELECT 1 FROM hg_new_seen WHERE genre=? LIMIT 1", (genre,)).fetchone()
    finally:
        c.close()
    if not has:
        new_run_checks([genre])  # 首次无库: 现抓一次(并建立基线)
    c = _db()
    try:
        if only_today:
            rows = c.execute("""SELECT * FROM hg_new_seen WHERE genre=? AND is_new=1
                AND substr(first_seen,1,10)=? ORDER BY first_seen DESC LIMIT ?""",
                (genre, today, limit)).fetchall()
        else:
            rows = c.execute("""SELECT * FROM hg_new_seen WHERE genre=? AND substr(first_seen,1,10)>=?
                ORDER BY first_seen DESC LIMIT ?""", (genre, cutoff, limit)).fetchall()
        return [_new_row_to_item(r, today) for r in rows]
    finally:
        c.close()


# ============ 后台定时调度(榜单+上新, 跨 gunicorn worker 原子认领) ============
_monitor_thread_started = False


def _claim_check(table, last):
    """原子认领一次检查: 仅当 last_check 仍为 last 时抢占成功(防多 worker 重复)。"""
    c = _db()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cur = c.execute(f"UPDATE {table} SET last_check=? WHERE id=1 AND COALESCE(last_check,'')=?",
                        (now, last))
        c.commit()
        return cur.rowcount == 1
    finally:
        c.close()


def _due(cfg, now_dt):
    import datetime
    if not cfg.get("enabled"):
        return False
    last = cfg.get("last_check") or ""
    if not last:
        return True
    try:
        dt = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        return (now_dt - dt).total_seconds() >= max(1, int(cfg.get("interval_min") or 120)) * 60
    except Exception:
        return True


def _monitor_loop():
    import datetime
    while True:
        try:
            now_dt = datetime.datetime.now()
            rc = rank_cfg_get()
            if _due(rc, now_dt) and _claim_check("hg_rank_cfg", rc.get("last_check") or ""):
                rank_run_checks()
            nc = new_cfg_get()
            if _due(nc, now_dt) and _claim_check("hg_new_cfg", nc.get("last_check") or ""):
                new_run_checks()
        except Exception:
            pass
        time.sleep(60)  # 每分钟唤醒判断是否到点


def _start_monitor():
    global _monitor_thread_started
    if _monitor_thread_started:
        return
    _monitor_thread_started = True
    import threading
    threading.Thread(target=_monitor_loop, name="hg-monitor", daemon=True).start()


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
    """7天内上新(默认)/今日上新。优先查库(后台监控已入库的不再请求红果);
    库中无该体裁数据时现抓一次并入库。今日上新=监控到的新增剧(is_new且首见为今日)。
    """
    genre = request.args.get("genre", "short_play")
    if genre not in H.GENRES:
        return jsonify({"detail": f"genre必须是 {list(H.GENRES)}"}), 400
    only_today = request.args.get("only_today", "false").lower() == "true"
    try:
        limit = max(1, min(300, int(request.args.get("limit", 120))))
    except ValueError:
        limit = 120
    items = new_serve(genre, only_today, limit)
    cfg = new_cfg_get()
    mode = "今日上新" if only_today else "7天内上新"
    return jsonify({"genre": genre, "name": H.GENRE_NAMES.get(genre), "mode": mode,
                    "only_today": only_today, "count": len(items), "items": items,
                    "interval_min": cfg["interval_min"], "enabled": bool(cfg["enabled"]),
                    "last_check": cfg["last_check"]})


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


# ============ 榜单变动监控接口 ============
@hongguo_bp.get("/rank/changes")
@data_auth
def rank_changes_api():
    """榜单变动记录(新进/上升/下降/掉榜)。可按 board 过滤, limit 默认100。"""
    board = request.args.get("board") or None
    if board and board not in H.RANK_BOARDS:
        return jsonify({"detail": f"board必须是 {list(H.RANK_BOARDS)}"}), 400
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
    except ValueError:
        limit = 100
    cfg = rank_cfg_get()
    return jsonify({"board": board, "interval_min": cfg["interval_min"],
                    "enabled": bool(cfg["enabled"]), "last_check": cfg["last_check"],
                    "changes": rank_changes(board, limit)})


@hongguo_bp.get("/hg/rank/config")
@admin_only
def rank_config_get():
    return jsonify(rank_cfg_get())


@hongguo_bp.post("/hg/rank/config")
@admin_only
def rank_config_set():
    """配置检查间隔(分钟, 最小5)与开关。"""
    iv = request.form.get("interval_min") or request.args.get("interval_min")
    en = request.form.get("enabled") or request.args.get("enabled")
    interval = None
    enabled = None
    if iv is not None:
        try:
            interval = int(iv)
        except ValueError:
            return jsonify({"ok": False, "error": "间隔须为整数(分钟)"}), 400
    if en is not None:
        enabled = en.lower() in ("1", "true", "on", "yes")
    rank_cfg_set(interval_min=interval, enabled=enabled)
    return jsonify({"ok": True, "config": rank_cfg_get()})


@hongguo_bp.post("/hg/rank/check")
@admin_only
def rank_check_now():
    """立即检查一次(可指定 board, 默认全部)并返回变动概要。"""
    board = request.form.get("board") or request.args.get("board")
    boards = [board] if board in H.RANK_BOARDS else None
    return jsonify(rank_run_checks(boards))


# ============ 上新监控管理接口(仅管理员) ============
@hongguo_bp.get("/hg/new/config")
@admin_only
def new_config_get():
    return jsonify(new_cfg_get())


@hongguo_bp.post("/hg/new/config")
@admin_only
def new_config_set():
    """配置上新监控间隔(分钟, 最小5)与开关。"""
    iv = request.form.get("interval_min") or request.args.get("interval_min")
    en = request.form.get("enabled") or request.args.get("enabled")
    interval = None
    enabled = None
    if iv is not None:
        try:
            interval = int(iv)
        except ValueError:
            return jsonify({"ok": False, "error": "间隔须为整数(分钟)"}), 400
    if en is not None:
        enabled = en.lower() in ("1", "true", "on", "yes")
    new_cfg_set(interval_min=interval, enabled=enabled)
    return jsonify({"ok": True, "config": new_cfg_get()})


@hongguo_bp.post("/hg/new/check")
@admin_only
def new_check_now():
    """立即抓取一次上新(可指定 genre, 默认全部)并返回概要。"""
    genre = request.form.get("genre") or request.args.get("genre")
    genres = [genre] if genre in H.GENRES else None
    return jsonify(new_run_checks(genres))


# 模块加载即启动后台定时检查线程(daemon, 同时跑榜单+上新监控, 跨worker原子认领)
try:
    _start_monitor()
except Exception as _e:  # noqa
    print(f"[warn] 监控定时线程启动失败: {_e}")
