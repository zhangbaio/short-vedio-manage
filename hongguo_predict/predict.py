# -*- coding: utf-8 -*-
"""今日预测 + 爆剧预警生成 —— 移植自 xinge api/short_analysis.go(todayPick*) 与
api/hongguo_boom_alert.go(generateHongguoBoomAlertsForGenre, 去掉远程下发)。
"""
from __future__ import annotations

from . import db, store
from .profile import ProfileCandidate, level_of, score_profile_candidate

SOURCE = "hgnew"
HISTORY_DAYS = 15
HIT_TOP = 20
# 滚动复评窗口（优化③'）: 对最近 N 天上新的剧持续复评, 用当前累计指标评分,
# 以抓住"上新后数天指标才 materialize"的晚熟爆款（仅评分上新当天会漏掉它们）。
LOOKBACK_DAYS = 7


def _today_candidates(c, genre, lookback_days=LOOKBACK_DAYS):
    """滚动候选: cohort 中 t0 在最近 lookback_days 天的该体裁剧, 跨快照聚合当前指标。
    lookback_days=0 退化为仅今日。"""
    today = db.today()
    cutoff = _date_offset(max(0, lookback_days))
    rows = c.execute(
        """SELECT c.series_id, c.title, c.cover, c.genre, c.category, c.intro, c.author, c.episode_cnt,
                  c.t0, c.fetched_at,
                  COALESCE(MAX(m.hot_value),0), COALESCE(MAX(m.play_cnt), c.play_t0, 0),
                  COALESCE(MAX(m.favorite), c.fav_t0, 0), COALESCE(MIN(NULLIF(m.best_rank,0)),0)
           FROM hg_new_cohort c
           LEFT JOIN hg_metric_snapshot m ON m.series_id=c.series_id AND m.source=c.source
           WHERE c.source=? AND c.genre=? AND c.t0>=? AND c.t0<=?
           GROUP BY c.series_id""",
        (SOURCE, genre, cutoff, today)).fetchall()
    out = []
    for r in rows:
        out.append({
            "series_id": r[0], "title": r[1], "cover": r[2], "genre": r[3], "category": r[4], "intro": r[5],
            "author": r[6], "episode_cnt": int(r[7] or 0), "first_seen": r[9] or r[8] or today, "last_seen": r[9] or "",
            "hot_value": int(r[10] or 0), "play_cnt": int(r[11] or 0), "favorite_count": int(r[12] or 0),
            "best_rank": int(r[13] or 0),
        })
    return out


def _history_report(c, genre):
    """历史画像样本概况, 驱动评分封顶（对应 xinge todayPickHistoryReport 的 Total/WithMetric）。"""
    end = _date_offset(1)
    start = _date_offset(HISTORY_DAYS)
    total = 0
    with_metric = 0
    for r in c.execute(
            """SELECT c.series_id, COALESCE(MAX(m.play_cnt),0)+COALESCE(MAX(m.favorite),0)
                      +COALESCE(MAX(m.hot_value),0)+COALESCE(MIN(NULLIF(m.best_rank,0)),0)
               FROM hg_new_cohort c
               LEFT JOIN hg_metric_snapshot m ON m.series_id=c.series_id AND m.source=c.source
               WHERE c.source=? AND c.genre=? AND c.t0>=? AND c.t0<=?
               GROUP BY c.series_id""",
            (SOURCE, genre, start, end)):
        total += 1
        if int(r[1] or 0) > 0:
            with_metric += 1
    return {"total": total, "with_metric": with_metric}


def _date_offset(days):
    import datetime
    return (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def _score_candidate(c, item, report):
    """对单个今日候选评分, 返回 prediction dict（对应 scoreHongguoTodayPickCandidate）。"""
    ps = score_profile_candidate(
        c, SOURCE, SOURCE, "auto",
        ProfileCandidate(
            series_id=item["series_id"], metric_series_id=item["series_id"], title=item["title"],
            genre=item["genre"], category=item["category"], intro=item["intro"], author=item["author"],
            episode_cnt=item["episode_cnt"], t0=item["first_seen"], hot_value=item["hot_value"],
            play_cnt=item["play_cnt"], favorite_count=item["favorite_count"], best_rank=item["best_rank"]),
        HIT_TOP, 48)
    reasons = ["今日上新候选"] + list(ps.reasons)
    risks = list(ps.risks)
    score = ps.score
    probability = ps.probability
    if report["total"] == 0:
        score = min(score, 65)
        probability = min(probability, 0.18)
        risks.append("历史画像样本不足")
    elif report["with_metric"] == 0:
        score = min(score, 72)
        probability = min(probability, 0.25)
        risks.append("历史结果指标样本不足")
    if ps.early_signal_score == 0:
        risks.append("早期指标不足")
    nxt = "观察 1-2 次榜单变化"
    if score >= 85:
        nxt = "优先下载并上传视频号"
    elif score >= 72:
        nxt = "建议下载试跑，等待二次确认"
    return {
        "series_id": item["series_id"], "title": item["title"], "cover": item["cover"], "genre": item["genre"],
        "category": item["category"], "intro": item["intro"], "author": item["author"],
        "episode_cnt": item["episode_cnt"], "hot_value": item["hot_value"], "play_cnt": item["play_cnt"],
        "favorite_count": item["favorite_count"], "first_seen": item["first_seen"], "last_seen": item["last_seen"],
        "potential": score, "level": level_of(score), "probability": probability,
        "score_breakdown": ps.score_breakdown, "profile_reasons": ps.profile_reasons,
        "history_scope": ps.history_scope, "borrowed_history": ps.borrowed_history,
        "early_signal_score": ps.early_signal_score, "best_rank": item["best_rank"],
        "reasons": reasons, "risks": risks, "recommend_next": nxt,
    }


def run_prediction(c, genres):
    """生成今日预测并覆盖入库。返回 summary。"""
    today = db.today()
    summary = {}
    for genre in genres:
        report = _history_report(c, genre)
        preds = [_score_candidate(c, it, report) for it in _today_candidates(c, genre)]
        preds.sort(key=lambda p: (p["potential"], p["probability"]), reverse=True)
        store.replace_today_predictions(c, SOURCE, genre, today, preds)
        summary[genre] = {"candidates": len(preds),
                          "top": preds[0]["potential"] if preds else 0}
    return summary


def run_boom_alert(c, genres, threshold):
    """扫描今日候选, 评分≥阈值者入库为爆剧预警（不远程下发）。返回 summary。"""
    today = db.today()
    threshold = threshold if threshold and threshold > 0 else 60
    scanned = alerted = duplicates = 0
    for genre in genres:
        report = _history_report(c, genre)
        for it in _today_candidates(c, genre):
            scanned += 1
            p = _score_candidate(c, it, report)
            if p["potential"] < threshold:
                continue
            created = store.create_boom_alert_if_new(c, {
                "source": SOURCE, "genre": genre, "series_id": p["series_id"], "title": p["title"],
                "cover": p["cover"], "category": p["category"], "intro": p["intro"], "author": p["author"],
                "episode_cnt": p["episode_cnt"], "play_cnt": p["play_cnt"], "favorite_count": p["favorite_count"],
                "hot_value": p["hot_value"], "score": p["potential"], "level": p["level"],
                "probability": p["probability"], "first_seen": p["first_seen"],
                "reasons": p["reasons"], "risks": p["risks"],
            })
            if created:
                alerted += 1
            else:
                duplicates += 1
    return {"threshold": threshold, "scanned": scanned, "alerted": alerted, "duplicates": duplicates}
