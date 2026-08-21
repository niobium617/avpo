# 开发进度

> 动态进度跟踪。方案见 `EXECUTION_PLAN.md`，任务拆解见 `IMPLEMENTATION_PLAN.md`。

## 当前状态（2026-08-21）

**M2 端到端管线 —— 3.1~3.5 全部完成**（115 测试全绿 + 真实 60s 口播 1.8min + 剪映打开验证 ✅）

| 任务 | 产出 | 状态 |
|---|---|---|
| 3.1 时间线组装 | `app/timeline/builder.py` + `run_timeline` + CLI `timeline` + schema 扩展 | ✅ |
| 3.2 导出扩展 | 字幕样式/淡入淡出/元信息 + `run_export` + CLI `export` + 下游失效重跑 | ✅ 剪映 9.7.1 打开验证通过 |
| 3.3 一键流水线 | CLI `run`（direct→confirm→gen_assets→timeline→export）+ edge-tts NoAudioReceived 重试 + GBK 控制台容错 | ✅ 真实全链路通过 |
| 3.4 e2e 测试 | `tests/test_e2e.py`（固定输入→逐字段一致）+ `tests/e2e_time_log.md` | ✅ |
| 3.5 优化 | 并发生图 4 张并行（gen_assets 120.6s→79.7s）+ done 跳过 | ✅ 60s 口播全自动 **106.9s ≈ 1.8min ≤5min** |

要点：
- scene 时长 = 配音段 mutagen 实际时长，场景首尾相接；`Scene.start_ms` 记录全局起点；
- 字幕保持场景内相对时间戳（M1 产物），**导出时**按 `scene.start_ms` 平移到全局时间轴 —— 单一真相源，组装幂等；
- `AudioClip.duration_ms`（组装写入）优先于 `voiceover.duration_ms` 用于导出音频段时长；
- 依赖检查：`gen_assets` 未 done 时 timeline 节点标 failed 并提示先跑 gen-assets；export 依赖 timeline done；
- 任一节点成功后**下游节点重置 pending**（产物变了强制重跑）；
- 导出扩展：字幕 6 号/居中/黑描边/y=-0.8；配音段 300ms 淡入淡出；`draft_name`/`tm_duration` 元信息；封面取首帧自动生成。

**M1 素材链路 —— ✅ 完成**（86 测试全绿 + 真实全链路验证通过）

| 任务 | 产出 | 状态 |
|---|---|---|
| 2.1 edge-tts 20 条稳定性实测 | `tests/edge_tts_log.md` | ✅ 20/20 一次通过 → 定版 |
| 2.2 TTS 协议 + edge 实现 | `app/tts/base.py` `app/tts/tts_edge.py` | ✅ |
| 2.3 字幕聚合 | `app/tts/subs.py` | ✅ 含标点回贴（edge-tts 词事件无标点） |
| 2.4 生图（多渠道） | `app/vision/base.py` `flux.py` `qwen.py` | ✅ siliconflow FLUX + dashscope 万相，同接口 |
| 2.5 生图缓存 | `app/vision/cache.py` | ✅ |
| 2.6 任务状态机 | `app/core/state.py` | ✅ |
| 2.7 分镜生成 | `app/director/director.py` | ✅ 强制 JSON + narration 逐字覆盖校验 |
| 2.8 素材链路测试 | `tests/test_m1_assets.py` + `app/core/pipeline.py` + CLI `direct`/`gen-assets`/`--provider` | ✅ mock 回归 + **真实全链路通过**（pytest -m live，68.9s） |

**M1 退出条件达成**：`proj_m1demo` 一条真实文案自动产出 配音 2 段 + 字幕 2 行 + 图 2 张，全部归档，`avpo status` 状态树正确。

**渠道变更（用户决策）**：暂无硅基流动 key（未充值）→ 新增 **dashscope（千问）渠道**：qwen-plus 分镜 + 通义万相 wanx2.1-t2i-turbo 生图（¥0.16/张估账）。siliconflow（FLUX）保持为默认渠道，拿到 key 后 `avpo new --provider siliconflow` 即可切回。

## 待办

### M2 端到端管线（IMPLEMENTATION_PLAN §3）

- [x] 3.1 时间线组装（scene 时长 = 对应配音段实际时长；运镜取 director 已分配值，`app/timeline/builder.py`）✅
- [x] 3.2 导出扩展（字幕样式/音频淡入淡出/封面首帧，`app/export/jianying.py`）→ 剪映打开验证 ✅（2026-08-21）
- [x] 3.3 一键流水线 `avpo run`（direct→confirm→gen_assets→timeline→export，confirm 输出分镜 y/n）✅
- [x] 3.4 e2e 测试（固定 seed 固定输出 + 耗时记录）✅
- [x] 3.5 优化 ≤5 分钟（并发生图 3~4 张、LLM 流式、done 跳过）✅

**退出条件**：`avpo run` 一条 60s 口播全自动 ≤5 分钟，剪映打开成功。
→ ✅ **已达成**（2026-08-21）：56.3s 口播全链路 106.9s ≈ 1.8min；剪映 9.7.1 打开验证通过（`avpo_m2_final_draft`，见 opened_log）。**M2 正式关闭。**

## 下一步：M3 健壮性（IMPLEMENTATION_PLAN §4）

4.1 断点续跑 / 4.2 成本统计 `avpo cost` / 4.3 错误分类可读化 / 4.4 3 个风格模板回归 / 4.5 导出成功率 ≥90% / 4.6 README + 架构图。

**退出条件**：一条真实文案自动产出配音 + 字幕 + 3 张图，全部归档，`avpo status` 状态树正确。

### M2 端到端管线（§3）→ M3 健壮性（§4）

## 钉版记录

见 `docs/versions.md`：剪映 9.7.1、pyJianYingDraft 0.3.0、edge-tts 7.2.8。
