from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list, row_to_dict
from app.services.common import RELEASE_SELECT

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        counts = {}
        for name in ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭"):
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status=?", (name,)
            ).fetchone()["c"]
        # 已放行按窗态分桶：时段外放行单独可见，绝不与普通时段内放行混成一格
        released_split = conn.execute(
            """
            SELECT
              SUM(CASE WHEN out_of_window=1 THEN 1 ELSE 0 END) AS 时段外,
              SUM(CASE WHEN out_of_window=0 THEN 1 ELSE 0 END) AS 时段内
            FROM releases WHERE status='已放行'
            """
        ).fetchone()
        counts["已放行-时段外"] = released_split["时段外"] or 0
        counts["已放行-时段内"] = released_split["时段内"] or 0
        active = rows_to_list(
            conn.execute(
                RELEASE_SELECT
                + " WHERE r.status IN ('已入闸','晋升中','已放行','回滚中') ORDER BY r.id DESC"
            ).fetchall()
        )
        versions = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       v.version AS 版本, (1 - v.in_window) AS 时段外,
                       v.updated_at AS 更新时间
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        health = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, (1 - i.in_window) AS 时段外,
                       i.release_id AS 发版单编号, i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )
        return {"分布": counts, "在途": active, "环境版本": versions, "实例": health}
