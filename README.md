# AVPO —— AI 视频工作流操作系统

> 连接剧本、AI 生成模型与剪映的智能中间层，消除工具间搬运与对齐。
> 以创作者为中心：AI 是协作者/提案者，简化的是工具流程，创作决策权始终在人。

**当前状态：M0~M7 全部完成（MVP 可交付 + 工作台五页 + 创作者中心 + 运镜动态化，276 测试绿）**

## MVP 工作流（营销口播视频）

```
一句话创意
   │ LLM（qwen-plus / DeepSeek-V3）：拆解分镜（强制 JSON，narration 逐字校验）
   ▼
分镜草稿 ──── 人工确认（M6 默认人审：逐镜编辑/选候选图/精修；--yes 仅自动/重跑）
   │ edge-tts：配音（word 时间戳）+ 每镜 N 张候选生图（通义万相 / FLUX，seed 级缓存）
   ▼
素材自动归档 → project.json 更新（状态与产物同一次 git 提交 = 断点续跑基础）
   │ 字幕 = TTS 时间戳聚合，零成本对齐
   ▼
运镜解析（M7：分镜运镜/首尾帧 → 关键帧计划，`animate` 节点）
   │ 结束帧从候选图选（0 额外生图）→ 配音后静态尾拍 0.4s
   ▼
时间线组装（场景时长 = 配音实际时长，mutagen 读取）
   │ pyJianYingDraft 生成明文草稿（关键帧运镜 + 字幕样式按风格模板 + 可选 BGM）
   ▼
剪映草稿目录/zip → 剪映打开精修 → 成片
```

## 快速开始

```bash
# 0. 环境：Python >= 3.11（Windows 11 实测，剪映 9.7.1 钉版）
py -3.11 -m venv .venv
.venv/Scripts/activate            # Windows Git Bash

# 1. 安装
pip install -e .                  # 安装运行依赖 + avpo 命令

# 2. 配置渠道（素材链路需要）
cp .env.example .env              # 填入 DASHSCOPE_API_KEY（千问）或 SILICONFLOW_API_KEY

# 3. 使用
avpo new proj_001 --title "AI 产品口播" --style fast_talk
avpo direct proj_001 --text "口播文案全文..."           # 分镜提案（按策划页 Brief）
avpo run proj_001 --text "口播文案全文..."              # 展示分镜后确认（默认人审）
avpo run proj_001 --text "口播文案全文..." --yes        # --yes 跳过人工确认（自动/重跑）
avpo status proj_001                                   # 状态树（节点/场景/素材）
avpo cost proj_001                                     # 成本明细 + 超预算告警

# 4. 图形工作台（浏览器操作，M4）
avpo web                                               # http://localhost:8501
```

## 常用命令

| 命令 | 说明 |
|---|---|
| `avpo new <pid> [--style 模板] [--provider 渠道]` | 建项目；风格模板 default/fast_talk/emotional/explainer |
| `avpo run <pid> --text "..." [--yes]` | 一键流水线：direct→confirm→gen_assets→**animate**→timeline→export（`--yes` 跳过人工确认，仅建议自动/重跑） |
| `avpo direct <pid> --text "..."` / `gen-assets` / `animate` / `timeline` / `export` | 单节点执行（已 done 节点自动跳过 = 断点续跑） |
| `avpo status <pid>` | 打印状态树：节点状态、场景、素材、成本 |
| `avpo cost <pid> [--budget 5]` | 成本汇总（LLM + 生图，纯 SUM 不二次对账），超预算告警 |
| `avpo new --provider siliconflow` | 换渠道（siliconflow FLUX / dashscope 通义万相） |
| `avpo web [--port 8501]` | 启动 Streamlit 工作台（见下） |

## 风格模板（M3）

`templates/*.json`，每条 = 运镜指导（注入分镜 LLM）+ 字幕样式 + 可选 BGM：

