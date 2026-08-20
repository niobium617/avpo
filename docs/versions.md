# 钉版记录

> 剪映格式漂移是最大风险（EXECUTION_PLAN §9）。所有验证基于以下钉住的版本，升级需重新验证。

| 组件 | 版本 | 确认日期 | 备注 |
|---|---|---|---|
| 剪映专业版 | 9.7.1 | 2026-08-19 | 用户「关于」页确认；草稿目录 `com.lveditor.draft` |
| pyJianYingDraft | 0.3.0（PyPI 最新） | 2026-08-19 | 生成草稿 `version=360000` / `new_version=110.0.0`（剪映 11.0.0 格式） |
| edge-tts | 7.2.8 | 2026-08-20 | **M1-2.1 定版**：20/20 一次通过（tests/edge_tts_log.md）。条件：必须传 `boundary="WordBoundary"`（默认 SentenceBoundary 无词级时间戳）；调用方须 ×3 重试（run_task 已满足） |

✅ **兼容性已实测（2026-08-19，M0-1.7）**：剪映 9.7.1 成功打开 pyJianYingDraft 0.3.0 生成的 11.0.0 格式草稿（见 tests/opened_log.md）。剪映升级后需重新验证。
