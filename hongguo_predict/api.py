# -*- coding: utf-8 -*-
"""爆剧预测 Flask Blueprint —— 配置/任务状态/日志/今日预测/爆剧预警 + 设置页。

鉴权: 页面与管理 API 仅管理员(session.role==admin)。
"""
from __future__ import annotations

import functools

from flask import Blueprint, jsonify, render_template, request, session

from . import db, scheduler, store

boom_prediction_bp = Blueprint("boom_prediction", __name__)

VALID_TASKS = {"new_sync", "metrics_refresh", "today_prediction", "boom_alert"}


def _admin(view):
    @functools.wraps(view)
    def w(*a, **k):
        if "user_id" not in session:
            return jsonify({"error": "需要登录"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "权限不足"}), 403
        return view(*a, **k)
    return w


def _admin_page(view):
    @functools.wraps(view)
    def w(*a, **k):
        from flask import abort, redirect, url_for
        if "user_id" not in session:
            return redirect(url_for("login", next=request.full_path))
        if session.get("role") != "admin":
            abort(403)
        return view(*a, **k)
    return w


@boom_prediction_bp.route("/boom-prediction")
@_admin_page
def page():
    return render_template("boom_prediction.html", active_page="boom_prediction")


@boom_prediction_bp.route("/api/boom-prediction/config", methods=["GET", "PUT"])
@_admin
def config():
    if request.method == "PUT":
        patch = request.get_json(silent=True) or {}
        return jsonify(store.save_config(patch))
    return jsonify(store.get_config())


@boom_prediction_bp.route("/api/boom-prediction/task-status")
@_admin
def task_status():
    date = request.args.get("date", "").strip() or db.today()
    return jsonify({"prediction_date": date, "tasks": store.list_task_states(date)})


@boom_prediction_bp.route("/api/boom-prediction/logs")
@_admin
def logs():
    return jsonify({"logs": store.list_logs(request.args.get("limit", 80))})


@boom_prediction_bp.route("/api/boom-prediction/today-predictions")
@_admin
def today_predictions():
    date = request.args.get("date", "").strip()
    return jsonify({
        "prediction_date": date or db.today(),
        "items": store.list_today_predictions(date, request.args.get("limit", 120)),
    })


@boom_prediction_bp.route("/api/boom-prediction/boom-alerts")
@_admin
def boom_alerts():
    return jsonify({"items": store.list_boom_alerts(request.args.get("limit", 50))})


@boom_prediction_bp.route("/api/boom-prediction/run/<task_type>", methods=["POST"])
@_admin
def run_task(task_type):
    if task_type not in VALID_TASKS:
        return jsonify({"error": "未知任务类型"}), 400
    try:
        summary = scheduler.run_now(task_type)
        return jsonify({"ok": True, "task_type": task_type, "summary": summary})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)[:300]}), 500