| 模板 | 适用 | 特点 |
|---|---|---|
| `fast_talk` | 带货/资讯 | 运镜频繁切换，字幕醒目低位 |
| `emotional` | 故事/情感 | 慢运镜 + BGM（策划页上传或放 `assets/bgm.mp3` 即生效，缺失自动降级） |
| `explainer` | 知识/解说 | 字幕大字号偏上，运镜克制 |

## 以创作者为中心（M6）

M6 落地六阶段创作流程（策划 → 画面生产 → 动态 → 音频 → 剪辑 → 导出）的**阶段一 +
阶段二**，并把默认工作流从「全自动」改回「默认人审」：AI 产出提案/候选，创作决策权
始终在创作者。

| 创作阶段 | AVPO 对应 | 里程碑 |
|---|---|---|
| 一、前期策划 | Brief 简报 + 参考图组 + BGM 节奏前置 | M6 ✅ |
| 二、画面生产 | 每镜 N 张候选图 + 人审改选 + 图生图精修 | M6 ✅ |
| 三、动态 | 运镜关键帧（animate 节点）+ 首尾帧静态尾拍（候选图选结束帧） | M7 ✅ |
| 四、音频 | sfx 素材引用 + BGM 卡点对齐（`bgm_hint` 已前置） | M8 ✅ |
| 五、剪辑 | 时间线组装（已完成）+ 卡点编辑增强 | M8 ✅ |
| 六、优化导出 | 止损转场（0.3s 闪白/震动覆盖不可修帧） | M9 ✅ |

### 策划页（阶段一）

工作台「策划」页填写创作简报（Brief）：主题/世界观/画风/时长/平台/主角/剧情/情绪 +
风格关键词包（主色调/光影/人物特征）+ BGM 节奏描述。AI 按简报提案分镜与画面；
保存后 direct 起全下游自动重置待运行。

- **参考图组**：多角度上传（角度/角色标签），支持图生图的渠道作为固定输入注入生图；
  渠道能力自适应提示（见下表）。
- **BGM 上传与卡点**：拷贝到 `assets/bgm.mp3`（导出铺满全片）；「BGM 卡点对齐」开关 +
  节拍数预览（M8，见「音频」章）；`bgm_hint` 节奏描述注入分镜提案。
- **音效库**：上传 mp3/wav 音效入库（M8，切点音效素材来源）。
- 光影指令为独立字段：brief 的光影覆盖风格模板默认光影（`compose_image_prompt`）。

### 分镜确认页（创作者枢纽）

- 文案可编辑（TTS 缓存按文案哈希自动失效重合成）、增删/排序场景、每镜 AI 重写
  （指令驱动，narration 默认不动）；
- 景别 / 规划时长（对齐 BGM 节奏）/ 音效描述 + **切点音效绑定**（M8，下拉选音效库素材）；
- **候选画廊**：每镜 N 张候选（默认 3，`ImageConfig.candidates` 1~6，project.json 可改），
  人审「选中」、换种子重 roll、图生图精修（选中图作参考 + 同种子 → 新候选自动选中）。
  改选只重跑时间线/导出，素材不重跑。

### 渠道能力

| 渠道 | 生图 | 参考图注入（图生图） | 精修 |
|---|---|---|---|
| dashscope（通义万相）| wanx2.1-t2i-turbo | ✅ wanx2.1-imageedit（live 验证）| ✅ |
| siliconflow（FLUX）| FLUX.1-schnell | ❌ 降级为风格关键词包模式 | ❌ |

### 成本预期（N=3 候选）

候选图按张计费：通义万相 ≈ ¥0.16/张 → 每镜 3 候选 ≈ ¥0.48；FLUX ≈ ¥0.02/张 →
每镜 ≈ ¥0.06。重 roll / 精修逐次累计，超预算告警见 `avpo cost`。

### 迁移说明（schema v0.1 → v0.2）

- v0.2 为纯增量字段：旧 project.json 直接加载，内存升版，下次保存持久化；
- 旧式单图 `img_<sid>` 在首次重跑 gen_assets 时清理并全量重生成候选（一次性成本）；
- 生图缓存键升级为 seed 级：旧 seedless 缓存仍可读，但候选流不再命中 → 升级后
  首次重跑全量重生成（一次性成本），之后断点续跑 0 重复调用语义不变。

