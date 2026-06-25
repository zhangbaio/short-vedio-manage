# -*- coding: utf-8 -*-
"""爆剧预测数据层 —— 连接、schema 初始化、通用辅助。

复用 short-vedio-manage 的 dramas.db；新增分析流水线所需的表（移植自
xinge server/internal/store/shortdrama_schema.go 中红果分析相关表）。
single-source 场景省略 hg_id_map（cohort.series_id == metric.series_id）。
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE = os.path.join(HERE, "data", "dramas.db")

# 全局单租户: 所有分析数据归属固定 owner。
OWNER_USER_ID = 0


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(DATABASE, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 30000")
    c.execute("PRAGMA journal_mode = WAL")
    return c


_SCHEMA = [
    # 上新历史 cohort（长期留存, 训练画像）
    """CREATE TABLE IF NOT EXISTS hg_new_cohort (
        series_id TEXT PRIMARY KEY,
        book_id TEXT,
        genre TEXT,
        t0 TEXT NOT NULL,
        title TEXT,
        category TEXT,
        episode_cnt INTEGER DEFAULT 0,
        author TEXT,
        fav_t0 INTEGER DEFAULT 0,
        publish_time TEXT,
        cover TEXT NOT NULL DEFAULT '',
        intro TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'hgnew',
        play_t0 INTEGER DEFAULT 0,
        fetched_at TEXT NOT NULL DEFAULT '',
        created_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cohort_t0 ON hg_new_cohort(t0)",
    "CREATE INDEX IF NOT EXISTS idx_cohort_source_date ON hg_new_cohort(source, genre, t0)",
    # 指标时序快照（按天）
    """CREATE TABLE IF NOT EXISTS hg_metric_snapshot (
        series_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        hot_value INTEGER DEFAULT 0,
        play_cnt INTEGER DEFAULT 0,
        favorite INTEGER DEFAULT 0,
        best_rank INTEGER DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'hgnew',
        PRIMARY KEY(source, series_id, ts)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_snap_sid ON hg_metric_snapshot(series_id)",
    # 爆剧画像权重
    """CREATE TABLE IF NOT EXISTS hongguo_boom_profile (
        profile_date TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'source_specific',
        feature_type TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        candidate_count INTEGER NOT NULL DEFAULT 0,
        hit_count INTEGER NOT NULL DEFAULT 0,
        hit_rate REAL NOT NULL DEFAULT 0,
        lift REAL NOT NULL DEFAULT 0,
        weight REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(source, genre, scope, feature_type, feature_key)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_hongguo_boom_profile_lookup ON hongguo_boom_profile(source, genre, scope, feature_type, lift)",
    # 今日预测
    """CREATE TABLE IF NOT EXISTS hongguo_today_predictions (
        owner_user_id INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL,
        genre TEXT NOT NULL,
        series_id TEXT NOT NULL,
        prediction_date TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        cover TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        intro TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        episode_cnt INTEGER NOT NULL DEFAULT 0,
        hot_value INTEGER NOT NULL DEFAULT 0,
        play_cnt INTEGER NOT NULL DEFAULT 0,
        favorite_count INTEGER NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL DEFAULT '',
        last_seen TEXT NOT NULL DEFAULT '',
        potential_score INTEGER NOT NULL DEFAULT 0,
        level TEXT NOT NULL DEFAULT 'C',
        probability REAL NOT NULL DEFAULT 0,
        score_breakdown_json TEXT NOT NULL DEFAULT '{}',
        profile_reasons_json TEXT NOT NULL DEFAULT '[]',
        history_scope TEXT NOT NULL DEFAULT '',
        borrowed_history INTEGER NOT NULL DEFAULT 0,
        early_signal_score INTEGER NOT NULL DEFAULT 0,
        best_rank INTEGER NOT NULL DEFAULT 0,
        reasons_json TEXT NOT NULL DEFAULT '[]',
        risks_json TEXT NOT NULL DEFAULT '[]',
        recommend_next TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(owner_user_id, source, genre, series_id, prediction_date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_hongguo_today_predictions_lookup ON hongguo_today_predictions(owner_user_id, source, genre, prediction_date, potential_score)",
    # 分析日志
    """CREATE TABLE IF NOT EXISTS hongguo_analysis_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '',
        prediction_date TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL DEFAULT '',
        stage TEXT NOT NULL DEFAULT '',
        level TEXT NOT NULL DEFAULT 'info',
        message TEXT NOT NULL DEFAULT '',
        detail_json TEXT NOT NULL DEFAULT '{}',
        duration_ms INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_hongguo_analysis_logs_lookup ON hongguo_analysis_logs(owner_user_id, source, prediction_date, action, id)",
    # 分析任务配置（单租户单行）
    """CREATE TABLE IF NOT EXISTS hongguo_analysis_task_config (
        owner_user_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        new_sync_enabled INTEGER NOT NULL DEFAULT 1,
        prediction_enabled INTEGER NOT NULL DEFAULT 1,
        metrics_enabled INTEGER NOT NULL DEFAULT 1,
        boom_alert_enabled INTEGER NOT NULL DEFAULT 0,
        new_sync_interval_min INTEGER NOT NULL DEFAULT 30,
        prediction_interval_min INTEGER NOT NULL DEFAULT 30,
        metrics_interval_min INTEGER NOT NULL DEFAULT 30,
        boom_alert_interval_min INTEGER NOT NULL DEFAULT 30,
        boom_alert_min_score INTEGER NOT NULL DEFAULT 60,
        prediction_limit INTEGER NOT NULL DEFAULT 120,
        metrics_limit INTEGER NOT NULL DEFAULT 80,
        sources_json TEXT NOT NULL DEFAULT '["hgnew"]',
        genres_json TEXT NOT NULL DEFAULT '["comic_series","ai_series"]',
        updated_at TEXT NOT NULL DEFAULT ''
    )""",
    # 任务执行状态机
    """CREATE TABLE IF NOT EXISTS hongguo_analysis_task_state (
        owner_user_id INTEGER NOT NULL DEFAULT 0,
        task_type TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '',
        prediction_date TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'idle',
        last_started_at TEXT NOT NULL DEFAULT '',
        last_finished_at TEXT NOT NULL DEFAULT '',
        last_success_at TEXT NOT NULL DEFAULT '',
        duration_ms INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(owner_user_id, task_type, source, genre, prediction_date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_hongguo_analysis_task_state_lookup ON hongguo_analysis_task_state(owner_user_id, prediction_date, task_type, status)",
    # 爆剧预警记录（暂不远程下发, 仅入库展示）
    """CREATE TABLE IF NOT EXISTS hongguo_boom_alerts (
        owner_user_id INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        genre TEXT NOT NULL DEFAULT '',
        series_id TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        cover TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        intro TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        episode_cnt INTEGER NOT NULL DEFAULT 0,
        play_cnt INTEGER NOT NULL DEFAULT 0,
        favorite_count INTEGER NOT NULL DEFAULT 0,
        hot_value INTEGER NOT NULL DEFAULT 0,
        score INTEGER NOT NULL DEFAULT 0,
        level TEXT NOT NULL DEFAULT '',
        probability REAL NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL DEFAULT '',
        reasons_json TEXT NOT NULL DEFAULT '[]',
        risks_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(owner_user_id, source, genre, series_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_hongguo_boom_alerts_lookup ON hongguo_boom_alerts(owner_user_id, source, genre, created_at)",
]


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    c = conn or connect()
    try:
        for stmt in _SCHEMA:
            c.execute(stmt)
        # 给现有 hg_new_seen 补 favorite_count / hot_value 列（存量迁移, 表由红果蓝图建）
        cols = {r[1] for r in c.execute("PRAGMA table_info(hg_new_seen)")} if _table_exists(c, "hg_new_seen") else set()
        if cols:
            if "favorite_count" not in cols:
                c.execute("ALTER TABLE hg_new_seen ADD COLUMN favorite_count INTEGER DEFAULT 0")
            if "hot_value" not in cols:
                c.execute("ALTER TABLE hg_new_seen ADD COLUMN hot_value INTEGER DEFAULT 0")
        c.commit()
    finally:
        if own:
            c.close()


def _table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# ---- 通用辅助 ----

def now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def now_text() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(text, default):
    try:
        v = json.loads(text)
        return v if v is not None else default
    except (TypeError, ValueError):
        return default


def clamp_int(value, lo, hi, fallback):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        value = fallback
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
