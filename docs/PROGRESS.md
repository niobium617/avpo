# 开发进度

> 动态进度跟踪。方案见 `EXECUTION_PLAN.md`，任务拆解见 `IMPLEMENTATION_PLAN.md`。

## 当前状态（2026-08-19）

**M0 数据层 + 剪映导出 PoC —— ✅ 完成**（28 个测试全绿）

| 任务 | 产出 | 状态 |
|---|---|---|
| 1.1 pydantic schema | `app/core/schema.py` | ✅ |
| 1.2 ProjectStore（原子写 + git） | `app/core/project.py` | ✅ |
| 1.3 CLI 骨架 | `app/cli.py` | ✅ |
| 1.4 pyJianYingDraft 调研 | `docs/jyd_notes.md` | ✅ |
| 1.5 导出适配器 | `app/export/jianying.py` | ✅ |
| 1.6 golden 测试 | `tests/test_m0_export.py` | ✅ |
| 1.7 剪映打开验证 | `tests/opened_log.md` | ✅ 剪映 9.7.1 成功打开 |

**M0 关键结论**：pyJianYingDraft 0.3.0 生成的 11.0.0 格式明文草稿在剪映 9.7.1 可直接打开 —— 全案最大风险消除，无需模板兜底。

## 待办

### M1 素材链路（当前阶段，IMPLEMENTATION_PLAN §2）

| 任务 | 产出 | 状态 |
|---|---|---|
| 2.1 edge-tts 20 条稳定性实测 | `tests/edge_tts_log.md` | ✅ 20/20 一次通过 → 定版 |
| 2.2 TTS 协议 + edge 实现 | `app/tts/base.py` `app/tts/tts_edge.py` | ✅ 9 测试 |
| 2.3 字幕聚合 | `app/tts/subs.py` | ✅ |
| 2.4 FLUX 生图（SiliconFlow） | `app/vision/flux.py` | ✅ 代码+8 mock 测试；真实 API 验证并入 2.8 |
| 2.5 生图缓存 | `app/vision/cache.py` | ✅ |
| 2.6 任务状态机 | `app/core/state.py` | ✅ 9 测试 |
| 2.7 分镜生成（DeepSeek 强制 JSON） | `app/director/director.py` | ⬜ |
| 2.8 素材链路测试 | `tests/test_m1_assets.py` | ⬜ |

**退出条件**：一条真实文案自动产出配音 + 字幕 + 3 张图，全部归档，`avpo status` 状态树正确。

### M2 端到端管线（§3）→ M3 健壮性（§4）

## 钉版记录

见 `docs/versions.md`：剪映 9.7.1、pyJianYingDraft 0.3.0、edge-tts 7.2.8。
