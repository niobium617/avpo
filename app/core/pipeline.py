"""M1 素材链路编排 —— IMPLEMENTATION_PLAN 2.8 退出条件的实现载体。

run_direct：     direct 节点 —— 文案 → 分镜（Director 强制 JSON），校验后写 project.scenes。
run_gen_assets： gen_assets 节点 —— 每个场景：配音段（TTS + word 时间戳）→ 字幕聚合
                → 生图（带 prompt_hash 缓存），全部产物入 assets/，场景标记 done。

产物命名约定（M2 时间线组装沿用）：
- 配音段资产 id = vo_<scene_id>，路径 assets/vo_<scene_id>.mp3；
- 图资产 id = img_<scene_id>，路径 assets/img_<scene_id>.png；
- 字幕 scene_id 绑定到对应场景，时间戳与配音同源。

两个函数都走 app/core/state.py 的 run_task：done 跳过、失败重试 ×3、状态与产物同一次提交。
fn 可重入：重跑覆盖同一资产 id/路径，字幕整体重建（不叠加）。
"""

import asyncio

from app.core.project import ProjectStore
from app.core.schema import Asset, Project, Scene
from app.core.state import FatalError, run_task
from app.director.director import Director
from app.tts.base import TTSProvider
from app.tts.subs import build_subtitles, reattach_punctuation
from app.vision.base import ImageProvider
from app.vision.cache import ImageCache, prompt_hash


def run_direct(store: ProjectStore, project: Project, director: Director, text: str) -> bool:
    """direct 节点：文案 → 分镜落盘。"""
    def fn(p: Project) -> None:
        scenes, cost = director.storyboard(text)
        share = round(cost / len(scenes), 6)
        for scene in scenes:
            scene.cost["llm"] = share          # 一次调用成本均摊到各场景
        p.scenes = scenes

    return run_task(store, project, "direct", fn)


def run_gen_assets(store: ProjectStore, project: Project, tts: TTSProvider, image: ImageProvider) -> bool:
    """gen_assets 节点：逐场景生成配音段 + 字幕 + 图，归档入 assets/。"""
    def fn(p: Project) -> None:
        cache = ImageCache(store.project_dir(p.project_id))
        image.cache = cache                     # 管线持有缓存生命周期（每个项目一份）
        p.subtitles = []                        # 整体重建 → fn 可重入，重试不叠加
        for scene in p.scenes:
            _gen_scene_assets(store, p, scene, tts, image)

    return run_task(store, project, "gen_assets", fn)


def _gen_scene_assets(
    store: ProjectStore, project: Project, scene: Scene, tts: TTSProvider, image: ImageProvider
) -> None:
    config = project.config
    assets_dir = store.project_dir(project.project_id) / "assets"

    # 1) 配音段 + 词级时间戳（edge-tts 免费，成本 0）
    if not scene.narration.strip():
        raise FatalError(f"scene {scene.scene_id} 的 narration 为空", hint="重跑 direct 生成分镜")
    result = asyncio.run(tts.synth(scene.narration, assets_dir / f"vo_{scene.scene_id}.mp3"))
    vo_id = f"vo_{scene.scene_id}"
    project.assets[vo_id] = Asset(
        type="audio",
        path=f"assets/vo_{scene.scene_id}.mp3",
        model=config.tts.engine,
        cost=0.0,
        status="done",
    )

    # 2) 字幕 = TTS 时间戳聚合（与配音零成本对齐）
    # edge-tts 词事件不含标点 → 先把文案标点回贴到词上，断句规则才可用
    words = reattach_punctuation(result.words, scene.narration)
    project.subtitles.extend(build_subtitles(words, scene.scene_id))

    # 3) 生图（缓存命中 0 API 调用）
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
