from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list

router = APIRouter(tags=["对账"])


@router.get("/对账")
def reconcile():
    with db() as conn:
        release_stats = {
            "申请数": conn.execute("SELECT COUNT(*) AS c FROM releases").fetchone()["c"],
            "已放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行'"
            ).fetchone()["c"],
            # 对账分桶：时段外放行单列，不能当成普通时段内放行
            "时段外放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行' AND out_of_window=1"
            ).fetchone()["c"],
            "时段内放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行' AND out_of_window=0"
            ).fetchone()["c"],
            "已回滚": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已回滚'"
            ).fetchone()["c"],
            "在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
        }
        version_vs_instance = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境,
                       v.version AS 名册版本,
                       (1 - v.in_window) AS 名册时段外,
                       GROUP_CONCAT(i.version, ',') AS 实例版本们,
                       GROUP_CONCAT((1 - i.in_window), ',') AS 实例时段外们,
                       SUM(CASE WHEN i.health='探针红' THEN 1 ELSE 0 END) AS 红灯数
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                LEFT JOIN instances i ON i.app_id = v.app_id AND i.env_id = v.env_id
                GROUP BY v.app_id, v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        promote_stats = conn.execute(
            """
            SELECT
              COUNT(*) AS 总数,
              SUM(CASE WHEN in_window=1 THEN 1 ELSE 0 END) AS 时段内,
              SUM(CASE WHEN in_window=0 THEN 1 ELSE 0 END) AS 时段外
            FROM promote_events WHERE decision='放行'
            """
        ).fetchone()
        return {
            "发版统计": release_stats,
            "晋升事件数": promote_stats["总数"] or 0,
            "晋升时段内": promote_stats["时段内"] or 0,
            "晋升时段外": promote_stats["时段外"] or 0,
            "名册与实例": version_vs_instance,
        }
