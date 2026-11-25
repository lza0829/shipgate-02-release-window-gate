# 发版闸（Shipgate）

骨架：发版申请入闸 → 环境晋升裁决 → 放行/回滚窗 → 发布结果对账

- 前端 http://127.0.0.1:6800
- API http://127.0.0.1:11800
- 数据库环境变量 `SHIPGATE_DB`（容器内默认 `/data/shipgate.db`）

```bash
docker compose up -d --build
```

## 放行维护时段闸（唯一规则：时段外告警仍可落产）

- 时段判定只有一条：目标环境存在「窗开」的放行窗即时段内，否则时段外；全站无硬拒分支，旧设置项 `维护窗硬拒` 已迁移清除。
- 时段外放行允许落产，但同一事务内强制留痕：`promote_events.in_window`、`releases.out_of_window`、名册 `app_env_versions.in_window`、实例 `instances.in_window` 同刷；失败整体回滚到操作前，无名册/实例半截态。
- 并发双保险：写口走排他事务并在锁内重读状态，部分唯一索引 `ux_promote_release_target` 保证同一发版单对同一环境只有一笔放行，双人同点不会双切产版本。
- 读侧按写侧规则重放：`GET /api/发版单/{id}/放行预判` 与写口共用同一校验/窗态函数；总览「已放行」分时段内外两格，对账有「时段外放行」分桶，审计支持 `?时段外=true` 过滤。
- 本闸只管放行（晋升）维护窗；产环境回滚的回滚窗开闭另立，不在本闸范围。

```bash
cd backend && python -m pytest tests/ -v
```
