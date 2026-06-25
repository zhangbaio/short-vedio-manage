# -*- coding: utf-8 -*-
"""爆剧画像评分模型 —— 移植自 xinge server/internal/store/hongguo_profile.go
（含 hongguo_strategy.go 的早期信号特征计算）。

设计为纯函数集合, 所有 DB 访问通过传入的 sqlite3 连接完成。
single-source 场景: source 固定为 'hgnew', cohort.series_id == metric.series_id,
因此省略 xinge 中跨源 id_map 桥接逻辑（算法等价）。
"""
from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field

# ---- 画像作用域常量 ----
SCOPE_AUTO = "auto"
SCOPE_SOURCE = "source_specific"
SCOPE_SHARED = "shared_history"

# 题材文本关键词词库（与 Go hongguoProfileTextKeywords 一致）
_TEXT_KEYWORDS = [
    "重生", "逆袭", "复仇", "豪门", "赘婿", "穿越", "系统", "异能", "玄幻", "修仙",
    "都市", "乡村", "年代", "萌宝", "马甲", "神医", "战神", "离婚", "闪婚", "契约",
    "总裁", "女帝", "团宠", "真假", "替嫁", "后悔", "归来", "觉醒", "末世", "种田",
    "脑洞", "甜宠", "虐恋", "悬疑", "权谋", "校园", "职场", "大佬",
]

_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def normalize_source(source: str) -> str:
    source = (source or "").strip().lower()
    if source in ("", "new", "native"):
        return "hgnew"
    if source in ("52", "52api", "hg52api"):
        return "52api"
    if source == "local":
        return "hglocal"
    return source


def normalize_profile_scope(scope: str) -> str:
    s = (scope or "").strip().lower()
    if s in (SCOPE_SOURCE, "source"):
        return SCOPE_SOURCE
    if s in (SCOPE_SHARED, "shared"):
        return SCOPE_SHARED
    return SCOPE_AUTO


@dataclass
class ProfileCandidate:
    series_id: str = ""
    metric_series_id: str = ""
    title: str = ""
    genre: str = ""
    category: str = ""
    intro: str = ""
    author: str = ""
    episode_cnt: int = 0
    t0: str = ""
    hot_value: int = 0
    play_cnt: int = 0
    favorite_count: int = 0
    best_rank: int = 0


@dataclass
class ProfileScore:
    score: int = 30
    level: str = "C"
    probability: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    profile_reasons: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    history_scope: str = ""
    borrowed_history: bool = False
    early_signal_score: int = 0
    feature_window_hours: int = 0


# ================= 文本/分桶辅助 =================

def parse_store_time(value):
    value = (value or "").strip()
    if not value:
        return None
    for layout in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        v = value
        if layout == "%Y-%m-%dT%H:%M:%S" and len(value) > 19:
            # 去掉时区/毫秒后缀, 仅取前 19 位再试 RFC3339 主体
            v = value[:19]
        try:
            return datetime.datetime.strptime(v, layout)
        except ValueError:
            continue
    return None


def backtest_topics(category: str):
    seen = set()
    out = []
    for field_ in re.split(r"[/\\,，、|;；\s]+", category or ""):
        field_ = field_.strip()
        if not field_ or field_ in seen:
            continue
        seen.add(field_)
        out.append(field_)
    return out


def episode_bucket(n: int) -> str:
    if n <= 0:
        return "unknown"
    if n <= 20:
        return "1-20"
    if n <= 40:
        return "21-40"
    if n <= 60:
        return "41-60"
    if n <= 80:
        return "61-80"
    return "80+"


def _title_tokens(text: str):
    runes = [r for r in (text or "").strip() if r.isalnum() or _is_han(r)]
    out = []
    i = 0
    while i + 1 < len(runes) and len(out) < 6:
        out.append("".join(runes[i:i + 2]))
        i += 2
    return out


