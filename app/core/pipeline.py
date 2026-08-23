"""素材链路编排 —— IMPLEMENTATION_PLAN 2.8 / 3.1 / 3.2 的实现载体。

run_direct：     direct 节点 —— 文案 → 分镜（Director 强制 JSON），校验后写 project.scenes。
run_gen_assets： gen_assets 节点 —— 每个场景：配音段（TTS + word 时间戳）→ 字幕聚合
                → 生图（带 prompt_hash 缓存），全部产物入 assets/，场景标记 done。
run_timeline：   timeline 节点 —— 逐场景素材组装全局时间轴（M2-3.1）。
run_export：     export 节点 —— project → 剪映草稿目录（M2-3.2），写 project.export。

依赖语义：任一节点成功后，下游节点状态重置为 pending —— 上游产物变了，下游必须
重跑（断点续跑不会拿着过期产物往下走）。

产物命名约定（M2 时间线组装沿用）：
- 配音段资产 id = vo_<scene_id>，路径 assets/vo_<scene_id>.mp3；
- 图资产 id = img_<scene_id>，路径 assets/img_<scene_id>.png；
- 字幕 scene_id 绑定到对应场景，时间戳与配音同源。

两个函数都走 app/core/state.py 的 run_task：done 跳过、失败重试 ×3、状态与产物同一次提交。
fn 可重入：重跑覆盖同一资产 id/路径，字幕整体重建（不叠加）。

M2-3.5 并发：配音+字幕阶段顺序执行（edge-tts 连续突发请求触发 NoAudioReceived，
场景间留 0.3s 间隔），生图阶段 4 张并行（慢操作，实测 120s → ~50s）。
"""

import asyncio
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core.project import ProjectStore
from app.core.schema import PIPELINE_NODES, Asset, Project, Scene
from app.core.state import FatalError, run_task
from app.core.styles import load_style
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
    progress: Callable[[str], None] | None = None,
) -> bool:
    """direct 节点：文案 → 分镜落盘（M3-4.4：按项目风格模板注入运镜指导）。

    progress（M4）：阶段文字回调，供 UI 展示（CLI 不传，默认 None 向后兼容）。
    """
    def fn(p: Project) -> None:
        if progress:
            progress("调用 LLM 生成分镜…")
        # 风格模板的运镜指导注入分镜提示词（fast_talk/emotional/explainer）
        scenes, cost = director.storyboard(text, motion_hint=load_style(p.config.style).motion_hint)
        share = round(cost / len(scenes), 6)
        for scene in scenes:
            scene.cost["llm"] = share          # 一次调用成本均摊到各场景
        p.scenes = scenes
        if progress:
            progress(f"分镜生成完成（{len(scenes)} 场景）")
        _invalidate_downstream(p, "direct")

    return run_task(store, project, "direct", fn)


def update_scenes(store: ProjectStore, project: Project, scenes: list[Scene]) -> Project:
    """分镜人工修改落盘：只允许改 visual / image_prompt / motion。

    narration 是「拼接=原文」的逐字校验不变量，改动即拒绝（改文案请重新跑 direct）。
    落盘后 direct 下游全部重置 pending —— 生图提示词/运镜变了，素材/时间线/导出
    必须重跑；配音文案未变时 TTS sidecar 缓存命中，重跑成本可忽略。
    """
    if [s.narration for s in scenes] != [s.narration for s in project.scenes]:
        raise ValueError("narration 不可修改（分镜文案逐字不变量）；改文案请重新运行 direct")
    project.scenes = scenes
    _invalidate_downstream(project, "direct")
    store.save(project, message=f"{project.project_id}: 分镜人工修改")
    return project


