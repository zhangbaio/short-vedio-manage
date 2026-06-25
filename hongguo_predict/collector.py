# -*- coding: utf-8 -*-
"""数据采集 —— 上新→cohort、指标补齐→时序快照。

复用 hongguo_core(签名/拉取层)。移植自 xinge 的 new_sync / metrics_refresh 任务,
但单源、按天快照。收藏数(favorite)红果上新接口不返回, 由 metrics 任务调
get_episodes().followed_cnt 补齐; hot_value 红果不直接给, 记 0。
"""
from __future__ import annotations

import os
import sys

from . import db

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NEW_SNAPSHOT_LIMIT = 200
SOURCE = "hgnew"  # 单源标签


def _H():
    """惰性导入 hongguo_core 签名/拉取层。延迟到任务执行时, 避免 import 失败拖垮蓝图注册。"""
    core = os.path.join(HERE, "hongguo_core")
    if core not in sys.path:
        sys.path.insert(0, core)
    import hongguo as H  # noqa: E402
    return H


def _upsert_metric_snapshot(c, series_id, snap_date, hot_value, play_cnt, favorite, best_rank, source=SOURCE):
    """按天 upsert 指标快照; 仅当新值>0 时覆盖（与 xinge upsertHongguoMetricSnapshotTx 一致）。"""
    series_id = (series_id or "").strip()
    if not series_id:
        return
    c.execute(
        """INSERT INTO hg_metric_snapshot(series_id, ts, hot_value, play_cnt, favorite, best_rank, source)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(source, series_id, ts) DO UPDATE SET
             hot_value=CASE WHEN excluded.hot_value>0 THEN excluded.hot_value ELSE hg_metric_snapshot.hot_value END,
             play_cnt=CASE WHEN excluded.play_cnt>0 THEN excluded.play_cnt ELSE hg_metric_snapshot.play_cnt END,
             favorite=CASE WHEN excluded.favorite>0 THEN excluded.favorite ELSE hg_metric_snapshot.favorite END,
             best_rank=CASE WHEN excluded.best_rank>0 THEN excluded.best_rank ELSE hg_metric_snapshot.best_rank END""",
        (series_id, snap_date, int(hot_value or 0), int(play_cnt or 0), int(favorite or 0),
         int(best_rank or 0), source))


def run_new_sync(c, genres):
    """抓取各体裁 7 天内上新, 写入 cohort(长期留存) + 当日 play 快照。
    cohort.t0 取首次入库日期(INSERT OR IGNORE 保留最早)。返回 summary。"""
    today = db.today()
    now = db.now_text()
    H = _H()
    summary = {}
    for genre in genres:
        try:
            items = H.latest(genre, only_today=False, max_items=NEW_SNAPSHOT_LIMIT)
        except Exception as e:  # noqa: BLE001
            summary[genre] = {"error": str(e)[:200]}
            continue
        added = 0
        for it in items:
            sid = str(it.get("series_id") or "")
            if not sid:
                continue
            play = int(it.get("play_cnt", 0) or 0)
            cur = c.execute(
                """INSERT OR IGNORE INTO hg_new_cohort(
                    series_id, book_id, genre, t0, title, category, episode_cnt, author, fav_t0, publish_time,
                    cover, intro, source, play_t0, fetched_at, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, sid, genre, today, it.get("title", ""), it.get("category", ""),
                 int(it.get("episode_cnt", 0) or 0), (it.get("copyright") or ""), 0, "",
                 it.get("cover", ""), (it.get("intro") or ""), SOURCE, play, now, now))
            if cur.rowcount == 1:
                added += 1
            else:
                # 已存在: 刷新可变元数据（不动 t0/首次入库时间）
                c.execute(
                    """UPDATE hg_new_cohort SET title=?, category=?, episode_cnt=?, author=?, cover=?, intro=?, fetched_at=?
                       WHERE series_id=?""",
                    (it.get("title", ""), it.get("category", ""), int(it.get("episode_cnt", 0) or 0),
                     (it.get("copyright") or ""), it.get("cover", ""), (it.get("intro") or ""), now, sid))
            # 当日 play 快照（favorite/hot 由 metrics 任务补）
            _upsert_metric_snapshot(c, sid, today, 0, play, 0, _rank_lookup(c, sid))
        c.commit()
        summary[genre] = {"fetched": len(items), "added": added}
    return summary


def _rank_lookup(c, series_id):
    """若红果榜单监控表存在, 取该剧跨榜最优名次作为 best_rank。"""
    try:
        r = c.execute("SELECT COALESCE(MIN(NULLIF(rank,0)),0) FROM hg_rank_state WHERE series_id=?",
                      (series_id,)).fetchone()
        return int(r[0] or 0) if r else 0
    except Exception:  # noqa: BLE001  表不存在(红果蓝图未启用)
        return 0


def run_metrics_refresh(c, genres, limit, lookback_days=14):
    """对近 lookback_days 的 cohort 候选调 get_episodes 补 favorite/play, 写当日快照。
    返回 summary。"""
    today = db.today()
    cutoff = _cutoff(lookback_days)
    placeholders = ",".join("?" for _ in genres) if genres else "''"
    rows = c.execute(
        "SELECT series_id FROM hg_new_cohort WHERE t0>=? AND genre IN (" + placeholders + ") "
        "ORDER BY t0 DESC, series_id DESC LIMIT ?",
        [cutoff, *genres, db.clamp_int(limit, 1, 500, 80)]).fetchall()
    H = _H()
    refreshed = 0
    errors = 0
    for r in rows:
        sid = r[0]
        try:
            meta, _eps = H.get_episodes(sid)  # 网络 I/O: 必须在写事务之外, 否则长持写锁阻塞 Web
        except Exception:  # noqa: BLE001
            errors += 1
            continue
        play = int(meta.get("play_cnt", 0) or 0)
        favorite = int(meta.get("followed_cnt", 0) or 0)
        _upsert_metric_snapshot(c, sid, today, 0, play, favorite, _rank_lookup(c, sid))
        # 同步首批收藏到 cohort.fav_t0(若此前为 0)
        c.execute("UPDATE hg_new_cohort SET fav_t0=? WHERE series_id=? AND COALESCE(fav_t0,0)=0",
                  (favorite, sid))
        c.commit()  # 每剧提交即释放写锁, 避免跨网络调用长持事务
        refreshed += 1
    return {"candidates": len(rows), "refreshed": refreshed, "errors": errors}


def _cutoff(lookback_days):
    import datetime
    return (datetime.date.today() - datetime.timedelta(days=max(1, lookback_days))).strftime("%Y-%m-%d")
