# -*- coding: utf-8 -*-
"""后台调度 —— 守护线程, 每 60s 唤醒, 按配置间隔经 claim_task(CAS) 驱动各任务。

移植自 xinge api/hongguo_monitor_runner.go 的调度循环, 单租户。多 worker 安全:
claim_task 用 task_state 行的 status/last_started_at 做原子认领, 同一时刻仅一个 worker 执行。
"""
from __future__ import annotations

import datetime
import threading
import time

from . import collector, db, predict, profile, store

SOURCE = "hgnew"
_started = False
_lock = threading.Lock()


def _run_task(c, task_type, genre, interval_min, fn, source_label, genre_label):
    """认领→执行→记状态/日志 的统一封装。fn() 返回 summary dict 或抛异常。"""
    date = db.today()
    if not store.claim_task(c, task_type, SOURCE, genre, date, interval_min):
        return False
    started = datetime.datetime.now()
    try:
        summary = fn()
        store.finish_task(c, task_type, SOURCE, genre, date, "success", _brief(summary), started)
        store.add_log(c, source_label, genre_label, date, task_type, "done", "info",
                      _TASK_LABELS.get(task_type, task_type) + "完成", summary, started)
        return True
    except Exception as e:  # noqa: BLE001
        store.finish_task(c, task_type, SOURCE, genre, date, "failed", str(e)[:200], started)
        store.add_log(c, source_label, genre_label, date, task_type, "failed", "error",
                      _TASK_LABELS.get(task_type, task_type) + "失败", {"error": str(e)[:200]}, started)
        return False


_TASK_LABELS = {
    "new_sync": "上新同步", "metrics_refresh": "指标补齐",
    "today_prediction": "今日预测", "boom_alert": "爆剧预警",
}


def _brief(summary):
    try:
        return "; ".join(f"{k}={v}" for k, v in (summary or {}).items())[:480]
    except Exception:  # noqa: BLE001
        return ""


def tick():
    """单次调度判定。可被 API 手动触发或循环调用。"""
    c = db.connect()
    try:
        cfg = store.get_config(c)
        if not cfg["enabled"]:
            return
        genres = cfg["genres"]

        if cfg["new_sync_enabled"]:
            _run_task(c, "new_sync", "", cfg["new_sync_interval_min"],
                      lambda: collector.run_new_sync(c, genres), SOURCE, "")

        if cfg["metrics_enabled"]:
            _run_task(c, "metrics_refresh", "", cfg["metrics_interval_min"],
                      lambda: collector.run_metrics_refresh(c, genres, cfg["metrics_limit"]), SOURCE, "")

        if cfg["prediction_enabled"]:
            def _predict():
                profile.rebuild_boom_profile(c, SOURCE, "")
                return predict.run_prediction(c, genres)
            _run_task(c, "today_prediction", "", cfg["prediction_interval_min"], _predict, SOURCE, "")

        if cfg["boom_alert_enabled"]:
            _run_task(c, "boom_alert", "", cfg["boom_alert_interval_min"],
                      lambda: predict.run_boom_alert(c, genres, cfg["boom_alert_min_score"]), SOURCE, "")
    finally:
        c.close()


def run_now(task_type):
    """忽略间隔, 立即执行指定任务（API 手动触发用）。返回 summary。"""
    c = db.connect()
    try:
        cfg = store.get_config(c)
        genres = cfg["genres"]
        date = db.today()
        started = datetime.datetime.now()
        if task_type == "new_sync":
            summary = collector.run_new_sync(c, genres)
        elif task_type == "metrics_refresh":
            summary = collector.run_metrics_refresh(c, genres, cfg["metrics_limit"])
        elif task_type == "today_prediction":
            profile.rebuild_boom_profile(c, SOURCE, "")
            summary = predict.run_prediction(c, genres)
        elif task_type == "boom_alert":
            summary = predict.run_boom_alert(c, genres, cfg["boom_alert_min_score"])
        else:
            raise ValueError("未知任务类型: " + str(task_type))
        store.finish_task(c, task_type, SOURCE, "", date, "success", _brief(summary), started)
        store.add_log(c, SOURCE, "", date, task_type, "manual_done", "info",
                      _TASK_LABELS.get(task_type, task_type) + "(手动)完成", summary, started)
        return summary
    finally:
        c.close()


def _loop():
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(60)


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="hg-predict-scheduler", daemon=True).start()
