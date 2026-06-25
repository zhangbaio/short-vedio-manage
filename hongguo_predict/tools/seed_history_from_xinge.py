# -*- coding: utf-8 -*-
"""从 xinge 服务器导入历史数据为爆剧预测喂料（消除冷启动）。

做什么：
  1. SSH 连 xinge 服务器（默认用 hongguo 项目的 deploy/id_hongguo 免密 key）。
  2. 在服务器 /tmp 构建"已桥接"种子库：xinge 的 hgnew cohort(上新历史) 的真实指标
     存在 hglocal 快照里, 经 hg_id_map 翻译到 cohort.series_id, 落为单源 hgnew 快照
     （本项目单源, 运行时无需 id_map）。只读生产库, 只写 /tmp。
  3. 下载种子库, 导入本地 data/dramas.db 的 hg_new_cohort / hg_metric_snapshot,
     并重建画像 hongguo_boom_profile。

用法（在 short-vedio-manage 根目录）：
    python -m hongguo_predict.tools.seed_history_from_xinge \
        --host 8.131.149.195 --key D:/code/hongguo/deploy/id_hongguo \
        --xinge-db /www/xinge/data/xinge.db

如需给"服务器上的" dramas.db 喂料, 把本脚本与 hongguo_predict 一起放到服务器跑,
或下载种子库后用 --import-only 指向目标库。
"""
from __future__ import annotations

import argparse
import os
import sys

# 服务器侧构建脚本（只读生产、写 /tmp）。在远端 python3 执行。
_REMOTE_BUILD = r'''
import sqlite3, os
PROD="file:{xinge_db}?mode=ro"; OUT="/tmp/xinge_seed2.db"
os.path.exists(OUT) and os.remove(OUT)
src=sqlite3.connect(PROD, uri=True); dst=sqlite3.connect(OUT); cur=dst.cursor()
cur.execute("CREATE TABLE cohort(series_id,book_id,genre,t0,title,category,episode_cnt,author,fav_t0,publish_time,cover,intro,source,fetched_at,created_at)")
coh=src.execute("SELECT series_id,book_id,genre,t0,title,category,episode_cnt,author,fav_t0,publish_time,cover,intro,source,fetched_at,created_at FROM hg_new_cohort WHERE source='hgnew'").fetchall()
cur.executemany("INSERT INTO cohort VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", coh)
cur.execute("CREATE TABLE idmap(book_id,series_id)")
cur.executemany("INSERT INTO idmap VALUES(?,?)", src.execute("SELECT book_id,series_id FROM hg_id_map WHERE source='hglocal'").fetchall())
cur.execute("CREATE TABLE snap_local(series_id,ts,hot_value,play_cnt,favorite,best_rank)")
cur.executemany("INSERT INTO snap_local VALUES(?,?,?,?,?,?)", src.execute("SELECT series_id,ts,hot_value,play_cnt,favorite,best_rank FROM hg_metric_snapshot WHERE source='hglocal'").fetchall())
cur.execute("CREATE TABLE snap_hgnew(series_id,ts,hot_value,play_cnt,favorite,best_rank)")
cur.executemany("INSERT INTO snap_hgnew VALUES(?,?,?,?,?,?)", src.execute("SELECT series_id,ts,hot_value,play_cnt,favorite,best_rank FROM hg_metric_snapshot WHERE source='hgnew'").fetchall())
dst.commit()
cur.execute("CREATE INDEX i1 ON idmap(book_id)"); cur.execute("CREATE INDEX i2 ON snap_local(series_id)"); dst.commit()
cur.execute("CREATE TABLE snap(series_id,ts,hot_value,play_cnt,favorite,best_rank,source)")
cur.execute("INSERT INTO snap SELECT c.series_id,m.ts,MAX(m.hot_value),MAX(m.play_cnt),MAX(m.favorite),COALESCE(MIN(NULLIF(m.best_rank,0)),0),'hgnew' FROM cohort c JOIN idmap im ON im.book_id=COALESCE(NULLIF(c.book_id,''),c.series_id) JOIN snap_local m ON m.series_id=im.series_id GROUP BY c.series_id,m.ts")
cur.execute("INSERT INTO snap SELECT s.series_id,s.ts,s.hot_value,s.play_cnt,s.favorite,s.best_rank,'hgnew' FROM snap_hgnew s WHERE NOT EXISTS(SELECT 1 FROM snap x WHERE x.series_id=s.series_id AND x.ts=s.ts)")
for t in ("idmap","snap_local","snap_hgnew"): cur.execute("DROP TABLE "+t)
dst.commit(); cur.execute("VACUUM"); dst.commit()
print("cohort=%d snap=%d" % (len(coh), cur.execute("SELECT COUNT(*) FROM snap").fetchone()[0]))
'''


