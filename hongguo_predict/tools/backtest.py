# -*- coding: utf-8 -*-
"""爆剧预测算法回测 —— 用历史预测对照成熟期真实指标，验证有效性并定位优化点。

方法（防泄漏）：
  - 画像只用"预测窗口起始日之前"的 cohort 训练；
  - 用每个剧"预测日早期(t0 附近)"的指标评分（模型当日能看到的）；
  - 成熟"结果"取该剧全时序的最新累计播放/收藏；
  - 分桶命中率 / 秩相关 / 漏报(低分高表现) / 误报(高分低表现) 全量统计。

用法: python -m hongguo_predict.tools.backtest [seed_path] [test_start] [test_end]
默认 seed=data/_seed_bt.db, 窗口 2026-06-08..2026-06-12。
"""
from __future__ import annotations

import os
import sys
import tempfile

from .. import db
from .. import profile as P

GENRES = ("comic_series", "ai_series")
SCORING_LOOKBACK = 1  # 评分指标回看天数（1=只看当天; 7=滚动复评窗口）


def _spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0


def _median(v):
    if not v:
        return 0
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(v, p):
    if not v:
        return 0
    s = sorted(v)
    import math
    idx = max(0, min(len(s) - 1, math.ceil(len(s) * p) - 1))
    return s[idx]


def _rebuild_dated(c, source, genre, scope, cohort_source, end_date):
    """用 t0<=end_date 的 cohort 训练画像（防泄漏）。"""
    rows = P.load_profile_cohorts(c, cohort_source, genre, "", end_date, 14)
    hot = P.percentile90([r["hot_value"] for r in rows])
    play = P.percentile90([r["play_cnt"] for r in rows])
    fav = P.percentile90([r["favorite"] for r in rows])
    stats = {}
    hits = 0
    for row in rows:
        hit, _ = P.profile_label(row, 20, hot, play, fav)
        if hit:
            hits += 1
        for ft, fk in P.profile_features(row):
            st = stats.setdefault(ft + "\x00" + fk, {"ft": ft, "fk": fk, "c": 0, "h": 0})
            st["c"] += 1
            if hit:
                st["h"] += 1
    base = hits / len(rows) if rows else 0.0
    c.execute("DELETE FROM hongguo_boom_profile WHERE source=? AND genre=? AND scope=?", (source, genre, scope))
    items = []
    for st in stats.values():
        if not st["fk"].strip() or st["c"] == 0:
            continue
        hr = st["h"] / st["c"]
        lift = hr / base if base > 0 else 0.0
        w = P.profile_weight(st["c"], st["h"], base)
        items.append(("2026-01-01", source, genre, scope, st["ft"], st["fk"], st["c"], st["h"], hr, lift, w, ""))
    c.executemany(
        "INSERT INTO hongguo_boom_profile(profile_date,source,genre,scope,feature_type,feature_key,"
        "candidate_count,hit_count,hit_rate,lift,weight,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", items)
    return len(rows), hits