## 动态化（M7）

六节点流水线中间插入 `animate`：把分镜运镜提案与首尾帧解析为**关键帧计划**
（`MotionPlan`），时间线组装与剪映导出都以该计划为唯一参数源。

- **运镜全关键帧化**：zoom_in_slow（1.0→1.15）/ zoom_out（1.15→1.0）/ pan_left /
  pan_right（position_x ±0.12，恒 1.15 缩放防露边）全部用剪映关键帧线性插值实现
  （pyJianYingDraft `KeyframeProperty.uniform_scale / position_x`）—— M2 时代
  `pan_*` 因无入场动画枚举而降级为静态，M7 解除该限制。
- **首尾帧 = 候选图选结束帧**：分镜确认页候选画廊每张候选可「设为结束帧」（edits
  API，0 额外生图成本）；animate 解析出 0.4s 尾拍（`END_FRAME_MS`），时间线在配音
  后追加静态尾拍，全片时长含尾拍（BGM 对齐依据）；尾拍硬切（M9 起无结束帧的切点
  自动闪白止损，见「转场」章）。
- **单点可调**：缩放/横移幅度、尾拍时长全部集中在 `app/core/motion.py`；animate
  落盘计划、导出兜底解析同源，改参数即全线生效。
- **失效语义**：改结束帧只重跑 animate 及下游（素材不重跑）；animate 成功后重置
  timeline/export。
- **旧项目兼容**：0.2 数据加载即按序补 `animate: pending` 键（内存升版 0.3，文件
  不迁移）；没跑 animate 的旧项目导出时按 `scene.motion` 兜底解析，老草稿运镜不丢。

## 音频（M8）

阶段四/五落地：音效素材引用 + BGM 卡点对齐（BGM 上传已随 M6 交付，M8 让它「会用」）。

- **音效库（sfx 引用）**：策划页上传 mp3/wav 音效入库（`assets/sfx/`，注册为
  `sfx_<名>` 资产，0 API 成本，素材自备）；分镜确认页每场景「切点音效」下拉
  绑定/解绑 —— 时间线组装把音效放到场景全局起点（切点音效），导出为独立 sfx 轨
  （按素材自身时长整段播放，不加淡入淡出）。`Scene.sfx` 文本仍是导演提案的
  标注字段，与素材绑定并存。
- **BGM 卡点对齐**：策划页「BGM 卡点对齐」开关（`ProjectConfig.beat_sync`），开启后
  时间线组装把场景切换点**向前** snap 到最近节拍（≤0.4s，只前移不后移 —— 配音
  不重叠，切点停顿处只有 BGM）；节拍检测在 `app/audio/beats.py`（miniaudio 解码 +
  能量包络峰值，纯本地确定性实现，同文件同输出 → 组装幂等），策划页显示检测到的
  节拍数（按文件 mtime 缓存）；BGM 未上传或解码失败时静默跳过（BGM 属装饰，
  不阻塞主线）。`bgm_hint` 仍注入导演提案（规划时长对齐节奏）。
- **BGM 来源解耦**：导出 BGM 轨优先用项目上传的 `assets/bgm.mp3`，模板 `style.bgm`
  兜底 —— default 模板的项目上传 BGM 后即铺满全片。
- **失效语义**：BGM 上传 / 卡点开关 / 音效入库删除 / 切点绑定都使 timeline（含）
  起重跑（卡点与音效轨是时间线组装产物），素材与运镜计划上游不动。
- **旧项目兼容**：0.1~0.4 数据加载即内存升版 0.5（新字段全部默认值），文件不迁移。

## 转场（M9）

阶段六落地：切点转场覆盖「不可修帧」—— 没有结束帧的场景切点画面停在运镜中途，
直接硬切视觉突兀；M9 用 0.3s 转场止损（闪白/震动），有结束帧的场景切点保持
M7 的静态尾拍硬切（设计过的剪辑）。

