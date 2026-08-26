# 开发进度

> 动态进度跟踪。方案见 `EXECUTION_PLAN.md`，任务拆解见 `IMPLEMENTATION_PLAN.md`。

## 当前状态（2026-08-26）

**M6 以创作者为中心 —— 7.1~7.10 全部完成**（253 测试全绿 + 2 live 跳过）

| 任务 | 产出 | 状态 |
|---|---|---|
| 7.1 schema 0.2 | Brief（12 字段）/Scene 新字段（景别·规划时长·sfx·候选·首尾帧预埋）/ReferenceImage 图组/校验扩展/load 0.1→0.2 内存升版 | ✅ |
| 7.2 风格模板提示词模块 | prompt_prefix/prompt_lighting/quality_suffix/shot_size_hint + compose_image_prompt 确定性拼装（brief 光影覆盖模板） | ✅ |
| 7.3 Director 简报感知 | brief/节奏/景别注入 + rewrite_scene 单镜重写（narration 默认不动/重试×2） | ✅ |
| 7.4 vision 种子缓存 + 参考图注入 | seed 缓存键（候选互不串键）+ reference_png + 渠道能力标志（**live 验证** wanx2.1-imageedit 图生图；flux 降级） | ✅ |
| 7.5 候选图生成 | 每镜 N 张 img_<sid>_v1..vN（种子复用断点 0 重复调用/孤儿清理/默认选 v1）+ builder 用选中图 | ✅ |
| 7.6 edits.py 编辑 API | update_scenes 放开文案/update_brief/select_image_candidate/参考图增删/BGM 早期上传/reroll/refine/rewrite，全部落盘+按需失效下游（`_invalidate_from` 含 node 自身） | ✅ |
| 7.7 Web 策划页 | Brief 全字段表单 + 参考图上传（angle/role/缩略图/删除）+ BGM 上传 + 渠道能力自适应提示，侧边栏五页按创作流排序 | ✅ |
| 7.8 分镜确认页改版 | 文案可编辑/景别·规划时长·sfx/增删排序/每镜 AI 重写/候选画廊（选中✓·改选·reroll·refine 按能力显隐）+ tasks.py scene_id 参数 ad-hoc 分发（不走 run_task 无自动重试）+ 进度 fragment 挂 main 跨页可见 | ✅ |
| 7.9 默认人审叙事 | 一键全链路降级为「自动模式（高级）」expander + 分镜页流程提示 + CLI --yes 措辞 + storyboard 打印扩展 | ✅ |
| 7.10 文档收尾 | README M6 章（六阶段映射/渠道能力表/N=3 成本/迁移说明）+ 里程碑表 + 本文件 + versions.md schema 0.2 | ✅ |

要点（M6）：
- **编辑失效语义**：`edits._invalidate_from(project, node)` 含 node 自身（update_brief/
  候选改选/参考图增删/reroll/refine —— 编辑作废了 node 的产物）；`pipeline._invalidate_downstream`
  仅 node 之后（update_scenes/rewrite_scene —— direct 产物已含人改保持 done，confirm 起重跑）。
  候选改选只失效 timeline+export，gen_assets 素材不重跑。
- **候选图种子语义**：生成时总传显式 seed（已有 seed 复用=断点续跑 0 重复调用，或新
  random.randint），seed 进缓存键（同 prompt 的 v1/v2 不串键）；`cache.put(record_seed=...)`
  让 seedless 调用者写 seedless 键但 sidecar 记实际 seed。精修 refine 用「选中图作参考 +
  同种子」确定性重绘，新资产附 reference_asset_id。
- **渠道能力降级**：`ImageProvider.supports_reference_image` 类属性 + `providers.image_capabilities`
  无 key 内省；siliconflow 无 img2img → 策划页提示降级 + 精修按钮隐藏 + refine 抛 ValueError；
  qwen 走 wanx2.1-imageedit（live 已验证）。
- **ad-hoc 任务**（reroll/refine/rewrite）：`tasks.start_task(scene_id=...)` 分发到
  `edits.*`（不走 run_task：无 done 跳过/自动重试，人类触发单次，失败直接报错）；
  进度/结果 fragment 挂在 main()，全页可见可消费。
- **参考图 id**：`Project.reference_seq` 计数器持久在 project.json，单调递增删除不复用。
- **测试**：253 = 222（7.4 前）+ 31（7.5/7.6 迁移与新增）+ 5（7.7）+ 9（7.8）+ 2（7.9）；
  AppTest 断言按钮/字幕按 key（st.image 不做断言）；file_uploader 无法在 AppTest 设置
  → 上传逻辑在 core 层（edits.py），web 测试只断 widget 存在；`at.button(key=缺失)` 抛
  KeyError（`_has_button` 辅助判断按钮显隐）。

### 下一步

- **M7 动态化**：`Scene.end_image_asset_id`（首尾帧）已预埋；PIPELINE_NODES 增 `animate`
  节点 + 旧项目 pipeline 键插入 shim；分镜运镜指令入剪映草稿。
