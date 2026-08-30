"""素材链路编排 —— IMPLEMENTATION_PLAN 2.8 / 3.1 / 3.2 的实现载体。

run_direct：     direct 节点 —— 文案 → 分镜（Director 强制 JSON），校验后写 project.scenes。
run_gen_assets： gen_assets 节点 —— 每个场景：配音段（TTS + word 时间戳）→ 字幕聚合
                → 生图（带 prompt_hash 缓存），全部产物入 assets/，场景标记 done。
                M10：自带音频（user_audio_asset_id）场景跳过 TTS 配音与字幕（该场景
                字幕由 transcribe 节点产出并保留）。
run_transcribe： transcribe 节点（M10）—— 自带音频场景本地转写（faster-whisper），
                字幕落盘 + sidecar 缓存（音频哈希/模型/语言三键校验）。
run_timeline：   timeline 节点 —— 逐场景素材组装全局时间轴（M2-3.1）。
run_export：     export 节点 —— project → 剪映草稿目录（M2-3.2），写 project.export。

依赖语义：任一节点成功后，下游节点状态重置为 pending —— 上游产物变了，下游必须
重跑（断点续跑不会拿着过期产物往下走）。

产物命名约定（M2 时间线组装沿用）：
- 配音段资产 id = vo_<scene_id>，路径 assets/vo_<scene_id>.mp3；
- 图资产 id = img_<scene_id>_v1..vN（M6-7.5 候选图），路径同 id；
  M6 前的旧式单图 img_<scene_id> 在重跑时清理（升级后首次重跑全量重生成）；
- 字幕 scene_id 绑定到对应场景，时间戳与配音同源。

两个函数都走 app/core/state.py 的 run_task：done 跳过、失败重试 ×3、状态与产物同一次提交。
fn 可重入：重跑覆盖同一资产 id/路径，字幕整体重建（不叠加）。

M2-3.5 并发：配音+字幕阶段顺序执行（edge-tts 连续突发请求触发 NoAudioReceived，
场景间留 0.3s 间隔），生图阶段 4 张并行（慢操作，实测 120s → ~50s）。
"""

import asyncio
import itertools
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.audio import whisper
from app.core.motion import resolve_motion_plan
from app.core.progress import ProgressCallback, ProgressEvent
from app.core.project import ProjectStore
from app.core.schema import PIPELINE_NODES, Asset, Project, Scene, Subtitle
from app.core.state import FatalError, TransientError, run_task
from app.core.styles import StyleTemplate, compose_image_prompt, load_style
from app.director.director import Director
from app.export.jianying import export as export_draft
from app.timeline.builder import build_timeline
from app.tts.base import TTSProvider
from app.tts.cache import load_words, save_words
from app.tts.subs import build_subtitles, reattach_punctuation
from app.vision.base import ImageProvider
from app.vision.cache import ImageCache, prompt_hash

# ---- M2-3.5 并发参数 ----
IMAGE_CONCURRENCY = 4       # 并发生图上限（3~4 张并行）
_TTS_GAP_S = 0.3            # 场景间配音间隔：edge-tts 突发请求会触发 NoAudioReceived


def run_direct(
    store: ProjectStore,
    project: Project,
    director: Director,
    text: str,
    progress: ProgressCallback | None = None,
) -> bool:
    """direct 节点：文案 → 分镜落盘（M3-4.4：按项目风格模板注入运镜指导）。

    progress（M5）：结构化进度事件回调，供 UI 展示（CLI 不传，默认 None 向后兼容）。
    """
    def fn(p: Project) -> None:
        if progress:
            progress(ProgressEvent("direct", "调用 LLM 生成分镜…", 0.0))
        # M6-7.3：风格模板运镜/景别指导 + 创作简报（Brief）注入分镜提示词
        style = load_style(p.config.style)
        scenes, cost = director.storyboard(
            text,
            motion_hint=style.motion_hint,
            brief=p.brief,
            shot_size_hint=style.shot_size_hint,
        )
        share = round(cost / len(scenes), 6)
        for scene in scenes:
            scene.cost["llm"] = share          # 一次调用成本均摊到各场景
        p.scenes = scenes
        if progress:
            progress(ProgressEvent("direct", f"分镜生成完成（{len(scenes)} 场景）", 1.0))
        _invalidate_downstream(p, "direct")

    return run_task(store, project, "direct", fn)