- **止损语义**：`Scene.transition` 默认 `auto` = 无结束帧（`end_frame_ms == 0`）的
  切点自动闪白 0.3s 覆盖；有结束帧保持硬切；末场景无切点恒无转场。创作者在分镜
  确认页逐镜覆盖（无/闪白/震动），显式选择始终优先 —— 转场是剪辑决策，人在环上。
- **组装**：时间线组装把 `scene.transition` 解析进 `VideoClip.transition`（挂在
  该场景最后一个 clip 上 —— pyJianYingDraft 转场加在前一段的语义）；有尾拍的
  场景转场落在尾拍 clip 上（主镜头保持无）。
- **导出**：`TransitionType.闪白`（非叠加）/ `震动`（叠加，剪映原生元数据），固定
  0.3s（`_TRANSITION_MS` 单点可调）；草稿 JSON 落地为 materials.transitions +
  段级 extra_material_refs，转场不改变轨道时长（时间线保持相邻拼装）。
- **失效语义**：转场设置使 timeline（含）起重跑（转场是时间线组装产物），
  animate/gen_assets 上游不动。
- **旧项目兼容**：0.1~0.4 数据加载即内存升版 0.5（新字段全部默认值），文件不迁移；
  旧项目跑 timeline 即获得止损默认，可逐镜关掉。

## 本地字幕（M10）

创作者自己的声音优先：分镜页给任一场景上传自己的录音（mp3/wav/m4a），该场景的
TTS 配音被替代，本地 Whisper 转写录音为字幕 —— 0 API 成本、纯本地推理。

- **上传即绑定**：分镜页每场景「上传自带音频」（`Scene.user_audio_asset_id`），
  文件入 `assets/user_audio/`；失效 transcribe（含）起，gen_assets 不动（TTS 素材
  留着无害，时间线优先用录音）。移除音频回到 TTS 配音，失效 gen_assets 起
  （配音与文案字幕重建）。
- **transcribe 节点**：`avpo transcribe` / 工作台流水线页按钮 / 一键全链路第 4 步
  （direct → confirm → gen_assets → **transcribe** → animate → timeline → export）。
  逐场景本地转写（faster-whisper，CTranslate2 CPU int8），模型懒加载单例进程内共享；
  sidecar 缓存 `assets/whisper_<scene>.json`（音频 sha256 + 模型 + 语言三键校验），
  命中 0 次推理 —— 断点续跑语义与 TTS 缓存一致；未检出语音 = 失败（不静默产出空字幕）。
- **模型与语言**：`ProjectConfig.whisper_model`（tiny/base/small/medium/large-v3，
  默认 small）+ `whisper_language`（默认 zh，留空 = 自动检测），策划页即时改配置
  （transcribe 起重跑）；模型首次使用联网下载，缓存于 `<数据目录>/whisper_models`
  （`AVPO_WHISPER_CACHE` 可改位置；国内网络可设 `HF_ENDPOINT=https://hf-mirror.com`）。
  转写文本经 OpenCC t2s 简繁归一（whisper 训练语料简繁混杂，小模型输出会简繁混用；
  繁体项目可关 `SIMPLIFY_CHINESE`）。
- **时间线**：自带音频场景的配音 clip = 录音（时长 = 实际音频，mutagen 读）；转写
  缓存必须有效（音频/模型/语言三键）—— 未转写或音频已更换时组装 FatalError 提示
  先跑 transcribe（字幕与录音不匹配宁可停下）。
- **导出**：录音走既有配音轨（300ms 淡入淡出一致）；whisper 字幕段走既有字幕通道
  （场景内相对时间戳 + 导出按 scene.start_ms 平移，样式同风格模板）。
- **旧项目兼容**：0.1~0.5 数据加载即内存升版 0.6（新字段全部默认值 + pipeline 补
  transcribe 键），文件不迁移；旧项目行为不变（无自带音频场景节点空转）。

## 成本