- **M8 音频**：`Scene.sfx` → `assets/sfx/` 引用；`bgm_hint` 卡点剪辑（BGM 早期上传已交付）。
- **M9 止损转场**：0.3s 闪白/震动覆盖不可修帧（未来 `VideoClip.transition` 字段，刻意不进 0.2）。
- 题材模板积累（templates/）；多任务并行/进度条跨页已随 7.8 部分解决（fragment 挂 main）。

---

## M5 工作台后台线程化 + 真进度条（2026-08-24）—— 6.1~6.3 全部完成

（183 测试全绿；见 README M5 章节。要点：线程纪律/worker 只写 TaskContainer；fragment 轮询
st.progress；按钮防双开；save 线程锁。）

## M4 Streamlit 工作台 —— 5.1~5.7 全部完成（2026-08-23）

**M4 Streamlit 工作台 —— 5.1~5.7 全部完成**（171 测试全绿 + 四功能区 + avpo web 启动器）

| 任务 | 产出 | 状态 |
|---|---|---|
| 5.0 依赖 | `streamlit==1.62.0`（pyproject/requirements 同步）+ `app.web` 包注册 | ✅ |
| 5.1 core 扩展 | `app/core/env.py`（统一环境入口）+ `ProjectStore.list_project_ids/list_projects` + `pipeline.update_scenes`（narration 守卫 + 下游失效）+ `run_direct/run_gen_assets` 进度回调（as_completed 主线程回调）+ `tests/test_pipeline.py` | ✅ 11 新测试 |
| 5.2 启动器 + 四页 | `avpo web`（USERPROFILE/HOME 重定向 data/webhome 禁写用户目录 + localhost 安全面）+ `.streamlit/config.toml` + `app/web/app.py` | ✅ 手动启动验证通过 |
| 5.3 项目管理页 | 项目卡片（进度/成本）+ 新建表单（风格/渠道/音色）+ 坏 JSON 内联容错 | ✅ |
| 5.4 流水线页 | 5 节点徽章 + 单节点按钮 + 一键全链路（st.status 阶段文字）+ 错误记录 + zip 下载 | ✅ |
| 5.5 分镜确认页 | 逐场景编辑 visual/image_prompt/motion（narration 只读）+ 保存落盘下游重置 + 确认按钮 | ✅ |
| 5.6 成本面板 | 总成本/预算 metric + 场景/资产明细 + 超预算告警 | ✅ |
| 5.7 文档 | README M4 章节 + 里程碑表 + 本文件 | ✅ |

要点（M4）：
- **复用而非重写**：四页全部直接调 `app/core/pipeline.py` 五节点函数，done 跳过/重试/落盘/git
  快照/断点续跑语义与 CLI 完全一致，浏览器与 CLI 混用安全。
- **同步阻塞模型**（M5 已改造为后台线程 + 真进度条）：gen_assets 真实 API 1~2 分钟，
  `st.status` 阶段文字实时滚动；`progress` 回调参数（默认 None 向后兼容）为 M5 预留接口。
  生图段 `pool.map` → `submit + as_completed`，回调只在主线程发出（Streamlit 线程限制）。
- **分镜编辑不变量**：narration 只读（"拼接=原文"逐字校验）；visual/image_prompt/motion 可改，
  保存后 confirm 及下游重置 pending —— 文案未变时 TTS sidecar 缓存命中，重跑成本可忽略。
- **禁写用户目录**：streamlit 磁盘缓存硬编码 `~/.streamlit`，`avpo web` 用 subprocess env
  重定向 USERPROFILE/HOME → `<数据目录>/webhome`（data/ 已 gitignore）。
- **测试**：AppTest（`streamlit.testing.v1`）9 条全流程；工作台脚本对 core 走模块引用
  （`pipeline.run_*`），monkeypatch 按 `app.core.*` 模块属性打补丁；widget 改动后显式 `at.run()`；
  瞬态 success 不断言（rerun 后消失），断言落盘状态。
- 数据目录 `AVPO_DATA` 环境变量正式生效（此前只存在于注释），`app/core/env.py` 统一解析。

---

## M3 健壮性 —— 4.1~4.6 全部完成（2026-08-22）

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

## 路线图（2026-08-26 更新）

- **M7 动态化**：首尾帧（`Scene.end_image_asset_id` 已预埋）+ 运镜指令入剪映草稿；
- **M8 音频**：`Scene.sfx` → `assets/sfx/` 引用 + `bgm_hint` 卡点剪辑；
- **M9 止损转场**：0.3s 闪白/震动覆盖不可修帧；
- 候选池：Whisper 本地字幕（用户自带音频场景）；SQLite 迁移；PR/DaVinci XML（优先级低于剪映）；
- 工作台多任务并行（当前单会话单任务）；进度条跨页已随 M6-7.8 解决（fragment 挂 main）。

## 钉版记录

见 `docs/versions.md`：剪映 9.7.1、pyJianYingDraft 0.3.0、edge-tts 7.2.8。