def run_gen_assets(
    store: ProjectStore,
    project: Project,
    tts: TTSProvider,
    image: ImageProvider,
    progress: ProgressCallback | None = None,
) -> bool:
    """gen_assets 节点：逐场景生成配音段 + 字幕 + 图，归档入 assets/。

    progress（M5）：结构化进度事件回调。percent 分段：配音+字幕占节点 45%
    （顺序执行，n 场景均分），生图占 55%（并发，按完成候选张数均分，M6-7.5 候选级
    粒度 = n 场景 × candidates 张），末事件恰好 1.0。
    生图段用 submit + as_completed，取结果后回调 —— 回调在 run_task 的调用线程发出
    （M5 后即 worker 线程，回调只写线程安全容器，绝不调 st.*）。
    """
    def fn(p: Project) -> None:
        cache = ImageCache(store.project_dir(p.project_id))
        image.cache = cache                     # 管线持有缓存生命周期（每个项目一份）
        # M10：自带音频场景的字幕由 transcribe 产出，重建时保留；其余整体重建
        user_audio_ids = {s.scene_id for s in p.scenes if s.user_audio_asset_id}
        p.subtitles = [s for s in p.subtitles if s.scene_id in user_audio_ids]
        # 阶段 1：配音 + 字幕（顺序执行，场景间留间隔防 edge-tts 瞬态限流；
        # M10 自带音频场景跳过 —— 配音/字幕来自用户录音与 transcribe）
        voiced = [s for s in p.scenes if s.scene_id not in user_audio_ids]
        for i, scene in enumerate(voiced):
            if i:
                time.sleep(_TTS_GAP_S)
            _gen_scene_voice(store, p, scene, tts)
            if progress:
                progress(ProgressEvent(
                    "gen_assets", f"配音+字幕 {i + 1}/{len(voiced)}（{scene.scene_id}）",
                    0.45 * (i + 1) / len(voiced),
                ))
        # 阶段 2：并发生成候选图（M6-7.5：每场景 N 张；慢操作按场景并行，缓存命中 0 API 调用）
        if p.scenes:
            total = len(p.scenes) * p.config.image.candidates
            done = itertools.count(1)           # 候选级进度计数（GIL 下 next() 线程安全）
            style = load_style(p.config.style)
            with ThreadPoolExecutor(max_workers=min(IMAGE_CONCURRENCY, len(p.scenes))) as pool:
                futures = [
                    pool.submit(_gen_scene_candidates, store, p, s, image, style, progress, done, total)
                    for s in p.scenes
                ]
                for fut in as_completed(futures):
                    fut.result()                # 调用线程取结果：异常冒泡给 run_task 重试
        _invalidate_downstream(p, "gen_assets")

    return run_task(store, project, "gen_assets", fn)


def _gen_scene_voice(
    store: ProjectStore, project: Project, scene: Scene, tts: TTSProvider
) -> None:
    """阶段 1：场景配音段 + 词级时间戳 → 字幕（与配音零成本对齐）。

    M3-4.1 断点续跑：mp3 + sidecar（narration_hash 校验）命中则跳过合成，
    从缓存恢复词级时间戳重建字幕 —— kill -9 后重跑 0 次重复 API 调用；
    文案改了缓存自动失效，重新合成。
    """
    config = project.config
    assets_dir = store.project_dir(project.project_id) / "assets"

    if not scene.narration.strip():
        raise FatalError(
            f"scene {scene.scene_id} 的 narration 为空",
            hint="到分镜确认页补齐该场景文案，或删除该场景（M6 起文案可在分镜页编辑）",
        )

    vo_id = f"vo_{scene.scene_id}"
    mp3_path = assets_dir / f"vo_{scene.scene_id}.mp3"

    words = load_words(assets_dir, scene.scene_id, scene.narration) if mp3_path.is_file() else None
    if words is None:
        result = asyncio.run(tts.synth(scene.narration, mp3_path))
        words = result.words
        save_words(assets_dir, scene.scene_id, scene.narration, words)

    project.assets[vo_id] = Asset(
        type="audio",
        path=f"assets/vo_{scene.scene_id}.mp3",
        model=config.tts.engine,
        cost=0.0,
        status="done",
    )

    # edge-tts 词事件不含标点 → 先把文案标点回贴到词上，断句规则才可用
    project.subtitles.extend(build_subtitles(reattach_punctuation(words, scene.narration), scene.scene_id))