- 记账：每次 API 调用成功即写 cost 到场景/资产（LLM 分镜成本均摊到场景，生图记
  场景 + 资产），`avpo cost` 只是 SUM，不做二次对账。
- 参考单价：通义万相 wanx2.1-t2i-turbo ≈ ¥0.16/张；FLUX.1-schnell ≈ ¥0.02/张；
  qwen-plus ≈ ¥0.002/千 token；edge-tts 配音免费。
- 预算：默认 ¥5/项目，`avpo cost --budget 10` 自定义；超出显示红色告警。

## 健壮性（M3）

- **断点续跑**：状态与产物同一次 git 提交落盘；kill -9 后重跑，配音/生图走缓存
  （sidecar + prompt_hash），0 次重复 API 调用；改文案缓存自动失效。
- **错误分类**：API（余额/限流/网络）vs 格式校验 vs 剪映草稿，失败时打印修复动作
  （`[分类] 错误详情（修复: ...）`）。
- **失败重试**：Transient 重试 ×3（指数退避），Fatal 不重试；下游节点在上游成功后
  自动重置 pending 重跑。

## Streamlit 工作台（M4~M6）

`avpo web` 启动，五页按创作流排序，与 CLI 共用同一套 pipeline/状态机/数据目录：

| 页面 | 功能 |
|---|---|
| 项目管理 | 项目卡片列表（进度/成本）+ 新建项目（标题/风格模板/渠道/音色） |
| 策划 | Brief 创作简报 + 参考图组（角度/角色标签、渠道能力提示）+ BGM 上传/卡点开关/节拍预览 + 音效库 + Whisper 模型/语言配置 |
| 分镜确认 | 创作者枢纽：文案/景别/规划时长/音效描述与切点音效绑定、自带音频上传/移除/转写字幕预览、增删排序、每镜 AI 重写、候选画廊（选中/重 roll/精修/结束帧）、确认 |
| 流水线 | 7 节点状态徽章 + 单节点运行（含 transcribe）+ 「自动模式（高级）」一键全链路 + 错误记录 + 草稿 zip 下载 |
| 成本面板 | 总成本/预算 metric + 按场景/资产明细 + 超预算告警 |

M5 后台任务模型：
- 节点在 worker 线程执行，页面立即返回不再冻结；进度经线程安全容器（`app/web/tasks.py`）
  传递，`st.fragment(run_every=1s)` 轮询渲染 st.progress（节点内百分比），完成自动刷新徽章；
  进度条与结果区跨页可见（重 roll/精修/AI 重写从分镜页发起，同页消费）；
- 运行中所有运行按钮与分镜编辑/确认禁用（防双开与并发覆盖），失败/中止原因持久展示；
- `ProjectStore.save` 加线程锁串行化 git 提交（防多浏览器会话并发写坏 git 索引）。

M6 编辑语义（`app/core/edits.py`）：策划保存/分镜修改/候选改选/参考图与 BGM 上传等
人类触发的单次编辑全部「落盘 + 按需失效下游」，不走 run_task（无自动重试）——失败
直接报错给创作者看；重 roll/精修/AI 重写为后台任务单次执行，同样不自动重试。

说明：
- `avpo web` 会把 streamlit 的磁盘缓存重定向到 `<数据目录>/webhome`，不写用户目录；
  手动 `streamlit run app/web/app.py` 需在仓库根执行，且缓存会落用户目录；
- 工作台不引入新状态：所有操作走 `app/core/pipeline.py`，浏览器与 CLI 混用安全。

## 架构

