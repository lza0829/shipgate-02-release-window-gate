from __future__ import annotations
import sqlite3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, row_to_dict
from app.services.common import (
    audit,
    env_by_code,
    get_release,
    get_setting,
    now_str,
    ordered_envs,
    release_window_open,
    set_app_env_version,
    upsert_instance,
)

router = APIRouter(tags=["晋升"])


class PromoteIn(BaseModel):
    目标环境: str
    备注: str = ""


def _validate_target(conn, r, target_code: str):
    """写口与读侧预判共用的唯一一套晋升规则，禁止两边各算各的。"""
    if r["status"] not in ("已入闸", "晋升中"):
        raise HTTPException(400, "当前状态不可晋升")
    envs = ordered_envs(conn)
    codes = [e["code"] for e in envs]
    if target_code not in codes:
        raise HTTPException(400, "目标环境非法")
    to_env = env_by_code(conn, target_code)
    cur_env = None
    if r["current_env_id"]:
        cur_env = conn.execute(
            "SELECT * FROM environments WHERE id=?", (r["current_env_id"],)
        ).fetchone()
    allow_skip = get_setting(conn, "允许跳级", "否") == "是"
    if cur_env:
        cur_idx = codes.index(cur_env["code"])
        to_idx = codes.index(target_code)
        if to_idx <= cur_idx:
            raise HTTPException(400, "只能向更高环境晋升")
        if not allow_skip and to_idx != cur_idx + 1:
            raise HTTPException(400, "禁止跳级，须逐级晋升")
    else:
        if target_code != "测":
            raise HTTPException(400, "未落地时只能先入测")
    return to_env


def _window_verdict(conn, to_env) -> bool:
    """告警臂唯一闸规：窗开=时段内；窗关=时段外，仍可放行，但必须留痕。
    全站不存在硬拒分支，也没有可切换该行为的开关。"""
    return release_window_open(conn, to_env["id"])


@router.get("/发版单/{release_id}/放行预判")
def preview(release_id: int, 目标环境: str):
    """读侧按写侧规则重放：能否放行、窗态、时段外提示均与写口同一函数产出。"""
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        to_env = _validate_target(conn, r, 目标环境)
        in_window = _window_verdict(conn, to_env)
        return {
            "编号": release_id,
            "目标环境": 目标环境,
            "可放行": True,
            "窗口状态": "窗开" if in_window else "窗关",
            "时段外": not in_window,
            "提示": (
                "目标环境放行窗开启，时段内放行。"
                if in_window
                else "目标环境放行窗已关：时段外仍可放行，将全程标记为时段外并计入对账分桶。"
            ),
        }


@router.post("/发版单/{release_id}/晋升")
def promote(release_id: int, body: PromoteIn):
    # 排他写事务：双人同点时第二请求在锁外等待，拿锁后重读状态，双切被状态校验+唯一索引双保险挡住
    with db(write=True) as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        to_env = _validate_target(conn, r, body.目标环境)
        in_window = _window_verdict(conn, to_env)

        now = now_str()
        try:
            conn.execute(
                """
                INSERT INTO promote_events(release_id, from_env_id, to_env_id, decision, in_window, created_at, note)
                VALUES (?, ?, ?, '放行', ?, ?, ?)
                """,
                (release_id, r["current_env_id"], to_env["id"], int(in_window), now, body.备注),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(400, "该发版单对目标环境已放行，禁止重复晋升")

        new_status = "已放行" if body.目标环境 == "产" else "晋升中"
        released_at = now if body.目标环境 == "产" else r["released_at"]
        # 时段外标志跟随最近一次晋升；落产那笔为时段外时，产版本即带时段外事实
        conn.execute(
            """
            UPDATE releases SET status=?, current_env_id=?, released_at=?, out_of_window=? WHERE id=?
            """,
            (new_status, to_env["id"], released_at, int(not in_window), release_id),
        )
        # 名册与实例同一事务同标志写入：任一失败整体回滚到时段外操作前，不存在半截态
        set_app_env_version(conn, r["app_id"], to_env["id"], r["target_version"], in_window)
        upsert_instance(
            conn, r["app_id"], to_env["id"], r["target_version"],
            "探针绿", release_id, in_window,
        )
        scope = "时段外" if not in_window else "时段内"
        audit(conn, "晋升", release_id, f"{scope}放行到{body.目标环境}", body.备注)
        return {
            "发版单": row_to_dict(get_release(conn, release_id)),
            "窗口状态": "窗开" if in_window else "窗关",
            "时段外": not in_window,
            "回执": f"{scope}放行成功：已晋升到{body.目标环境}，名册与实例已同步。",
        }