def run_gen_assets(
    store: ProjectStore,
    project: Project,
    tts: TTSProvider,
    image: ImageProvider,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """gen_assets 节点：逐场景生成配音段 + 字幕 + 图，归档入 assets/。

    progress（M4）：阶段文字回调，供 UI 展示。生图段用 submit + as_completed，
    主线程逐个取结果后回调 —— 进度回调绝不从工作线程发出（Streamlit 线程限制）。
    """
    def fn(p: Project) -> None:
        cache = ImageCache(store.project_dir(p.project_id))
        image.cache = cache                     # 管线持有缓存生命周期（每个项目一份）
        p.subtitles = []                        # 整体重建 → fn 可重入，重试不叠加
        # 阶段 1：配音 + 字幕（顺序执行，场景间留间隔防 edge-tts 瞬态限流）
        for i, scene in enumerate(p.scenes):
            if i:
                time.sleep(_TTS_GAP_S)
            _gen_scene_voice(store, p, scene, tts)
            if progress:
                progress(f"配音+字幕 {i + 1}/{len(p.scenes)}（{scene.scene_id}）")
        # 阶段 2：并发生图（慢操作，4 张并行；缓存命中 0 API 调用）
        if p.scenes:
            with ThreadPoolExecutor(max_workers=min(IMAGE_CONCURRENCY, len(p.scenes))) as pool:
                futures = [pool.submit(_gen_scene_image, store, p, s, image) for s in p.scenes]
                for done_n, fut in enumerate(as_completed(futures), 1):
                    fut.result()                # 主线程取结果：异常冒泡给 run_task 重试
                    if progress:
                        progress(f"生图 {done_n}/{len(p.scenes)}")
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
        raise FatalError(f"scene {scene.scene_id} 的 narration 为空", hint="重跑 direct 生成分镜")

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


def _gen_scene_image(
    store: ProjectStore, project: Project, scene: Scene, image: ImageProvider
) -> None:
    """阶段 2：场景生图（缓存命中 0 API 调用）。多个场景并发执行，各写各的资产。"""
    config = project.config
    assets_dir = store.project_dir(project.project_id) / "assets"

    gen = image.generate(
        scene.image_prompt,
        assets_dir / f"img_{scene.scene_id}.png",
        model=config.image.model,
        size=config.image.size,
    )
    img_id = f"img_{scene.scene_id}"
    project.assets[img_id] = Asset(
        type="image",
        path=f"assets/img_{scene.scene_id}.png",
        model=config.image.model,
        prompt_hash=prompt_hash(scene.image_prompt, config.image.model, config.image.size),
        seed=gen.seed,
        cost=gen.cost,
        status="done",
    )
    scene.image_asset_id = img_id
    scene.cost["image"] = gen.cost
    scene.status = "done"


def run_timeline(store: ProjectStore, project: Project) -> bool:
    """timeline 节点：逐场景素材 → 全局时间轴（scene.start_ms + 双轨 + 总时长）。"""
    def fn(p: Project) -> None:
        # 前置依赖：素材链路必须已完成（图/配音资产在 gen_assets 里产出）
        if p.pipeline["gen_assets"] != "done":
            raise FatalError(
                f"gen_assets 未完成（{p.pipeline['gen_assets']}），无法组装时间线",
                hint="先跑 avpo gen-assets",
            )
        build_timeline(p, store.project_dir(p.project_id))
        _invalidate_downstream(p, "timeline")

    return run_task(store, project, "timeline", fn)


def run_confirm(store: ProjectStore, project: Project) -> bool:
    """confirm 节点：分镜人工确认。fn 本身空转 —— CLI 已展示分镜并读到用户 y 才调用。"""
    def fn(p: Project) -> None:
        pass

    return run_task(store, project, "confirm", fn)


def run_export(store: ProjectStore, project: Project, *, zip_archive: bool = True) -> bool:
    """export 节点：project → 剪映草稿目录（含 zip），写 project.export 状态。"""
    def fn(p: Project) -> None:
        if p.pipeline["timeline"] != "done":
            raise FatalError(
                f"timeline 未完成（{p.pipeline['timeline']}），无法导出",
                hint="先跑 avpo timeline",
            )
        project_dir = store.project_dir(p.project_id)
        draft_dir = export_draft(p, project_dir, project_dir / "exports", zip_archive=zip_archive)
        p.export.path = str(draft_dir.relative_to(project_dir)).replace("\\", "/")
        p.export.status = "done"

    return run_task(store, project, "export", fn)


def _invalidate_downstream(project: Project, node: str) -> None:
    """节点成功后把下游状态重置为 pending —— 上游产物变了，下游必须重跑。"""
    for downstream in PIPELINE_NODES[PIPELINE_NODES.index(node) + 1:]:
        project.pipeline[downstream] = "pending"
