# 开发进度

> 动态进度跟踪。方案见 `EXECUTION_PLAN.md`，任务拆解见 `IMPLEMENTATION_PLAN.md`。

## 当前状态（2026-08-20）

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

3.1 时间线组装（scene 时长 = 对应配音段实际时长；运镜轮转，`app/timeline/builder.py`）
3.2 导出扩展（字幕样式/音频淡入淡出/封面首帧，`app/export/jianying.py`）→ 剪映打开验证
3.3 一键流水线 `avpo run`（direct→confirm→gen_assets→timeline→export，confirm 输出分镜 y/n）
3.4 e2e 测试（固定 seed 固定输出 + 耗时记录）
3.5 优化 ≤5 分钟（并发生图 3~4 张、LLM 流式、done 跳过）

**退出条件**：`avpo run` 一条 60s 口播全自动 ≤5 分钟，剪映打开成功。

**退出条件**：一条真实文案自动产出配音 + 字幕 + 3 张图，全部归档，`avpo status` 状态树正确。

### M2 端到端管线（§3）→ M3 健壮性（§4）

## 钉版记录

见 `docs/versions.md`：剪映 9.7.1、pyJianYingDraft 0.3.0、edge-tts 7.2.8。
