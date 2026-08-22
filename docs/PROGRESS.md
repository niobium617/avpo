# 开发进度

> 动态进度跟踪。方案见 `EXECUTION_PLAN.md`，任务拆解见 `IMPLEMENTATION_PLAN.md`。

## 当前状态（2026-08-22）

**M3 健壮性 —— 4.1~4.6 全部完成**（139+ 测试全绿 + 断点 0 重复调用 + 成本告警 + 3 模板回归 + 打开成功率 3/3 ✅）

| 任务 | 产出 | 状态 |
|---|---|---|
| 4.1 断点续跑 | `app/tts/cache.py`（sidecar + narration_hash 校验）+ `_gen_scene_voice` 缓存跳过 + `tests/test_resume.py` | ✅ kill -9 重跑 **0 次重复 API 调用**（5 测试） |
| 4.2 成本统计 | `app/core/cost.py` + CLI `avpo cost [--budget 5]` + `tests/test_cost.py` | ✅ 明细/总计/超预算告警（4 测试） |
| 4.3 错误分类 | `app/core/errors.py`（余额/限流/网络/格式/剪映五类 + 修复动作）+ `PipelineError.kind/hint` 落盘 + CLI 失败提示接入 + `tests/test_errors.py` | ✅ 三类错误均有测试（9 测试） |
| 4.4 风格模板 | `templates/{fast_talk,emotional,explainer}.json` + `app/core/styles.py` + `ProjectConfig.style` + director motion_hint 注入 + 导出字幕样式参数化 + BGM 轨（缺失降级）+ `tests/test_regression.py`/`test_styles.py` | ✅ 3 模板 × 3 条全链路 12/12 通过 |
| 4.5 成功率汇总 | `tests/opened_log.md` 汇总段 | ✅ 3/3 = 100% ≥90% |
| 4.6 文档收尾 | `README.md`（安装/使用/成本/架构图）+ 本文件 | ✅ |

要点（M3）：
- **断点续跑**：配音 sidecar 缓存（`assets/vo_<scene>.json` 存词级时间戳 + narration_hash），
  mp3 与 sidecar 都在且文案未变 → 跳过合成；文案变了缓存自动失效。生图侧 prompt_hash 已有同语义。
  状态与产物同一次 git 提交（M2 已有），kill -9 后 `avpo run` 直接续跑。
- **成本账本**：纯 SUM 不二次对账；LLM 分镜成本均摊到 `scene.cost.llm`，生图记 `scene.cost.image`
  与 `asset.cost`（汇总只取场景侧避免双计）；默认预算 ¥5/项目。
- **错误分类**：`classify(exc)` 关键词归入 API 余额/限流/网络 vs 格式 vs 剪映五类，
  失败落盘 `PipelineError.kind/hint`，CLI 打印 `[分类] 详情（修复: ...）`；抛错方显式 hint 优先。
- **风格模板**：`templates/*.json` = 运镜指导（注入 director 系统提示词）+ 字幕样式
  （字号/低位 y/描边宽）+ 可选 BGM（`assets/bgm.mp3`，缺失降级跳过）；`default` 内建 = MVP 固定样式。
- 回归护栏：`test_regression.py` 3 模板 × 3 条全链路（mock 渠道确定性），9/9 导出产物完整。

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

### M3 健壮性（IMPLEMENTATION_PLAN §4）

- [x] 4.1 断点续跑（TTS sidecar 缓存 + kill -9 重跑 0 次重复 API 调用，`app/tts/cache.py` + `tests/test_resume.py`）✅（2026-08-22）
- [x] 4.2 成本统计（`app/core/cost.py` + CLI `avpo cost`，默认预算 ¥5/项目）✅
- [x] 4.3 错误分类与可读化（`app/core/errors.py` 五类 + 修复动作，落盘 `PipelineError.kind/hint`）✅
- [x] 4.4 风格模板（`templates/` 3 模板 + schema/director/export 接线 + 3×3 回归测试）✅
- [x] 4.5 opened_log 汇总（3/3 = 100% ≥90%）✅
- [x] 4.6 README + 架构图 ✅

**退出条件**：成功率达标（3/3 ✅）；kill 恢复（test_resume ✅）、成本告警（test_cost ✅）、
错误提示（test_errors ✅）全部验证；MVP 可交付使用。
→ ✅ **已达成**（2026-08-22）。**M3 正式关闭。**

## 下一步：M4 UI 工作台（阶段 1）

Streamlit 工作台：项目管理视图 + 流水线可视化 + 分镜确认页 + 成本面板。
（待用户确认范围后再拆任务。）

## 钉版记录

见 `docs/versions.md`：剪映 9.7.1、pyJianYingDraft 0.3.0、edge-tts 7.2.8。