def _gen_scene_candidates(
    store: ProjectStore,
    project: Project,
    scene: Scene,
    image: ImageProvider,
    style: StyleTemplate,
    progress: ProgressCallback | None,
    done: itertools.count,
    total: int,
) -> None:
    """阶段 2：场景候选图生成（M6-7.5，每场景 N 张候选，多场景并发各写各的资产）。

    候选 id = img_<scene_id>_v1..vN，默认选中 v1（人审可改选，7.6 select_image_candidate）。
    断点续跑：重跑复用已有候选的 seed（seed 进缓存键，同种子 0 次重复 API 调用）；
    candidates 收敛后残留的旧 vN+ 资产/文件删除（孤儿清理），M6 前的旧式单图一并清理
    （升级后首次重跑全量重生成，一次性成本见 README 迁移说明）。
    提示词在生成时唯一拼装点 compose_image_prompt（确定性 = prompt_hash 缓存稳定）。
    scene.cost["image"] = 各候选成本累计（3 候选 ≈ 成本×3）。
    """
    config = project.config
    assets_dir = store.project_dir(project.project_id) / "assets"
    n = config.image.candidates
    base = f"img_{scene.scene_id}"

    # 已有候选的 seed 复用（断点续跑 0 重复调用；无则新抽，候选互不串缓存键）
    existing: dict[int, int] = {}
    for cid in list(scene.image_candidates):
        asset = project.assets.get(cid)
        if asset and asset.seed is not None and cid.startswith(f"{base}_v"):
            try:
                existing[int(cid.rsplit("_v", 1)[1])] = asset.seed
            except ValueError:
                pass

    # 孤儿清理：candidates 收敛后的旧 vN+ 与 M6 前旧式单图 img_<scene_id>
    keep = {f"{base}_v{i}" for i in range(1, n + 1)}
    for cid in list(project.assets):
        if cid == base or (cid.startswith(f"{base}_v") and cid not in keep):
            project.assets.pop(cid, None)
            (assets_dir / f"{cid}.png").unlink(missing_ok=True)

    prompt = compose_image_prompt(style, project.brief, scene)
    candidates: list[str] = []
    cost = 0.0
    for i in range(1, n + 1):
        cid = f"{base}_v{i}"
        gen = image.generate(
            prompt,
            assets_dir / f"{cid}.png",
            model=config.image.model,
            size=config.image.size,
            seed=existing[i] if i in existing else random.randint(0, 10**9),
        )
        project.assets[cid] = Asset(
            type="image",
            path=f"assets/{cid}.png",
            model=config.image.model,
            prompt_hash=prompt_hash(prompt, config.image.model, config.image.size, seed=gen.seed),
            seed=gen.seed,
            cost=gen.cost,
            status="done",
        )
        candidates.append(cid)
        cost += gen.cost
        if progress:
            k = next(done)
            progress(ProgressEvent("gen_assets", f"生图 {k}/{total}", 0.45 + 0.55 * k / total))

    scene.image_candidates = candidates
    scene.image_asset_id = scene.image_asset_id if scene.image_asset_id in keep else f"{base}_v1"
    scene.cost["image"] = cost
    scene.status = "done"


def run_transcribe(
    store: ProjectStore, project: Project, progress: ProgressCallback | None = None,
) -> bool:
    """transcribe 节点（M10）：用户自带音频 → 本地转写（faster-whisper）→ 字幕落盘。

    逐场景：有 user_audio_asset_id 才转写（模型懒加载单例，进程内共享）；sidecar
    缓存（音频 sha256 + 模型 + 语言三键）命中即跳过推理 —— 断点续跑语义与 TTS
    一致；该场景字幕整体替换（重跑幂等）。未检出语音 = FatalError（大概率放错
    文件，不静默产出空字幕）。无自带音频场景 → 节点空转 done（下游失效）。
    """
    def fn(p: Project) -> None:
        target = [s for s in p.scenes if s.user_audio_asset_id]
        assets_dir = store.project_dir(p.project_id) / "assets"
        download_root = whisper.model_cache_dir(store.data_dir)
        for i, scene in enumerate(target):
            if progress:
                progress(ProgressEvent(
                    "transcribe", f"本地转写 {i + 1}/{len(target)}（{scene.scene_id}）",
                    i / len(target),
                ))
            asset = p.assets.get(scene.user_audio_asset_id or "")
            if asset is None or asset.type != "audio":
                raise FatalError(
                    f"scene {scene.scene_id} 自带音频资产缺失",
                    hint="重新上传音频",
                )
            audio_path = store.project_dir(p.project_id) / asset.path
            if not audio_path.is_file():
                raise FatalError(f"自带音频文件缺失: {audio_path}", hint="重新上传音频")
            model = p.config.whisper_model
            language = p.config.whisper_language
            audio_hash = whisper.audio_sha256(audio_path)
            segments = whisper.load_whisper_cache(
                assets_dir, scene.scene_id, audio_hash, model, language,
            )
            if segments is None:
                try:
                    segments = whisper.transcribe_audio(audio_path, model, language, download_root)
                except whisper.WhisperModelError as exc:
                    raise TransientError(
                        f"{exc}（重试中）",
                        hint="检查网络（首次转写需下载模型），可设 HF_ENDPOINT=https://hf-mirror.com 走镜像",
                    ) from exc
                except whisper.WhisperDecodeError as exc:
                    raise FatalError(str(exc), hint="更换有效音频文件（mp3/wav/m4a）后重新上传") from exc
                if not segments:
                    raise FatalError(
                        f"scene {scene.scene_id} 未检出语音（音频可能为静音或非人声）",
                        hint="重新录制/更换音频后重新上传",
                    )
                whisper.save_whisper_cache(assets_dir, scene.scene_id, audio_hash, model, language, segments)
            # 该场景字幕整体替换（重跑幂等；无旧字幕时追加尾部，导出按时间戳定位不依赖顺序）
            p.subtitles = [s for s in p.subtitles if s.scene_id != scene.scene_id]
            p.subtitles.extend(
                Subtitle(scene_id=scene.scene_id, start_ms=seg.start_ms, end_ms=seg.end_ms, text=seg.text)
                for seg in segments
            )
        if progress:
            progress(ProgressEvent(
                "transcribe",
                f"转写完成（{len(target)} 场景）" if target else "无自带音频场景，跳过",
                1.0,
            ))
        _invalidate_downstream(p, "transcribe")

    return run_task(store, project, "transcribe", fn)


