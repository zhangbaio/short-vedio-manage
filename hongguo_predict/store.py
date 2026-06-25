# -*- coding: utf-8 -*-
"""分析流水线存储层 —— 配置 / 任务状态机 / 日志 / 今日预测 / 爆剧预警 CRUD。

移植自 xinge:
  - store/hongguo_analysis_tasks.go  (配置 + claim/finish 状态机)
  - store/hongguo_boom_alert.go       (预警入库)
  - store/hongguo_today_prediction.go (今日预测入库)
  - api/short_analysis_logs.go        (分析日志)
全局单租户: owner_user_id 固定为 db.OWNER_USER_ID。
"""
from __future__ import annotations

import datetime

from . import db

ALLOWED_SOURCES = {"hgnew", "hglocal", "52api"}
ALLOWED_GENRES = {"comic_series", "ai_series", "short_play"}

_DEFAULT_CONFIG = {
    "enabled": True,
    "new_sync_enabled": True,
    "prediction_enabled": True,
    "metrics_enabled": True,
    "boom_alert_enabled": False,
    "new_sync_interval_min": 30,
    "prediction_interval_min": 30,
    "metrics_interval_min": 30,
    "boom_alert_interval_min": 30,
    "boom_alert_min_score": 60,
    "prediction_limit": 120,
    "metrics_limit": 80,
    "sources": ["hgnew"],
    "genres": ["comic_series", "ai_series"],
}

_TASK_RUNNING_TIMEOUT_MIN = {
    "new_sync": 10, "boom_alert": 10, "metrics_refresh": 12, "today_prediction": 15,
}


def _running_timeout_min(task_type):
    return _TASK_RUNNING_TIMEOUT_MIN.get((task_type or "").strip(), 30)


# ================= 配置 =================

def _normalize_sources(values):
    out, seen = [], set()
    for s in values or []:
        from .profile import normalize_source
        s = normalize_source(s)
        if s in ALLOWED_SOURCES and s not in seen:
            seen.add(s)
            out.append(s)
    return out or ["hgnew"]


def _normalize_genres(values):
    out, seen = [], set()
    for g in values or []:
        g = (g or "").strip()
        if g in ALLOWED_GENRES and g not in seen:
            seen.add(g)
            out.append(g)
    return out or ["comic_series", "ai_series"]


def _normalize_config(cfg):
    cfg = dict(cfg)
    cfg["new_sync_interval_min"] = db.clamp_int(cfg.get("new_sync_interval_min"), 1, 1440, 30)
    cfg["prediction_interval_min"] = db.clamp_int(cfg.get("prediction_interval_min"), 5, 1440, 30)
    cfg["metrics_interval_min"] = db.clamp_int(cfg.get("metrics_interval_min"), 5, 1440, 30)
    cfg["boom_alert_interval_min"] = db.clamp_int(cfg.get("boom_alert_interval_min"), 1, 1440, 30)
    cfg["boom_alert_min_score"] = db.clamp_int(cfg.get("boom_alert_min_score"), 1, 100, 60)
    cfg["prediction_limit"] = db.clamp_int(cfg.get("prediction_limit"), 20, 200, 120)
    cfg["metrics_limit"] = db.clamp_int(cfg.get("metrics_limit"), 1, 200, 80)
    cfg["sources"] = _normalize_sources(cfg.get("sources"))
    cfg["genres"] = _normalize_genres(cfg.get("genres"))
    for k in ("enabled", "new_sync_enabled", "prediction_enabled", "metrics_enabled", "boom_alert_enabled"):
        cfg[k] = bool(cfg.get(k))
    return cfg