def run(seed, test_start, test_end, train_end):
    analysis = tempfile.mktemp(suffix=".db")
    db.DATABASE = analysis
    db.init_schema()
    c = db.connect()
    c.execute("ATTACH ? AS s", (seed,))
    c.execute("""INSERT OR IGNORE INTO hg_new_cohort(series_id,book_id,genre,t0,title,category,episode_cnt,author,
        fav_t0,publish_time,cover,intro,source,play_t0,fetched_at,created_at)
        SELECT series_id,book_id,genre,t0,title,category,episode_cnt,author,fav_t0,publish_time,cover,intro,source,0,fetched_at,created_at FROM s.cohort""")
    c.execute("""INSERT OR REPLACE INTO hg_metric_snapshot(series_id,ts,hot_value,play_cnt,favorite,best_rank,source)
        SELECT series_id,ts,hot_value,play_cnt,favorite,best_rank,source FROM s.snap""")
    c.commit()
    c.execute("DETACH s")

    print(f"== 训练画像（防泄漏, t0<={train_end}） ==")
    for g in (*GENRES, ""):
        nr, nh = _rebuild_dated(c, "hgnew", g, P.SCOPE_SOURCE, "hgnew", train_end)
        _rebuild_dated(c, "shared", g, P.SCOPE_SHARED, "", train_end)
        if g:
            print(f"  {g:13} 训练样本={nr} 命中={nh}")
    pos = c.execute("SELECT COUNT(*) FROM hongguo_boom_profile WHERE weight>0").fetchone()[0]
    print(f"  正权重特征={pos}")

    test = c.execute(
        "SELECT series_id,genre,t0,title,category,episode_cnt,author,intro FROM hg_new_cohort "
        "WHERE t0>=? AND t0<=? AND genre IN ('comic_series','ai_series')", (test_start, test_end)).fetchall()
    items = []
    no_metric = 0
    for s in test:
        sid = s["series_id"]
        late = c.execute("SELECT MAX(play_cnt),MAX(favorite),MAX(ts),COUNT(*) FROM hg_metric_snapshot WHERE series_id=?", (sid,)).fetchone()
        if not late or late[0] is None or late[3] == 0:
            no_metric += 1
            continue
        out_play, out_fav, last_ts, nsnap = int(late[0] or 0), int(late[1] or 0), late[2], late[3]
        # 评分指标 = t0 后 lookback 天内 materialize 的最新快照（lookback=1 模拟只看当天;
        # lookback>=7 模拟"滚动复评窗口"在指标出现后当天抓到）。
        early = c.execute(
            "SELECT play_cnt,favorite,hot_value,best_rank,ts FROM hg_metric_snapshot WHERE series_id=? "
            "AND ts<=date(?, '+' || ? || ' days') ORDER BY play_cnt DESC, ts DESC LIMIT 1",
            (sid, s["t0"], SCORING_LOOKBACK)).fetchone()
        if not early:
            early = c.execute(
                "SELECT play_cnt,favorite,hot_value,best_rank,ts FROM hg_metric_snapshot WHERE series_id=? "
                "ORDER BY ts ASC LIMIT 1", (sid,)).fetchone()
        e_play, e_fav, e_hot, e_rank, e_ts = int(early[0] or 0), int(early[1] or 0), int(early[2] or 0), int(early[3] or 0), early[4]
        days_first = (_d(e_ts) - _d(s["t0"])).days if e_ts and s["t0"] else 0
        ps = P.score_profile_candidate(c, "hgnew", "hgnew", "auto", P.ProfileCandidate(
            series_id=sid, metric_series_id=sid, title=s["title"] or "", genre=s["genre"], category=s["category"] or "",
            intro=s["intro"] or "", author=s["author"] or "", episode_cnt=int(s["episode_cnt"] or 0), t0=s["t0"],
            hot_value=e_hot, play_cnt=e_play, favorite_count=e_fav, best_rank=e_rank), 20, 48)
        items.append({
            "sid": sid, "title": s["title"] or "", "genre": s["genre"], "t0": s["t0"],
            "score": ps.score, "level": ps.level, "prob": ps.probability,
            "e_fav": e_fav, "e_play": e_play, "out_fav": out_fav, "out_play": out_play,
            "grow_fav": out_fav - e_fav, "days_first": days_first, "nsnap": nsnap,
            "reasons": ps.profile_reasons,
        })
    return c, items, len(test), no_metric


def _d(s):
    import datetime
    return datetime.datetime.strptime((s or "")[:10], "%Y-%m-%d")