```
┌─────────────── app/cli.py ───────────────┐
│ new / run / status / cost / web / 单节点 │
└──────┬───────────────────────────────────┘
       │        ┌── app/web/app.py（Streamlit 工作台 M4~M6）
       │        │  项目管理 / 策划 / 分镜确认 / 流水线 / 成本面板
       │        │  app/web/tasks.py（后台任务 worker + 进度容器 + ad-hoc 分发）
       │        ▼
       │ app/core/pipeline.py（节点编排，下游失效自动重跑）
       │ app/core/edits.py（M6 人类单次编辑 API：落盘 + 按需失效下游）
┌──────▼──────┐ ┌───────────┐ ┌─────────────┐
│ state.py    │ │ schema.py │ │ project.py  │
│ 状态机      │ │ pydantic  │ │ 原子写+git  │
│ 重试/落盘   │ │ 单一真相源 │ │ 提交=历史   │
└──────┬──────┘ └───────────┘ └─────────────┘
       │ 子模块（各节点 fn）
┌──────▼──────────────────────────────────┐
│ director/  文案→分镜（LLM 强制 JSON）    │
│ tts/       edge-tts 配音 + 字幕 + 缓存   │
│ vision/    生图（多渠道）+ prompt_hash   │
│ core/motion.py  运镜解析唯一入口（M7）   │
│ audio/     BGM 节拍检测（M8，卡点对齐） │
│            + 本地转写（M10，faster-     │
│            whisper + sidecar 缓存）      │
│ timeline/  全局时间轴（mutagen 实长 +    │
│            首尾帧尾拍 + sfx/卡点 +       │
│            用户自带音频）                │
│ export/    剪映草稿（pyJianYingDraft +   │
│            关键帧运镜 + sfx/BGM 轨）     │
│ core/      cost 账本 / errors 分类 /     │
│            styles 模板注册表             │
└──────────────────────────────────────────┘
        │ 产物
        ▼
data/projects/<pid>/project.json + assets/ + exports/<pid>_draft/
```

## 目录结构

```
AVPO/
├── app/
│   ├── core/         # schema、store、状态机、pipeline、motion（运镜解析）、成本、错误分类、风格模板、进度事件
│   ├── director/     # AI 导演助手：文案 → 分镜（LLM 强制 JSON + 逐字校验）
│   ├── tts/          # edge-tts 配音 + word 时间戳 + sidecar 缓存 → 字幕
│   ├── vision/       # 生图（dashscope 千问 / siliconflow FLUX）+ seed 级缓存 + 参考图注入
│   ├── audio/        # M8：BGM 节拍检测（miniaudio 解码 + 能量包络峰值）；M10：本地语音转写（faster-whisper + sidecar 缓存）
│   ├── timeline/     # 时间线组装（场景时长 = 配音实际时长 + 首尾帧尾拍 + 音效轨 + BGM 卡点 + 自带音频）
│   ├── export/       # 剪映草稿生成（pyJianYingDraft 封装，关键帧运镜 + 模板字幕样式 + BGM/sfx 轨）
│   ├── web/          # Streamlit 工作台（M4：四功能区；M5：后台线程 + 真进度条）
│   └── cli.py        # avpo 命令入口
├── templates/        # 风格模板 JSON（fast_talk / emotional / explainer）
├── data/             # 项目数据（独立 git 仓库，每次保存自动提交 = 免费版本历史）
└── tests/            # 全链路回归（含 3 模板 × 3 条）、断点续跑、成本、错误分类测试
```

## 里程碑

