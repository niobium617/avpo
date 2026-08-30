# 钉版记录

> 剪映格式漂移是最大风险（EXECUTION_PLAN §9）。所有验证基于以下钉住的版本，升级需重新验证。

| 组件 | 版本 | 确认日期 | 备注 |
|---|---|---|---|
| 剪映专业版 | 9.7.1 | 2026-08-19 | 用户「关于」页确认；草稿目录 `com.lveditor.draft` |
| pyJianYingDraft | 0.3.0（PyPI 最新） | 2026-08-19 | 生成草稿 `version=360000` / `new_version=110.0.0`（剪映 11.0.0 格式） |
| edge-tts | 7.2.8 | 2026-08-20 | **M1-2.1 定版**：20/20 一次通过（tests/edge_tts_log.md）。条件：必须传 `boundary="WordBoundary"`（默认 SentenceBoundary 无词级时间戳）；调用方须 ×3 重试（run_task 已满足）。词事件文本不含标点，由 `subs.reattach_punctuation` 回贴 |
| DashScope（千问渠道） | qwen-plus / wanx2.1-t2i-turbo | 2026-08-20 | 兼容模式 chat + 原生 image-synthesis（异步轮询），真实全链路通过（pytest -m live 68.9s）。估账：生图 ¥0.16/张 |
| DashScope 图生图 | wanx2.1-imageedit | 2026-08-25 | **M6-7.4 live 验证**：image2image/image-synthesis（base64 data URI，function=stylization_all），512x512 白图参考真实调用通过（`tests/test_qwen.py::test_live_imageedit`，8.1s）。据此置 `QwenImage.supports_reference_image=True, max_reference_images=1` |
| AVPO schema | 0.2 | 2026-08-25 | 0.1 → 0.2 纯增量（M6-7.1）：`Brief`（12 字段）、`ReferenceImage` 图组、`Scene` 增 shot_size/planned_duration_ms/sfx/image_candidates/end_image_asset_id、`Asset.reference_asset_id`、`ImageConfig.candidates(1~6)`、`Project.brief/reference_images/reference_seq`。无 key 迁移：旧 JSON 直接加载，load 时内存升版，下次 save 持久化 |
| AVPO schema | 0.3 | 2026-08-27 | 0.1/0.2 → 0.3 纯增量（M7-8.1）：`MotionPlan`（scale/pan 起止值 + end_frame_ms）、`Scene.motion_plan`、`VideoClip.scene_id`、`PIPELINE_NODES` 增 `animate`（direct→confirm→gen_assets→**animate**→timeline→export）。load shim：旧 5 节点 pipeline 按序补 `animate: pending` + 内存升版 0.3（文件不迁移，下次 save 持久化）；不跑 animate 的旧项目导出按 `scene.motion` 兜底解析，老草稿运镜不丢。运镜关键帧（uniform_scale/position_x）经 pyJianYingDraft 0.3.0 KeyframeProperty 写入（导出为 KFTypeScaleX/KFTypePositionX），同 M0 钉版无需升级 |
| AVPO schema | 0.4 | 2026-08-28 | 0.1/0.2/0.3 → 0.4 纯增量（M8）：`Scene.sfx_asset_id`（切点音效引用）、`Timeline.sfx`（音效轨）、`ProjectConfig.beat_sync`（BGM 卡点开关）。全部默认值字段，无 key 迁移：旧 JSON 直接加载，load 时内存升版（文件不迁移，下次 save 持久化）。音效轨导出为独立 audio 轨（切点定位 + 素材自身时长 + 无淡入淡出），同 pyJianYingDraft 0.3.0 钉版 |
| miniaudio | 1.71 | 2026-08-28 | **M8 钉版**：BGM 节拍检测解码器（内置 dr_mp3/dr_wav，无 ffmpeg 依赖）。python 3.13 Windows wheel 实测解码 mp3/wav 正常 |
| faster-whisper | 1.2.1 | 2026-08-30 | **M10 钉版**（ctranslate2 4.8.1 + av 18.1.0，py3.13 wheel 实测）。本地转写：`WhisperModel(device="cpu", compute_type="int8")`；模型经 `download_model(cache_dir=<数据目录>/whisper_models)` 下载（HF_HOME 重定向禁写 C 盘，`HF_ENDPOINT` 可配镜像）。模型尺寸 tiny/base/small/medium/large-v3 可换（默认 small）。**实测**：small 中文输出简繁混用（「变/内/创」→「變/內/創」），需 OpenCC t2s 归一 |
| opencc-python-reimplemented | 0.1.7 | 2026-08-30 | **M10 钉版**：whisper 转写文本 t2s 简繁归一（纯 Python 实现，无 C 扩展）。`SIMPLIFY_CHINESE` 开关在 app/audio/whisper.py（繁体项目置 False）；对非中文文本空操作 |
| AVPO schema | 0.5 | 2026-08-29 | 0.1~0.4 → 0.5 纯增量（M9 止损转场）：`TransitionKind`（auto/none/flash_white/shake）、`Scene.transition`（默认 auto = 无结束帧的切点自动闪白）、`VideoClip.transition`（默认 none，timeline 组装解析写入）。全部默认值字段，无 key 迁移：旧 JSON 直接加载，load 时内存升版（文件不迁移，下次 save 持久化）。导出映射 `TransitionType.闪白`（非叠加）/`震动`（叠加）固定 0.3s，同 pyJianYingDraft 0.3.0 钉版；**剪映 9.7.1 打开验证通过**（opened_log 2026-08-29，震动叠加型叠化正常） |
| AVPO schema | 0.6 | 2026-08-30 | 0.1~0.5 → 0.6 纯增量（M10 本地字幕）：`WhisperModelKind`（tiny/base/small/medium/large-v3）、`Scene.user_audio_asset_id`（自带音频引用，替代 TTS 配音）、`ProjectConfig.whisper_model/whisper_language`（默认 small/zh）、`PIPELINE_NODES` 增 `transcribe`（direct→confirm→gen_assets→**transcribe**→animate→timeline→export）。全部默认值字段，无 key 迁移；load shim 补 `transcribe: pending` 键 + 内存升版 0.6（文件不迁移，下次 save 持久化）。时间线用 mutagen 泛化读时长（mp3/wav/m4a），导出走既有配音/字幕通道，无需 pyJianYingDraft 升级 |

✅ **兼容性已实测（2026-08-19，M0-1.7）**：剪映 9.7.1 成功打开 pyJianYingDraft 0.3.0 生成的 11.0.0 格式草稿（见 tests/opened_log.md）。剪映升级后需重新验证。