def _is_han(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def text_keywords(text: str):
    text = (text or "").strip()
    seen = set()
    out = []
    for word in _TEXT_KEYWORDS:
        if word in text and word not in seen:
            seen.add(word)
            out.append(word)
    if len(out) < 4:
        for token in _title_tokens(text):
            if token not in seen:
                seen.add(token)
                out.append(token)
                if len(out) >= 4:
                    break
    return out


def hour_bucket(t: datetime.datetime) -> str:
    h = t.hour
    if h < 6:
        return "00-06"
    if h < 12:
        return "06-12"
    if h < 18:
        return "12-18"
    return "18-24"


def feature_type_group(feature_type: str) -> str:
    return {
        "topic": "topic",
        "genre": "topic",
        "title_keyword": "title",
        "intro_keyword": "intro",
        "episode_bucket": "episode",
        "publish_hour": "time",
        "publish_weekday": "time",
    }.get(feature_type, feature_type)


def group_cap(group: str) -> int:
    return {
        "topic": 25, "title": 15, "intro": 20, "episode": 10,
        "author": 12, "time": 8, "early": 5,
    }.get(group, 0)


def metric_cap(key: str) -> int:
    # play/hot 提高封顶以承载对数缩放后的真实量级（旧值 8 把百万级播放压成噪声）
    return {"favorite": 36, "play": 20, "hot": 12}.get(key, 0)


def level_of(score: int) -> str:
    if score >= 85:
        return "S"
    if score >= 72:
        return "A"
    if score >= 60:
        return "B"
    return "C"


# ================= 定权 / 命中标签 =================

def wilson_lower_bound(hits: int, n: int) -> float:
    if n <= 0 or hits <= 0:
        return 0.0
    z = 1.96
    phat = hits / n
    den = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    lb = (centre - margin) / den
    return lb if lb > 0 else 0.0


def profile_weight(candidates: int, hits: int, base: float) -> float:
    MIN_SAMPLE = 8
    MAX_WEIGHT = 25.0
    TARGET_LIFT = 8.0
    if candidates < MIN_SAMPLE or hits <= 0 or base <= 0:
        return 0.0
    lb = wilson_lower_bound(hits, candidates)
    lift_lb = lb / base
    if lift_lb <= 1:
        return 0.0
    score = (lift_lb - 1) / (TARGET_LIFT - 1) * MAX_WEIGHT
    return min(score, MAX_WEIGHT)


def percentile90(values):
    vals = sorted(v for v in values if v > 0)
    if len(vals) < 5:
        return 0
    idx = math.ceil(len(vals) * 0.9) - 1
    idx = max(0, min(idx, len(vals) - 1))
    return vals[idx]


def profile_label(row, hit_top, hot_threshold, play_threshold, favorite_threshold):
    basis = []
    if row["best_rank"] > 0 and row["best_rank"] <= hit_top:
        basis.append("rank_top")
    if hot_threshold > 0 and row["hot_value"] >= hot_threshold:
        basis.append("hot_percentile")
    if play_threshold > 0 and row["play_cnt"] >= play_threshold:
        basis.append("play_percentile")
    if favorite_threshold > 0 and row["favorite"] >= favorite_threshold:
        basis.append("favorite_percentile")
    return (len(basis) > 0, basis)


def profile_features(row):
    """返回 [(feature_type, feature_key), ...]，与 Go hongguoProfileFeatures 一致。"""
    out = [("scope_total", "all")]
    if row["genre"]:
        out.append(("genre", row["genre"]))
    for topic in backtest_topics(row["category"]):
        out.append(("topic", topic))
    if row["author"]:
        out.append(("author", row["author"]))
    out.append(("episode_bucket", episode_bucket(row["episode_cnt"])))
    for kw in text_keywords(row["title"]):
        out.append(("title_keyword", kw))
    for kw in text_keywords(row["intro"]):
        out.append(("intro_keyword", kw))
    t = parse_store_time(row["t0"])
    if t is not None:
        out.append(("publish_hour", hour_bucket(t)))
        out.append(("publish_weekday", _WEEKDAYS[(t.weekday() + 1) % 7]))
    return out


# ================= 指标信号 =================

def metric_signals(candidate: ProfileCandidate):
    breakdown = {"favorite": 0, "play": 0, "hot": 0}
    reasons = []
    fav = candidate.favorite_count
    if fav >= 2000:
        breakdown["favorite"] = 28
        reasons.append("strong_favorite_signal")
    elif fav >= 1000:
        breakdown["favorite"] = 24
        reasons.append("strong_favorite_signal")
    elif fav >= 500:
        breakdown["favorite"] = 18
        reasons.append("favorite_signal")
    elif fav >= 200:
        breakdown["favorite"] = 12
        reasons.append("favorite_signal")
    elif fav >= 100:
        breakdown["favorite"] = 8
        reasons.append("favorite_signal")
    elif fav >= 50:
        breakdown["favorite"] = 4
        reasons.append("favorite_signal")
    elif fav > 0:
        breakdown["favorite"] = 2

    if (candidate.genre or "").strip().lower() == "comic_series" and fav > 0:
        if fav >= 1000:
            breakdown["favorite"] += 8
            reasons.append("comic_favorite_signal")
        elif fav >= 200:
            breakdown["favorite"] += 6
            reasons.append("comic_favorite_signal")
        elif fav >= 50:
            breakdown["favorite"] += 3
            reasons.append("comic_favorite_signal")
    if breakdown["favorite"] > metric_cap("favorite"):
        breakdown["favorite"] = metric_cap("favorite")

    # 播放: 对数缩放覆盖 1e3~1e7+ 量级。旧版固定阈值 1000/1020/1040、封顶 6 分,
    # 对真实百万级播放完全失真（14M 播放与 1040 播放同分）。
    if candidate.play_cnt > 1000:
        pts = int(round(math.log10(candidate.play_cnt / 1000.0) * 5))
        pts = max(0, min(pts, metric_cap("play")))
        if pts > 0:
            breakdown["play"] = pts
            reasons.append("play_signal")
    # 热度: 同样对数缩放（覆盖 1e3~1e6+）
    if candidate.hot_value > 1000:
        pts = int(round(math.log10(candidate.hot_value / 1000.0) * 4))
        pts = max(0, min(pts, metric_cap("hot")))
        if pts > 0:
            breakdown["hot"] = pts
            reasons.append("hot_signal")
    return breakdown, reasons


def probability_of(score: int, has_metric_evidence: bool, early_score: int, profile_reason_count: int) -> float:
    p = 0.03 + score * 0.0042
    if has_metric_evidence:
        p += 0.05
    if early_score > 0:
        p += 0.08
    if profile_reason_count == 0:
        cap = 0.12
        if has_metric_evidence and score >= 60:
            cap = 0.28
        if p > cap:
            p = cap
    if not has_metric_evidence and early_score == 0 and p > 0.30:
        p = 0.30
    if p > 0.65:
        p = 0.65
    return p if p > 0 else 0.0


# ================= cohort 加载 + 画像重建 =================

def load_profile_cohorts(db, source, genre, start_date, end_date, label_window_days):
    """加载 cohort 行并跨 hg_metric_snapshot 在标签窗口内聚合指标。
    返回 list[dict(genre, t0, series_id, title, category, episode_cnt, author, intro,
                  hot_value, play_cnt, favorite, best_rank)]。"""
    source = normalize_source(source)
    genre = (genre or "").strip()
    where = ["1=1"]
    args = []
    if source:
        where.append("c.source=?")
        args.append(source)
    if genre:
        where.append("c.genre=?")
        args.append(genre)
    if (start_date or "").strip():
        where.append("c.t0>=?")
        args.append(start_date.strip())
    if (end_date or "").strip():
        where.append("c.t0<=?")
        args.append(end_date.strip())

    query_args = []
    metric_join_source = ""
    if source and source != "all":
        metric_join_source = " AND m.source=?"
        query_args.append(source)
    metric_time_window = ""
    if label_window_days and label_window_days > 0:
        metric_time_window = " AND substr(m.ts,1,10)>=c.t0 AND substr(m.ts,1,10)<=date(c.t0, '+' || ? || ' days')"
        query_args.append(label_window_days)
    query_args.extend(args)

    sql = (
        "SELECT c.genre, c.t0, c.series_id, c.title, c.category, c.episode_cnt, c.author, c.intro, "
        "COALESCE(MAX(m.hot_value), 0), COALESCE(MAX(m.play_cnt), 0), COALESCE(MAX(m.favorite), 0), "
        "COALESCE(MIN(NULLIF(m.best_rank, 0)), 0) "
        "FROM hg_new_cohort c "
        "LEFT JOIN hg_metric_snapshot m ON m.series_id=c.series_id" + metric_join_source + metric_time_window + " "
        "WHERE " + " AND ".join(where) + " "
        "GROUP BY c.series_id ORDER BY c.t0 ASC, c.series_id ASC"
    )
    out = []
    for r in db.execute(sql, query_args):
        out.append({
            "genre": r[0] or "", "t0": r[1] or "", "series_id": r[2] or "",
            "title": r[3] or "", "category": r[4] or "", "episode_cnt": int(r[5] or 0),
            "author": r[6] or "", "intro": r[7] or "",
            "hot_value": int(r[8] or 0), "play_cnt": int(r[9] or 0),
            "favorite": int(r[10] or 0), "best_rank": int(r[11] or 0),
        })
    return out


def rebuild_boom_profile(db, source, genre, hit_top=20, label_window_days=14):
    """重建画像: 覆盖 请求题材 + 标准题材(漫剧/AI) + 全部桶, source_specific 与 shared 两个作用域。"""
    source = normalize_source(source) or "hgnew"
    genre = (genre or "").strip()
    seen = set()
    for g in (genre, "comic_series", "ai_series", ""):
        g = (g or "").strip()
        if g in seen:
            continue
        seen.add(g)
        _rebuild_scope(db, source, g, SCOPE_SOURCE, source, hit_top, label_window_days)
        _rebuild_scope(db, "shared", g, SCOPE_SHARED, "", hit_top, label_window_days)
    db.commit()


def _rebuild_scope(db, profile_source, genre, scope, cohort_source, hit_top, label_window_days):
    if hit_top <= 0:
        hit_top = 20
    if label_window_days <= 0:
        label_window_days = 14
    rows = load_profile_cohorts(db, cohort_source, genre, "", "", label_window_days)
    hot_th = percentile90([r["hot_value"] for r in rows])
    play_th = percentile90([r["play_cnt"] for r in rows])
    fav_th = percentile90([r["favorite"] for r in rows])
    stats = {}
    hits = 0
    for row in rows:
        hit, _ = profile_label(row, hit_top, hot_th, play_th, fav_th)
        if hit:
            hits += 1
        for ft, fk in profile_features(row):
            key = ft + "\x00" + fk
            st = stats.get(key)
            if st is None:
                st = {"ft": ft, "fk": fk, "candidates": 0, "hits": 0}
                stats[key] = st
            st["candidates"] += 1
            if hit:
                st["hits"] += 1
    candidate_count = len(rows)
    base = (hits / candidate_count) if candidate_count > 0 else 0.0
    profile_date = datetime.date.today().strftime("%Y-%m-%d")
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    items = []
    for st in stats.values():
        if not (st["fk"] or "").strip() or st["candidates"] == 0:
            continue
        hit_rate = st["hits"] / st["candidates"]
        lift = (hit_rate / base) if base > 0 else 0.0
        weight = profile_weight(st["candidates"], st["hits"], base)
        items.append((profile_date, profile_source, genre, scope, st["ft"], st["fk"],
                      st["candidates"], st["hits"], hit_rate, lift, weight, now))
    db.execute("DELETE FROM hongguo_boom_profile WHERE source=? AND genre=? AND scope=?",
               (profile_source, genre, scope))
    db.executemany(
        "INSERT INTO hongguo_boom_profile(profile_date, source, genre, scope, feature_type, feature_key, "
        "candidate_count, hit_count, hit_rate, lift, weight, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        items)


def resolve_profile_scope(db, source, genre, requested):
    requested = normalize_profile_scope(requested)
    if requested in (SCOPE_SOURCE, SCOPE_SHARED):
        return requested
    source = normalize_source(source)
    genre = (genre or "").strip()
    r = db.execute(
        "SELECT COALESCE(MAX(candidate_count),0), COALESCE(MAX(hit_count),0) "
        "FROM hongguo_boom_profile WHERE source=? AND genre=? AND scope=?",
        (source, genre, SCOPE_SOURCE)).fetchone()
    candidates = int(r[0] or 0)
    hits = int(r[1] or 0)
    if candidates >= 200 and hits >= 20:
        return SCOPE_SOURCE
    return SCOPE_SHARED


def _profile_weights(db, source, genre, scope):
    out = {}
    for r in db.execute(
            "SELECT feature_type, feature_key, weight FROM hongguo_boom_profile "
            "WHERE source=? AND genre=? AND scope=?", (source, genre, scope)):
        out[(r[0] or "") + "\x00" + (r[1] or "")] = float(r[2] or 0)
    return out


def _weights_have_positive(w):
    return any(v > 0 for v in w.values())


# ================= 早期信号 =================

def _compute_early_features(db, metric_source, series_id, t0, feature_window_hours):
    if feature_window_hours <= 0:
        feature_window_hours = 48
    start = parse_store_time(t0)
    if start is None:
        return None
    where = ["series_id=?"]
    args = [(series_id or "").strip()]
    metric_source = (metric_source or "").strip()
    if metric_source and metric_source != "all":
        where.append("source=?")
        args.append(normalize_source(metric_source))
    end = start + datetime.timedelta(hours=feature_window_hours)
    points = []
    for r in db.execute(
            "SELECT ts, hot_value, play_cnt, favorite, best_rank FROM hg_metric_snapshot "
            "WHERE " + " AND ".join(where) + " ORDER BY ts ASC", args):
        at = parse_store_time(r[0])
        if at is None or at < start or at > end:
            continue
        points.append({
            "at": at, "hours": (at - start).total_seconds() / 3600.0,
            "hot": int(r[1] or 0), "play": int(r[2] or 0),
            "favorite": int(r[3] or 0), "rank": int(r[4] or 0),
        })
    points.sort(key=lambda p: p["at"])
    f = {
        "snapshot_count": len(points), "hot_24": 0.0, "play_24": 0.0, "fav_24": 0.0,
        "rank_enter_hours": 0.0, "best_rank_early": 0, "rank_up_count": 0,
    }
    if len(points) < 2:
        return f

    def slope(limit, pick):
        first = last = None
        for p in points:
            if p["hours"] > limit:
                continue
            if first is None:
                first = p
            last = p
        if first is None or last is None or not last["at"] > first["at"]:
            return 0.0
        return (pick(last) - pick(first)) / max(1.0, (last["at"] - first["at"]).total_seconds() / 3600.0)

    f["hot_24"] = slope(24, lambda p: p["hot"])
    f["play_24"] = slope(24, lambda p: p["play"])
    f["fav_24"] = slope(24, lambda p: p["favorite"])
    last_rank = 0
    for p in points:
        if p["rank"] > 0:
            if f["rank_enter_hours"] == 0:
                f["rank_enter_hours"] = p["hours"]
            if f["best_rank_early"] == 0 or p["rank"] < f["best_rank_early"]:
                f["best_rank_early"] = p["rank"]
            if last_rank > 0 and p["rank"] < last_rank:
                f["rank_up_count"] += 1
            last_rank = p["rank"]
    return f


def _early_signal(db, metric_source, candidate: ProfileCandidate, hit_top, feature_window_hours) -> int:
    series_id = (candidate.metric_series_id or "").strip() or (candidate.series_id or "").strip()
    if not series_id:
        return 0
    f = _compute_early_features(db, metric_source, series_id, candidate.t0, feature_window_hours)
    if f is None:
        return 0
    score = 0.0
    if 0 < f["best_rank_early"] <= hit_top:
        score += 2
    if 0 < f["rank_enter_hours"] <= 6:
        score += 1
    if f["play_24"] > 0 or f["fav_24"] > 0 or f["hot_24"] > 0:
        score += 1
    if f["rank_up_count"] > 0:
        score += 1
    return min(5, int(score + 0.5))


# ================= 主评分 =================

def score_profile_candidate(db, source, metric_source, history_scope, candidate: ProfileCandidate,
                            hit_top=20, feature_window_hours=48) -> ProfileScore:
    source = normalize_source(source)
    metric_source = (metric_source or "").strip()
    if metric_source and metric_source != "all":
        metric_source = normalize_source(metric_source)
    if hit_top <= 0:
        hit_top = 20

    scope = resolve_profile_scope(db, source, candidate.genre, history_scope)
    profile_source = "shared" if scope == SCOPE_SHARED else source
    weights = _profile_weights(db, profile_source, candidate.genre, scope)
    if (candidate.genre or "").strip() and not _weights_have_positive(weights):
        fb = _profile_weights(db, profile_source, "", scope)
        if _weights_have_positive(fb):
            weights = fb

    breakdown = {k: 0 for k in
                 ("topic", "title", "intro", "episode", "author", "time", "early", "play", "favorite", "hot")}
    reasons = []
    profile_reasons = []
    risks = []

    def add_feature(feature_type, feature_key, cap, label):
        w = weights.get(feature_type + "\x00" + feature_key, 0.0)
        if w <= 0:
            return
        group = feature_type_group(feature_type)
        remaining = group_cap(group) - breakdown[group]
        if remaining <= 0:
            return
        sc = min(cap, int(round(w)))
        sc = min(sc, remaining)
        if sc <= 0:
            return
        breakdown[group] += sc
        reasons.append(label + ":" + feature_key)
        profile_reasons.append(label + ":" + feature_key)

    for topic in backtest_topics(candidate.category):
        add_feature("topic", topic, 25, "topic")
    for kw in text_keywords(candidate.title):
        add_feature("title_keyword", kw, 15, "title")
    for kw in text_keywords(candidate.intro):
        add_feature("intro_keyword", kw, 20, "intro")
    if (candidate.author or "").strip():
        add_feature("author", candidate.author.strip(), 15, "author")
    else:
        risks.append("missing_author")
    add_feature("episode_bucket", episode_bucket(candidate.episode_cnt), 10, "episodes")
    if candidate.episode_cnt <= 0:
        risks.append("missing_episode_count")
    t = parse_store_time(candidate.t0)
    if t is not None:
        add_feature("publish_hour", hour_bucket(t), 10, "publish_hour")
        add_feature("publish_weekday", _WEEKDAYS[(t.weekday() + 1) % 7], 5, "publish_weekday")
    else:
        risks.append("missing_publish_time")
    add_feature("genre", candidate.genre, 10, "genre")
    if not candidate.intro:
        risks.append("missing_intro")
    if not profile_reasons:
        risks.append("no_profile_lift")

    score = 30
    for v in breakdown.values():
        score += v

    early = _early_signal(db, metric_source, candidate, hit_top, feature_window_hours)
    score += early
    breakdown["early"] = early

    has_metric_evidence = (candidate.hot_value > 0 or candidate.play_cnt > 0
                           or candidate.favorite_count > 0 or candidate.best_rank > 0)
    metric_breakdown, metric_reasons = metric_signals(candidate)
    metric_boost = 0
    for key, value in metric_breakdown.items():
        if value <= 0:
            continue
        breakdown[key] = min(breakdown[key] + value, metric_cap(key))
        metric_boost += value
        score += value
    reasons.extend(metric_reasons)

    score_cap = 100
    if not profile_reasons:
        score_cap = min(score_cap, 78 if metric_boost >= 12 else 65)
    if early == 0 and not has_metric_evidence:
        score_cap = min(score_cap, 72)
        risks.append("weak_metric_evidence")
    elif early == 0:
        score_cap = min(score_cap, 90 if metric_boost >= 12 else 82)
        risks.append("missing_early_signal")
    if scope == SCOPE_SHARED and early == 0:
        score_cap = min(score_cap, 88 if metric_boost >= 12 else 78)
    if score > score_cap:
        score = score_cap
    score = max(0, min(100, score))

    probability = probability_of(score, has_metric_evidence, early, len(profile_reasons))
    return ProfileScore(
        score=score, level=level_of(score), probability=probability,
        score_breakdown=breakdown, profile_reasons=profile_reasons, reasons=reasons, risks=risks,
        history_scope=scope, borrowed_history=(scope == SCOPE_SHARED),
        early_signal_score=early, feature_window_hours=feature_window_hours,
    )