def _ensure_config(c):
    cfg = dict(_DEFAULT_CONFIG)
    c.execute(
        """INSERT OR IGNORE INTO hongguo_analysis_task_config(
            owner_user_id, enabled, new_sync_enabled, prediction_enabled, metrics_enabled, boom_alert_enabled,
            new_sync_interval_min, prediction_interval_min, metrics_interval_min, boom_alert_interval_min,
            boom_alert_min_score, prediction_limit, metrics_limit, sources_json, genres_json, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (db.OWNER_USER_ID, 1, 1, 1, 1, 0, 30, 30, 30, 30, 60, 120, 80,
         db.dumps(cfg["sources"]), db.dumps(cfg["genres"]), db.now_iso()))
    c.commit()


def get_config(c=None):
    own = c is None
    c = c or db.connect()
    try:
        _ensure_config(c)
        r = c.execute(
            """SELECT enabled, new_sync_enabled, prediction_enabled, metrics_enabled, boom_alert_enabled,
                      new_sync_interval_min, prediction_interval_min, metrics_interval_min, boom_alert_interval_min,
                      boom_alert_min_score, prediction_limit, metrics_limit, sources_json, genres_json, updated_at
               FROM hongguo_analysis_task_config WHERE owner_user_id=?""", (db.OWNER_USER_ID,)).fetchone()
        cfg = {
            "owner_user_id": db.OWNER_USER_ID,
            "enabled": bool(r[0]), "new_sync_enabled": bool(r[1]), "prediction_enabled": bool(r[2]),
            "metrics_enabled": bool(r[3]), "boom_alert_enabled": bool(r[4]),
            "new_sync_interval_min": r[5], "prediction_interval_min": r[6], "metrics_interval_min": r[7],
            "boom_alert_interval_min": r[8], "boom_alert_min_score": r[9],
            "prediction_limit": r[10], "metrics_limit": r[11],
            "sources": db.loads(r[12], ["hgnew"]), "genres": db.loads(r[13], ["comic_series", "ai_series"]),
            "updated_at": r[14],
        }
        return _normalize_config(cfg) | {"owner_user_id": db.OWNER_USER_ID, "updated_at": r[14]}
    finally:
        if own:
            c.close()


def save_config(patch, c=None):
    own = c is None
    c = c or db.connect()
    try:
        cur = get_config(c)
        merged = dict(cur)
        for k in _DEFAULT_CONFIG:
            if k in patch:
                merged[k] = patch[k]
        cfg = _normalize_config(merged)
        cfg["updated_at"] = db.now_iso()
        c.execute(
            """UPDATE hongguo_analysis_task_config SET
                enabled=?, new_sync_enabled=?, prediction_enabled=?, metrics_enabled=?, boom_alert_enabled=?,
                new_sync_interval_min=?, prediction_interval_min=?, metrics_interval_min=?, boom_alert_interval_min=?,
                boom_alert_min_score=?, prediction_limit=?, metrics_limit=?, sources_json=?, genres_json=?, updated_at=?
               WHERE owner_user_id=?""",
            (int(cfg["enabled"]), int(cfg["new_sync_enabled"]), int(cfg["prediction_enabled"]),
             int(cfg["metrics_enabled"]), int(cfg["boom_alert_enabled"]),
             cfg["new_sync_interval_min"], cfg["prediction_interval_min"], cfg["metrics_interval_min"],
             cfg["boom_alert_interval_min"], cfg["boom_alert_min_score"], cfg["prediction_limit"],
             cfg["metrics_limit"], db.dumps(cfg["sources"]), db.dumps(cfg["genres"]), cfg["updated_at"],
             db.OWNER_USER_ID))
        c.commit()
        return get_config(c)
    finally:
        if own:
            c.close()


# ================= 任务状态机 =================

def _parse_dt(value):
    for layout in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime((value or "")[:19], layout)
        except ValueError:
            continue
    return None


def _get_state(c, task_type, source, genre, prediction_date):
    r = c.execute(
        """SELECT status, last_started_at, last_finished_at, last_success_at, duration_ms, message, updated_at
           FROM hongguo_analysis_task_state
           WHERE owner_user_id=? AND task_type=? AND source=? AND genre=? AND prediction_date=?""",
        (db.OWNER_USER_ID, task_type, source, genre, prediction_date)).fetchone()
    if not r:
        return None
    return {
        "task_type": task_type, "source": source, "genre": genre, "prediction_date": prediction_date,
        "status": r[0], "last_started_at": r[1], "last_finished_at": r[2], "last_success_at": r[3],
        "duration_ms": r[4], "message": r[5], "updated_at": r[6],
    }


def claim_task(c, task_type, source, genre, prediction_date, interval_min):
    """原子认领: 距上次完成/启动不足 interval 则不认领; 正在运行且未超时则不认领。
    返回 True 表示本次成功认领（应执行）。"""
    from .profile import normalize_source
    task_type = (task_type or "").strip()
    source = normalize_source(source)
    genre = (genre or "").strip()
    prediction_date = (prediction_date or "").strip() or db.today()
    interval_min = max(1, int(interval_min or 1))
    now = datetime.datetime.now()
    now_text = now.strftime("%Y-%m-%dT%H:%M:%S")
    c.execute(
        """INSERT OR IGNORE INTO hongguo_analysis_task_state(
            owner_user_id, task_type, source, genre, prediction_date, status, updated_at)
           VALUES(?,?,?,?,?,'idle',?)""",
        (db.OWNER_USER_ID, task_type, source, genre, prediction_date, now_text))
    c.commit()
    state = _get_state(c, task_type, source, genre, prediction_date)
    if state:
        if state["status"] == "running":
            started = _parse_dt(state["last_started_at"])
            if started is not None and (now - started).total_seconds() <= _running_timeout_min(task_type) * 60:
                return False
        last_run = state["last_finished_at"] or state["last_started_at"]
        dt = _parse_dt(last_run)
        if dt is not None and (now - dt).total_seconds() < interval_min * 60:
            return False
    timeout_cut = (now - datetime.timedelta(minutes=_running_timeout_min(task_type))).strftime("%Y-%m-%dT%H:%M:%S")
    cur = c.execute(
        """UPDATE hongguo_analysis_task_state
           SET status='running', last_started_at=?, message='', updated_at=?
           WHERE owner_user_id=? AND task_type=? AND source=? AND genre=? AND prediction_date=?
             AND (status<>'running' OR last_started_at='' OR last_started_at<?)""",
        (now_text, now_text, db.OWNER_USER_ID, task_type, source, genre, prediction_date, timeout_cut))
    c.commit()
    return cur.rowcount == 1


def finish_task(c, task_type, source, genre, prediction_date, status, message, started_at):
    from .profile import normalize_source
    task_type = (task_type or "").strip()
    source = normalize_source(source)
    genre = (genre or "").strip()
    prediction_date = (prediction_date or "").strip() or db.today()
    status = (status or "").strip() or "success"
    now = datetime.datetime.now()
    now_text = now.strftime("%Y-%m-%dT%H:%M:%S")
    duration_ms = 0
    if started_at is not None:
        duration_ms = int((now - started_at).total_seconds() * 1000)
    message = (message or "")[:480]
    if status == "success":
        c.execute(
            """UPDATE hongguo_analysis_task_state
               SET status=?, last_finished_at=?, duration_ms=?, message=?, last_success_at=?, updated_at=?
               WHERE owner_user_id=? AND task_type=? AND source=? AND genre=? AND prediction_date=?""",
            (status, now_text, duration_ms, message, now_text, now_text,
             db.OWNER_USER_ID, task_type, source, genre, prediction_date))
    else:
        c.execute(
            """UPDATE hongguo_analysis_task_state
               SET status=?, last_finished_at=?, duration_ms=?, message=?, updated_at=?
               WHERE owner_user_id=? AND task_type=? AND source=? AND genre=? AND prediction_date=?""",
            (status, now_text, duration_ms, message, now_text,
             db.OWNER_USER_ID, task_type, source, genre, prediction_date))
    c.commit()


def list_task_states(prediction_date="", c=None):
    own = c is None
    c = c or db.connect()
    try:
        where = ["owner_user_id=?"]
        args = [db.OWNER_USER_ID]
        if (prediction_date or "").strip():
            where.append("prediction_date=?")
            args.append(prediction_date.strip())
        out = []
        for r in c.execute(
                """SELECT task_type, source, genre, prediction_date, status, last_started_at, last_finished_at,
                          last_success_at, duration_ms, message, updated_at
                   FROM hongguo_analysis_task_state WHERE """ + " AND ".join(where) +
                " ORDER BY updated_at DESC, task_type, source, genre", args):
            out.append({
                "task_type": r[0], "source": r[1], "genre": r[2], "prediction_date": r[3], "status": r[4],
                "last_started_at": r[5], "last_finished_at": r[6], "last_success_at": r[7],
                "duration_ms": r[8], "message": r[9], "updated_at": r[10],
            })
        return out
    finally:
        if own:
            c.close()


# ================= 分析日志 =================

def add_log(c, source, genre, prediction_date, action, stage, level, message, detail, started_at=None):
    duration_ms = 0
    if started_at is not None:
        duration_ms = int((datetime.datetime.now() - started_at).total_seconds() * 1000)
    c.execute(
        """INSERT INTO hongguo_analysis_logs(
            owner_user_id, source, genre, prediction_date, action, stage, level, message, detail_json, duration_ms, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (db.OWNER_USER_ID, source, genre, prediction_date, action, stage, level, message,
         db.dumps(detail or {}), duration_ms, db.now_iso()))
    c.commit()


def list_logs(limit=80, c=None):
    own = c is None
    c = c or db.connect()
    try:
        limit = db.clamp_int(limit, 1, 500, 80)
        out = []
        for r in c.execute(
                """SELECT source, genre, prediction_date, action, stage, level, message, detail_json, duration_ms, created_at
                   FROM hongguo_analysis_logs WHERE owner_user_id=? ORDER BY id DESC LIMIT ?""",
                (db.OWNER_USER_ID, limit)):
            out.append({
                "source": r[0], "genre": r[1], "prediction_date": r[2], "action": r[3], "stage": r[4],
                "level": r[5], "message": r[6], "detail": db.loads(r[7], {}), "duration_ms": r[8], "created_at": r[9],
            })
        return out
    finally:
        if own:
            c.close()


# ================= 今日预测 =================

def replace_today_predictions(c, source, genre, prediction_date, predictions):
    """覆盖写入某 source+genre+date 的今日预测。predictions: list[dict]。"""
    c.execute(
        """DELETE FROM hongguo_today_predictions
           WHERE owner_user_id=? AND source=? AND genre=? AND prediction_date=?""",
        (db.OWNER_USER_ID, source, genre, prediction_date))
    now = db.now_iso()
    rows = []
    for p in predictions:
        rows.append((
            db.OWNER_USER_ID, source, genre, p["series_id"], prediction_date,
            p.get("title", ""), p.get("cover", ""), p.get("category", ""), p.get("intro", ""), p.get("author", ""),
            int(p.get("episode_cnt", 0) or 0), int(p.get("hot_value", 0) or 0), int(p.get("play_cnt", 0) or 0),
            int(p.get("favorite_count", 0) or 0), p.get("first_seen", ""), p.get("last_seen", ""),
            int(p.get("potential", 0) or 0), p.get("level", "C"), float(p.get("probability", 0) or 0),
            db.dumps(p.get("score_breakdown", {})), db.dumps(p.get("profile_reasons", [])),
            p.get("history_scope", ""), int(bool(p.get("borrowed_history"))),
            int(p.get("early_signal_score", 0) or 0), int(p.get("best_rank", 0) or 0),
            db.dumps(p.get("reasons", [])), db.dumps(p.get("risks", [])), p.get("recommend_next", ""), now))
    c.executemany(
        """INSERT INTO hongguo_today_predictions(
            owner_user_id, source, genre, series_id, prediction_date, title, cover, category, intro, author,
            episode_cnt, hot_value, play_cnt, favorite_count, first_seen, last_seen, potential_score, level,
            probability, score_breakdown_json, profile_reasons_json, history_scope, borrowed_history,
            early_signal_score, best_rank, reasons_json, risks_json, recommend_next, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    c.commit()


def list_today_predictions(prediction_date="", limit=120, c=None):
    own = c is None
    c = c or db.connect()
    try:
        prediction_date = (prediction_date or "").strip() or db.today()
        limit = db.clamp_int(limit, 1, 500, 120)
        out = []
        for r in c.execute(
                """SELECT source, genre, series_id, title, cover, category, intro, author, episode_cnt,
                          hot_value, play_cnt, favorite_count, first_seen, last_seen, potential_score, level,
                          probability, score_breakdown_json, profile_reasons_json, history_scope, borrowed_history,
                          early_signal_score, best_rank, reasons_json, risks_json, recommend_next
                   FROM hongguo_today_predictions
                   WHERE owner_user_id=? AND prediction_date=?
                   ORDER BY potential_score DESC, probability DESC LIMIT ?""",
                (db.OWNER_USER_ID, prediction_date, limit)):
            out.append({
                "source": r[0], "genre": r[1], "series_id": r[2], "title": r[3], "cover": r[4], "category": r[5],
                "intro": r[6], "author": r[7], "episode_cnt": r[8], "hot_value": r[9], "play_cnt": r[10],
                "favorite_count": r[11], "first_seen": r[12], "last_seen": r[13], "potential": r[14], "level": r[15],
                "probability": r[16], "score_breakdown": db.loads(r[17], {}), "profile_reasons": db.loads(r[18], []),
                "history_scope": r[19], "borrowed_history": bool(r[20]), "early_signal_score": r[21],
                "best_rank": r[22], "reasons": db.loads(r[23], []), "risks": db.loads(r[24], []),
                "recommend_next": r[25],
            })
        return out
    finally:
        if own:
            c.close()


# ================= 爆剧预警 =================

def create_boom_alert_if_new(c, alert):
    """INSERT OR IGNORE 写入预警; 已存在则更新指标字段。返回 (created: bool)。"""
    from .profile import normalize_source
    source = normalize_source(alert["source"])
    genre = (alert.get("genre") or "").strip()
    series_id = (alert.get("series_id") or "").strip()
    title = (alert.get("title") or "").strip()
    if not series_id or not title:
        return False
    now = db.now_iso()
    cur = c.execute(
        """INSERT OR IGNORE INTO hongguo_boom_alerts(
            owner_user_id, source, genre, series_id, title, cover, category, intro, author, episode_cnt,
            play_cnt, favorite_count, hot_value, score, level, probability, first_seen, reasons_json, risks_json,
            status, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
        (db.OWNER_USER_ID, source, genre, series_id, title, alert.get("cover", ""), alert.get("category", ""),
         alert.get("intro", ""), alert.get("author", ""), int(alert.get("episode_cnt", 0) or 0),
         int(alert.get("play_cnt", 0) or 0), int(alert.get("favorite_count", 0) or 0),
         int(alert.get("hot_value", 0) or 0), int(alert.get("score", 0) or 0), alert.get("level", ""),
         float(alert.get("probability", 0) or 0), alert.get("first_seen", ""),
         db.dumps(alert.get("reasons", [])), db.dumps(alert.get("risks", [])), now, now))
    created = cur.rowcount == 1
    if not created:
        c.execute(
            """UPDATE hongguo_boom_alerts SET title=?, cover=?, category=?, intro=?, author=?, episode_cnt=?,
                play_cnt=?, favorite_count=?, hot_value=?, score=?, level=?, probability=?, first_seen=?,
                reasons_json=?, risks_json=?, updated_at=?
               WHERE owner_user_id=? AND source=? AND genre=? AND series_id=?""",
            (title, alert.get("cover", ""), alert.get("category", ""), alert.get("intro", ""),
             alert.get("author", ""), int(alert.get("episode_cnt", 0) or 0), int(alert.get("play_cnt", 0) or 0),
             int(alert.get("favorite_count", 0) or 0), int(alert.get("hot_value", 0) or 0),
             int(alert.get("score", 0) or 0), alert.get("level", ""), float(alert.get("probability", 0) or 0),
             alert.get("first_seen", ""), db.dumps(alert.get("reasons", [])), db.dumps(alert.get("risks", [])),
             now, db.OWNER_USER_ID, source, genre, series_id))
    c.commit()
    return created


def list_boom_alerts(limit=50, c=None):
    own = c is None
    c = c or db.connect()
    try:
        limit = db.clamp_int(limit, 1, 500, 50)
        out = []
        for r in c.execute(
                """SELECT source, genre, series_id, title, cover, category, intro, author, episode_cnt,
                          play_cnt, favorite_count, hot_value, score, level, probability, first_seen,
                          reasons_json, risks_json, status, created_at, updated_at
                   FROM hongguo_boom_alerts WHERE owner_user_id=?
                   ORDER BY created_at DESC, updated_at DESC LIMIT ?""", (db.OWNER_USER_ID, limit)):
            out.append({
                "source": r[0], "genre": r[1], "series_id": r[2], "title": r[3], "cover": r[4], "category": r[5],
                "intro": r[6], "author": r[7], "episode_cnt": r[8], "play_cnt": r[9], "favorite_count": r[10],
                "hot_value": r[11], "score": r[12], "level": r[13], "probability": r[14], "first_seen": r[15],
                "reasons": db.loads(r[16], []), "risks": db.loads(r[17], []), "status": r[18],
                "created_at": r[19], "updated_at": r[20],
            })
        return out
    finally:
        if own:
            c.close()