def report(items, n_test, no_metric, test_start, test_end):
    n = len(items)
    print(f"\n== 回测窗口 {test_start}..{test_end}（成熟至最新快照） ==")
    print(f"上新候选={n_test}  可评估(有指标时序)={n}  无指标跳过={no_metric}")
    if n < 5:
        print("可评估样本过少，无法得出统计结论。")
        return
    days = [it["days_first"] for it in items]
    print(f"早期快照距 t0 天数: 中位 {_median(days):.0f}  P90 {_percentile(days,0.9)}（越小越接近真实'早期'）")

    plays = [it["out_play"] for it in items]
    print(f"成熟播放分布: P50={_percentile(plays,0.5):.0f} P90={_percentile(plays,0.9):.0f} P95={_percentile(plays,0.95):.0f} max={max(plays)}")
    # 实际"爆剧"用成熟播放绝对阈值（收藏字段稀疏不可靠；播放双峰分离清晰: 死剧~1e3 vs 爆款~1e6）
    PLAY_BOOM = 50000
    for it in items:
        it["hit"] = it["out_play"] >= PLAY_BOOM
    nhit = sum(it["hit"] for it in items)
    print(f"实际爆剧阈值: 成熟播放 >= {PLAY_BOOM}  -> 实际爆剧 {nhit}/{n}")

    print("\n-- 按预测等级分桶 --")
    print(f"{'等级':<5}{'数量':>5}{'中位成熟收藏':>14}{'中位成熟播放':>14}{'实际爆剧率':>12}")
    for lvl in ("S", "A", "B", "C"):
        g = [it for it in items if it["level"] == lvl]
        if not g:
            continue
        hr = sum(x["hit"] for x in g) / len(g)
        print(f"{lvl:<5}{len(g):>5}{_median([x['out_fav'] for x in g]):>14.0f}{_median([x['out_play'] for x in g]):>14.0f}{hr:>11.0%}")

    sc = [it["score"] for it in items]
    print("\n-- 秩相关(Spearman) 预测分 vs 成熟指标 --")
    print(f"  vs 成熟收藏 {_spearman(sc,[it['out_fav'] for it in items]):+.3f}   vs 成熟播放 {_spearman(sc,[it['out_play'] for it in items]):+.3f}   vs 收藏增量 {_spearman(sc,[it['grow_fav'] for it in items]):+.3f}")

    K = min(20, n // 3)
    by_score = sorted(items, key=lambda x: (x["score"], x["out_play"]), reverse=True)[:K]
    prec = sum(x["hit"] for x in by_score) / K
    by_out = sorted(items, key=lambda x: x["out_play"], reverse=True)
    top_out_ids = {x["sid"] for x in by_out[:K]}
    recall = sum(1 for x in by_score if x["sid"] in top_out_ids) / K
    print(f"\n-- Top-{K}（按预测分）--  Precision@{K}={prec:.0%}  Recall@{K}(命中真实播放Top{K})={recall:.0%}")

    print(f"\n-- 模型选出的 Top-10（高分）实际表现 --")
    for it in by_score[:10]:
        flag = "[爆]" if it["hit"] else "[未]"
        print(f"  {it['score']:>3}{it['level']} 播放{it['out_play']:>9} 收藏{it['out_fav']:>7} {flag}  {it['title'][:20]}")

    print(f"\n-- 漏报：实际爆款(播放>=5万)但预测低分(<60) 的剧（关键优化信号）--")
    miss = sorted([it for it in items if it["hit"] and it["score"] < 60], key=lambda x: x["out_play"], reverse=True)
    if not miss:
        print("  无（爆款都拿到了≥60分）")
    for it in miss[:10]:
        print(f"  {it['score']:>3}{it['level']} 播放{it['out_play']:>9} 早期播放{it['e_play']:>6} 早期收藏{it['e_fav']:>6} 距t0 {it['days_first']}天  {it['title'][:16]} | 画像:{it['reasons'][:3]}")

    print(f"\n-- 误报：预测高分(>=72)但实际未爆(播放<1万) 的剧 --")
    fp = sorted([it for it in items if it["score"] >= 72 and it["out_play"] < 10000], key=lambda x: x["score"], reverse=True)
    if not fp:
        print("  无")
    for it in fp[:8]:
        print(f"  {it['score']:>3}{it['level']} 播放{it['out_play']:>7} 早期收藏{it['e_fav']:>6}  {it['title'][:18]} | 画像:{it['reasons'][:3]}")


def main():
    seed = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "..", "data", "_seed_bt.db")
    test_start = sys.argv[2] if len(sys.argv) > 2 else "2026-06-08"
    test_end = sys.argv[3] if len(sys.argv) > 3 else "2026-06-12"
    global SCORING_LOOKBACK
    if len(sys.argv) > 4:
        SCORING_LOOKBACK = int(sys.argv[4])
    print(f"[评分回看窗口 = {SCORING_LOOKBACK} 天]")
    import datetime
    train_end = (_d(test_start) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    c, items, n_test, no_metric = run(os.path.abspath(seed), test_start, test_end, train_end)
    report(items, n_test, no_metric, test_start, test_end)
    c.close()


if __name__ == "__main__":
    main()
