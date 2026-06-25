# -*- coding: utf-8 -*-
"""爆剧预测流水线（迁移自 xinge）。

在 app.py 注册:
    from hongguo_predict import boom_prediction_bp, init_predict, start_scheduler
    app.register_blueprint(boom_prediction_bp)
    init_predict()         # 建表 + 存量迁移
    start_scheduler()      # 启动后台采集/评分守护线程

依赖: hongguo_core(签名/拉取层) 可用, 环境变量 SIGN_SERVER 指向真实签名后端。
"""
from .api import boom_prediction_bp
from .db import init_schema


def init_predict():
    init_schema()


def start_scheduler():
    from . import scheduler
    scheduler.start()


__all__ = ["boom_prediction_bp", "init_predict", "start_scheduler"]
