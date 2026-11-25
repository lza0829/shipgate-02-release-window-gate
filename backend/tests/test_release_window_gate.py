"""放行维护时段闸夹具（告警臂：时段外放行 + 可核留痕，整站唯一规则）。

覆盖：
1. 时段关闭点放行：允许落产，但时段册/发版单/名册/实例/总览/对账/审计同刷且一致
2. 时段开放点放行：普通时段内放行不带时段外标记
3. 双人同点：并发放行不得双切产版本（状态重读 + 部分唯一索引双保险）
4. 半截回滚：名册写后实例写失败，整笔回到操作前，无半截态
5. 读侧预判按写侧规则回放，不是红色提示条
6. 旧库迁移幂等且硬拒开关被清除
"""
from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.unlink(tmp.name)
    monkeypatch.setenv("SHIPGATE_DB", tmp.name)
    # 确保拿到的是绑定本临时库的全新 app
    import importlib
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    import app.seed
    importlib.reload(app.seed)
    app.seed.seed_if_empty()
    import app.main
    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c
    os.unlink(tmp.name)


def _seed_release_id(client) -> int:
    return client.get("/api/发版单").json()[0]["编号"]


def _toggle_window(client, env: str, status: str):
    return client.post("/api/放行窗/切换", json={"环境": env, "状态": status})


def _promote(client, rid: int, env: str):
    return client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": env})


def _release_snapshot(client, rid: int):
    """把同一事实在各读侧的投影一次取齐，供一致性断言。"""
    release = client.get(f"/api/发版单/{rid}").json()
    overview = client.get("/api/总览").json()
    reconcile = client.get("/api/对账").json()
    audit_rows = client.get("/api/审计").json()
    instances = client.get("/api/实例").json()
    return release, overview, reconcile, audit_rows, instances


# ---------- 夹具1：时段关闭点放行（告警臂）----------
def test_closed_window_promotion_succeeds_and_is_traced_everywhere(client):
    rid = _seed_release_id(client)
    # 先进到预（窗开）
    assert _promote(client, rid, "预").status_code == 200
    # 关掉产环境放行窗
    _toggle_window(client, "产", "窗关")

    # 读侧预判：窗关、时段外、仍可放行——与写口同一规则
    preview = client.get(f"/api/发版单/{rid}/放行预判", params={"目标环境": "产"}).json()
    assert preview["可放行"] is True
    assert preview["窗口状态"] == "窗关"
    assert preview["时段外"] is True

    resp = _promote(client, rid, "产")
    assert resp.status_code == 200
    body = resp.json()
    assert body["时段外"] is True
    assert "时段外" in body["回执"]

    release, overview, reconcile, audit_rows, instances = _release_snapshot(client, rid)

    # 发版单：已放行且带时段外事实
    assert release["状态"] == "已放行"
    assert release["时段外"] == 1

    # 总览：已放行计入时段外分桶，不进时段内桶
    dist = overview["分布"]
    assert dist["已放行"] == 1
    assert dist["已放行-时段外"] == 1
    assert dist["已放行-时段内"] == 0

    # 名册（环境版本）与实例：产版本已切、已挂，且双双带时段外标志
    prod_version = next(
        v for v in overview["环境版本"]
        if v["环境"] == "产" and v["应用"] == release["应用"]
    )
    assert prod_version["版本"] == release["目标版本"]
    assert prod_version["时段外"] == 1
    prod_instance = next(
        i for i in instances
        if i["环境"] == "产" and i["应用"] == release["应用"]
        and i["版本"] == release["目标版本"]
    )
    assert prod_instance["发版单编号"] == rid
    assert prod_instance["时段外"] == 1

    # 对账：时段外分桶 + 晋升事件窗态
    assert reconcile["发版统计"]["时段外放行"] == 1
    assert reconcile["发版统计"]["时段内放行"] == 0
    assert reconcile["晋升时段外"] == 1
    ledger = next(
        x for x in reconcile["名册与实例"]
        if x["环境"] == "产" and x["应用"] == release["应用"]
    )
    assert ledger["名册时段外"] == 1
    assert "1" in str(ledger["实例时段外们"])

    # 审计：可过滤出时段外放行事实，且窗态写在动作里
    filtered = client.get("/api/审计", params={"时段外": True}).json()
    assert any(a["关联编号"] == rid and "时段外" in a["动作"] for a in filtered)
    assert any(a["关联编号"] == rid and a["动作"] == "时段外放行到产" for a in audit_rows)


# ---------- 夹具2：时段开放点放行 ----------
def test_open_window_promotion_is_not_marked_out_of_window(client):
    rid = _seed_release_id(client)
    resp = _promote(client, rid, "预")
    assert resp.status_code == 200
    assert resp.json()["时段外"] is False
    assert resp.json()["窗口状态"] == "窗开"

    release = client.get(f"/api/发版单/{rid}").json()
    assert release["状态"] == "晋升中"
    assert release["时段外"] == 0

    overview = client.get("/api/总览").json()
    assert overview["分布"]["已放行-时段外"] == 0
    row = next(
        v for v in overview["环境版本"]
        if v["环境"] == "预" and v["应用"] == release["应用"]
    )
    assert row["时段外"] == 0

    reconcile = client.get("/api/对账").json()
    assert reconcile["晋升时段内"] == 1
    assert reconcile["晋升时段外"] == 0
    assert client.get("/api/审计", params={"时段外": True}).json() == []