def build_and_fetch(host, user, key, password, xinge_db, local_seed):
    import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(timeout=20, banner_timeout=30, auth_timeout=30)
    if key and os.path.exists(key):
        kw["pkey"] = paramiko.RSAKey.from_private_key_file(key)
    elif password:
        kw["password"] = password
    cli.connect(host, username=user, **kw)
    try:
        script = _REMOTE_BUILD.format(xinge_db=xinge_db)
        sftp = cli.open_sftp()
        with sftp.open("/tmp/_hg_seed_build.py", "w") as f:
            f.write(script)
        _i, o, e = cli.exec_command("python3 /tmp/_hg_seed_build.py", timeout=900)
        out = o.read().decode("utf-8", "replace").strip()
        err = e.read().decode("utf-8", "replace").strip()
        print("[remote]", out or "(no stdout)")
        if err:
            print("[remote-stderr]", err[:500])
        sftp.get("/tmp/xinge_seed2.db", local_seed)
        sftp.remove("/tmp/xinge_seed2.db")
        sftp.remove("/tmp/_hg_seed_build.py")
        sftp.close()
    finally:
        cli.close()
    print("[local] seed downloaded ->", local_seed, os.path.getsize(local_seed), "bytes")


def import_seed(seed_path):
    from hongguo_predict import db, predict, profile
    db.init_schema()
    c = db.connect()
    for t in ("hg_new_cohort", "hg_metric_snapshot", "hongguo_boom_profile"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    c.execute("ATTACH ? AS seed", (seed_path,))
    c.execute("""INSERT OR IGNORE INTO hg_new_cohort(series_id,book_id,genre,t0,title,category,episode_cnt,author,
        fav_t0,publish_time,cover,intro,source,play_t0,fetched_at,created_at)
        SELECT series_id,book_id,genre,t0,title,category,episode_cnt,author,fav_t0,publish_time,cover,intro,source,0,fetched_at,created_at
        FROM seed.cohort""")
    c.execute("""INSERT OR REPLACE INTO hg_metric_snapshot(series_id,ts,hot_value,play_cnt,favorite,best_rank,source)
        SELECT series_id,ts,hot_value,play_cnt,favorite,best_rank,source FROM seed.snap""")
    c.commit()
    c.execute("DETACH seed")
    nc = c.execute("SELECT COUNT(*) FROM hg_new_cohort").fetchone()[0]
    ns = c.execute("SELECT COUNT(*) FROM hg_metric_snapshot").fetchone()[0]
    print(f"[local] imported cohort={nc} snap={ns}; rebuilding profile…")
    profile.rebuild_boom_profile(c, "hgnew", "")
    pos = c.execute("SELECT COUNT(*) FROM hongguo_boom_profile WHERE weight>0").fetchone()[0]
    print(f"[local] profile positive_weight={pos}")
    for g in ("comic_series", "ai_series"):
        print(f"[local] {g} history_report", predict._history_report(c, g))
    c.close()


def main():
    ap = argparse.ArgumentParser(description="从 xinge 服务器桥接导入历史数据")
    ap.add_argument("--host", default="8.131.149.195")
    ap.add_argument("--user", default="root")
    ap.add_argument("--key", default="D:/code/hongguo/deploy/id_hongguo", help="SSH 私钥路径（优先）")
    ap.add_argument("--password", default=os.environ.get("XINGE_SSH_PWD", ""), help="无 key 时回退密码")
    ap.add_argument("--xinge-db", default="/www/xinge/data/xinge.db")
    ap.add_argument("--seed", default=os.path.join(os.path.dirname(__file__), "..", "..", "data", "_xinge_seed.db"))
    ap.add_argument("--import-only", action="store_true", help="跳过 SSH, 直接导入已下载的 --seed")
    args = ap.parse_args()
    seed = os.path.abspath(args.seed)
    if not args.import_only:
        build_and_fetch(args.host, args.user, args.key, args.password, args.xinge_db, seed)
    import_seed(seed)
    try:
        os.remove(seed)
    except OSError:
        pass
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