| 阶段 | 内容 | 验收 | 状态 |
|---|---|---|---|
| M0 | 数据层 + 剪映导出 PoC | 剪映成功打开生成草稿（三轨） | ✅ |
| M1 | 素材链路（TTS/生图/归档/分镜）| 真实文案自动产出配音+字幕+图 | ✅ |
| M2 | 端到端管线（一键 run/并发提速）| 60s 口播全自动 ≤5min（实测 1.8min）| ✅ |
| M3 | 健壮性（断点/成本/错误/模板）| 断点 0 重复调用、成本告警、3 模板回归、打开成功率 3/3 | ✅ |
| M4 | Streamlit 工作台（阶段 1 第一步）| 四功能区（项目管理/流水线/分镜确认/成本面板）+ avpo web 启动器，171 测试全绿 | ✅ |
| M5 | 工作台后台线程化 + 真进度条 | 结构化进度事件（五节点）+ worker 线程 + fragment 轮询 st.progress + 按钮防双开 + save 线程锁，183 测试全绿 | ✅ |
| M6 | 以创作者为中心（策划 + 候选图 + 默认人审）| 策划页（Brief/参考图/BGM）+ 每镜候选画廊（改选/重 roll/精修）+ 分镜编辑器增强 + 编辑 API + 人审优先叙事，253 测试全绿 + wanx2.1-imageedit live 验证 | ✅ |
| M7 | 动态化（运镜关键帧 + 首尾帧）| animate 节点（六节点流水线）+ 四种运镜全关键帧化（pan 降级解除）+ 候选图选结束帧 → 0.4s 静态尾拍 + 旧项目兼容 shim，276 测试全绿 | ✅ |
| M8 | 音频（sfx 素材引用 + BGM 卡点对齐）| 音效库上传 + 切点绑定 + sfx 独立轨导出 + miniaudio 节拍检测向前 snap 卡点 + BGM 来源解耦（上传优先/模板兜底），303 测试全绿 | ✅ |
| M9 | 止损转场（0.3s 闪白/震动覆盖不可修帧）| Scene.transition auto 止损（无结束帧的切点自动闪白，创作者逐镜覆盖）+ VideoClip.transition 组装解析 + 剪映 TransitionType 导出（300ms）+ 分镜页转场 selectbox + shim 0.5，317 测试全绿 | ✅ |
| M10 | 本地字幕（用户自带音频 → Whisper 转写）| 场景自带音频上传/移除（替代 TTS 配音）+ transcribe 节点（faster-whisper 本地推理 + 三键 sidecar 缓存）+ 时间线录音时长/转写守卫 + 导出走既有配音/字幕通道 + 策划页模型配置 + shim 0.6，351 测试全绿 + 真实模型端到端转写验证 | ✅ |

完整方案见 [EXECUTION_PLAN.md](EXECUTION_PLAN.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)、[SUMMARY.md](SUMMARY.md)、[docs/PROGRESS.md](docs/PROGRESS.md)。

## 开发

```bash
pip install -e .[dev]
pytest                            # 测试临时文件走 F:/tmp/avpo-pytest（见 conftest.py）
pytest -m live                    # 真实链路（调用外部 API，按 .env 渠道）
```

## 已知限制

- 运镜：关键帧动画已覆盖 zoom/pan 四类（M7），但分镜 LLM 的运镜/景别仍是提案，
  最终以剪映内关键帧为准；
- 转场：闪白/震动固定 0.3s，不支持时长/曲线调节（可在剪映内改）；叠加型（震动）
  在相邻 clip 上的叠化窗口由剪映编辑器处理，导出后打开草稿试看确认；
- BGM 卡点：节拍检测基于能量包络（鼓点/重拍），轻音乐或无鼓点曲目检出节拍少；
  开关开启后建议试听核对（切点可回剪映手动微调）；BGM 素材自备，AVPO 不提供；
- 音效：素材自备（`assets/sfx/` 本地入库，0 API 成本）；音效放在场景切点，
  不提供多时间点/音量包络微调（可在剪映内调）；
- 本地字幕：首次转写需联网下载 whisper 模型（small ≈ 460MB，缓存于数据目录
  `whisper_models/`，国内网络可设 `HF_ENDPOINT=https://hf-mirror.com`）；字幕按
  whisper 识别段一行展示（不做词级切分），识别错误需人工核对（创作者在环）；
  模型越大越准越慢（tiny~large-v3 可换）；转写文本已做 OpenCC 简繁归一
  （t2s，繁体项目可关 `SIMPLIFY_CHINESE`）；
- 渠道 key 只放 `.env`，不入库（公开仓库规范）；
- 工作台单会话单任务（运行中不能开第二个任务/多项目并行）；`ProjectStore.save` 锁为进程内
  锁，CLI 与 Web 同时跑同一数据目录时 git 提交不互斥；运行中关浏览器任务继续跑完落盘，
  但进度条随会话丢失（重开看徽章终态）。