# ---------- 夹具3：双人同点，不得双切 ----------
def test_concurrent_promotion_cannot_double_switch(client):
    rid = _seed_release_id(client)
    _promote(client, rid, "预")
    _toggle_window(client, "产", "窗关")  # 顺带压并发下的时段外路径

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _promote(client, rid, "产"), range(2)))

    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 400], statuses
    success = next(r for r in results if r.status_code == 200).json()
    assert success["时段外"] is True

    # 产版本只切一次：只有一笔放产事件，名册/实例各一行目标版本
    reconcile = client.get("/api/对账").json()
    assert reconcile["晋升事件数"] == 2  # 预一笔 + 产一笔，产绝不重复
    release = client.get(f"/api/发版单/{rid}").json()
    assert release["状态"] == "已放行"
    instances = [
        i for i in client.get("/api/实例").json()
        if i["环境"] == "产" and i["应用"] == release["应用"]
        and i["版本"] == release["目标版本"]
    ]
    assert len(instances) == 1


# ---------- 夹具4：半截回滚（名册已切实例未挂）----------
def test_instance_failure_rolls_back_roster_and_release(client, monkeypatch):
    rid = _seed_release_id(client)
    _promote(client, rid, "预")
    _toggle_window(client, "产", "窗关")

    # 记录操作前产名册版本
    before = client.get("/api/总览").json()
    app_name = client.get(f"/api/发版单/{rid}").json()["应用"]
    prod_before = next(
        v for v in before["环境版本"] if v["环境"] == "产" and v["应用"] == app_name
    )["版本"]

    # 让实例挂接在名册写入之后抛错
    import app.routers.promote as promote_mod

    def boom(*args, **kwargs):
        raise RuntimeError("实例挂接故障")

    monkeypatch.setattr(promote_mod, "upsert_instance", boom)

    with pytest.raises(RuntimeError):
        _promote(client, rid, "产")

    # 整笔回到操作前：发版单未放行、名册未切、无放产事件、无残留实例
    release = client.get(f"/api/发版单/{rid}").json()
    assert release["状态"] == "晋升中"
    assert release["当前环境"] == "预"
    assert release["时段外"] == 0
    after = client.get("/api/总览").json()
    prod_after = next(
        v for v in after["环境版本"] if v["环境"] == "产" and v["应用"] == app_name
    )["版本"]
    assert prod_after == prod_before
    reconcile = client.get("/api/对账").json()
    assert reconcile["晋升事件数"] == 1  # 只有预那笔，产那笔随事务回滚
    assert reconcile["发版统计"]["时段外放行"] == 0
    assert not any(
        a["关联编号"] == rid and "产" in a["动作"]
        for a in client.get("/api/审计").json()
    )

    # 恢复后同一张单仍可正常时段外落产
    monkeypatch.undo()
    ok = _promote(client, rid, "产")
    assert ok.status_code == 200 and ok.json()["时段外"] is True


# ---------- 夹具5：硬拒分支已不存在，全站只剩告警臂 ----------
def test_hard_reject_arm_removed_single_rule(client):
    rid = _seed_release_id(client)
    _promote(client, rid, "预")
    _toggle_window(client, "产", "窗关")
    # 即使有人手工塞回旧硬拒开关，写口也不读它：判定路径只有告警臂一条
    client.post("/api/设置", json={"键": "维护窗硬拒", "值": "是"})
    resp = _promote(client, rid, "产")
    assert resp.status_code == 200
    assert resp.json()["时段外"] is True
    release = client.get(f"/api/发版单/{rid}").json()
    assert release["状态"] == "已放行"
    # 下一次迁移会把残留旧键清掉，整站不留双轨
    from app import db as db_mod
    with db_mod.db() as conn:
        db_mod.run_migrations(conn)
        assert conn.execute(
            "SELECT 1 FROM settings WHERE key='维护窗硬拒'"
        ).fetchone() is None


# ---------- 夹具6：旧库迁移幂等，重复执行不报错 ----------
def test_migration_idempotent_and_cleans_legacy_flag(client):
    from app import db as db_mod
    # 造一个带旧硬拒开关的库状态
    client.post("/api/设置", json={"键": "维护窗硬拒", "值": "是"})
    with db_mod.db() as conn:
        db_mod.run_migrations(conn)
        db_mod.run_migrations(conn)  # 再跑一次不报错
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(releases)").fetchall()}
        assert "out_of_window" in cols
        legacy = conn.execute(
            "SELECT value FROM settings WHERE key='维护窗硬拒'"
        ).fetchone()
        assert legacy is None