def run_animate(
    store: ProjectStore, project: Project, progress: ProgressCallback | None = None,
) -> bool:
    """animate 节点（M7-8.4）：scene.motion + 首尾帧 → 关键帧运镜计划落盘。

    逐场景调用 resolve_motion_plan（app/core/motion.py 唯一解析点）写入
    scene.motion_plan；参数只在这里落盘（可审计可调），timeline 按
    plan.end_frame_ms 追加首尾帧尾拍，export 按 plan 渲染运镜关键帧。
    纯本地解析（无 API 调用），产物是剪映草稿运镜动画的唯一消费输入。
    """
    def fn(p: Project) -> None:
        # 前置依赖：素材链路必须先完成（运镜计划是素材之后的动态化阶段）
        if p.pipeline["gen_assets"] != "done":
            raise FatalError(
                f"gen_assets 未完成（{p.pipeline['gen_assets']}），无法解析运镜计划",
                hint="先跑 avpo gen-assets",
            )
        for i, scene in enumerate(p.scenes):
            scene.motion_plan = resolve_motion_plan(scene)
            if progress:
                progress(ProgressEvent(
                    "animate", f"解析运镜 {i + 1}/{len(p.scenes)}（{scene.scene_id}）",
                    (i + 1) / len(p.scenes),
                ))
        _invalidate_downstream(p, "animate")

    return run_task(store, project, "animate", fn)


def run_timeline(store: ProjectStore, project: Project, progress: ProgressCallback | None = None) -> bool:
    """timeline 节点：逐场景素材 → 全局时间轴（scene.start_ms + 双轨 + 总时长）。"""
    def fn(p: Project) -> None:
        # 前置依赖：素材链路必须已完成（图/配音资产在 gen_assets 里产出）
        if p.pipeline["gen_assets"] != "done":
            raise FatalError(
                f"gen_assets 未完成（{p.pipeline['gen_assets']}），无法组装时间线",
                hint="先跑 avpo gen-assets",
            )
        if progress:
            progress(ProgressEvent("timeline", "组装时间线…", 0.0))
        build_timeline(p, store.project_dir(p.project_id))
        if progress:
            progress(ProgressEvent("timeline", f"时间线组装完成（{len(p.timeline.video)} 段视频轨）", 1.0))
        _invalidate_downstream(p, "timeline")

    return run_task(store, project, "timeline", fn)


def run_confirm(store: ProjectStore, project: Project, progress: ProgressCallback | None = None) -> bool:
    """confirm 节点：分镜人工确认。fn 本身空转 —— CLI 已展示分镜并读到用户 y 才调用。"""
    def fn(p: Project) -> None:
        if progress:
            progress(ProgressEvent("confirm", "分镜确认已记录", 1.0))

    return run_task(store, project, "confirm", fn)


def run_export(
    store: ProjectStore, project: Project, *, zip_archive: bool = True,
    progress: ProgressCallback | None = None,
) -> bool:
    """export 节点：project → 剪映草稿目录（含 zip），写 project.export 状态。"""
    def fn(p: Project) -> None:
        if p.pipeline["timeline"] != "done":
            raise FatalError(
                f"timeline 未完成（{p.pipeline['timeline']}），无法导出",
                hint="先跑 avpo timeline",
            )
        if progress:
            progress(ProgressEvent("export", "导出剪映草稿…", 0.0))
        project_dir = store.project_dir(p.project_id)
        draft_dir = export_draft(p, project_dir, project_dir / "exports", zip_archive=zip_archive)
        p.export.path = str(draft_dir.relative_to(project_dir)).replace("\\", "/")
        p.export.status = "done"
        if progress:
            progress(ProgressEvent("export", "导出完成", 1.0))

    return run_task(store, project, "export", fn)


def _invalidate_downstream(project: Project, node: str) -> None:
    """节点成功后把下游状态重置为 pending —— 上游产物变了，下游必须重跑。"""
    for downstream in PIPELINE_NODES[PIPELINE_NODES.index(node) + 1:]:
        project.pipeline[downstream] = "pending"
