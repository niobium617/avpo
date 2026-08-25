"""编辑 API（M6-7.6）—— 人类触发的单次操作，不走 run_task（无自动重试）。

与 run_* 节点函数的分工：run_* 是管线自动执行（状态机/重试/进度），这里是
创作者在 UI/CLI 里的每一次主动编辑，全部「落盘 + 按需失效下游」：

- update_scenes          分镜编辑（文案可改/增删/排序）→ direct 起全下游重跑
- update_brief           创作简报 → direct 起全下游重跑
- select_image_candidate 候选图改选 → 只失效 timeline/export（素材不重跑）
- add/remove_reference_image  参考图图组管理（多角度/角色标签）
- add_bgm                BGM 早期上传 → export 重跑
- reroll_scene_candidates 候选图换种子重 roll（人类触发单次）
- refine_scene_image     图生图精修：选中图作参考 + 同种子 → 新候选 v_{k+1}
- rewrite_scene          单场景 AI 重写（narration 默认不动，scene_id 保持）

ad-hoc 操作（reroll/refine/rewrite）不自动重试 —— 人类触发单次，失败直接
报错给创作者看，不掩盖问题（与 run_task 的自动重试语义相反，文档已说明）。
"""

import random
import shutil
from pathlib import Path

from app.core.pipeline import _invalidate_downstream
from app.core.project import ProjectStore
from app.core.schema import PIPELINE_NODES, Asset, Brief, Project, ReferenceImage, Scene
from app.core.styles import compose_image_prompt, load_style
from app.director.director import Director
from app.vision.base import ImageProvider
from app.vision.cache import ImageCache, prompt_hash

BGM_FILENAME = "bgm.mp3"            # 导出节点约定的 BGM 路径（assets/bgm.mp3）


def _get_scene(project: Project, scene_id: str) -> Scene:
    for s in project.scenes:
        if s.scene_id == scene_id:
            return s
    raise ValueError(f"场景不存在: {scene_id}")


def _ensure_image_cache(store: ProjectStore, project: Project, image: ImageProvider) -> None:
    """ad-hoc 生图同样接项目缓存（管线持有生命周期，此处补上）。"""
    if image.cache is None:
        image.cache = ImageCache(store.project_dir(project.project_id))


def _invalidate_from(project: Project, node: str) -> None:
    """从 node 起（含 node）置下游 pending —— 编辑把 node 的产物作废了，重跑起点就是它。

    与 pipeline._invalidate_downstream（仅 node 之后）的区别：update_brief 这类
    编辑直接改掉 node 的输入，node 自身必须重跑；update_scenes 这类编辑（产物已
    含人改）node 保持 done，用 _invalidate_downstream。
    """
    project.pipeline[node] = "pending"
    for downstream in PIPELINE_NODES[PIPELINE_NODES.index(node) + 1:]:
        project.pipeline[downstream] = "pending"


def update_scenes(store: ProjectStore, project: Project, scenes: list[Scene]) -> Project:
    """分镜人工修改落盘：文案可改（TTS sidecar 按 narration_hash 自动失效重合成）、
    支持增删排序。删除场景会先清理其字幕/资产引用（模型校验器在赋值时重跑，
    悬挂引用会炸）。落盘后 direct 下游全部重置 pending。
    """
    removed = {s.scene_id for s in project.scenes} - {s.scene_id for s in scenes}
    if removed:
        # 先清引用再赋值：删除场景的字幕 + 资产（vo_* / img_* 及候选/首尾帧）
        project.subtitles = [s for s in project.subtitles if s.scene_id not in removed]
        for sid in removed:
            for cid in list(project.assets):
                if cid == f"vo_{sid}" or cid.startswith(f"img_{sid}_"):
                    asset = project.assets.pop(cid, None)
                    if asset:
                        (store.project_dir(project.project_id) / asset.path).unlink(missing_ok=True)
    project.scenes = scenes
    _invalidate_downstream(project, "direct")
    store.save(project, message=f"{project.project_id}: 分镜人工修改")
    return project


def update_brief(store: ProjectStore, project: Project, brief: Brief) -> Project:
    """创作简报落盘：direct 起（含）全下游 pending —— 简报是 direct 的输入，
    改了必须重生成分镜。"""
    project.brief = brief
    _invalidate_from(project, "direct")
    store.save(project, message=f"{project.project_id}: 创作简报更新")
    return project


def select_image_candidate(store: ProjectStore, project: Project, scene_id: str, asset_id: str) -> Project:
    """候选图改选：只失效 timeline（含）起 —— 素材不重跑，时间线改引用即可。"""
    scene = _get_scene(project, scene_id)
    if asset_id not in scene.image_candidates:
        raise ValueError(f"{asset_id} 不在 {scene_id} 的候选清单中: {scene.image_candidates}")
    scene.image_asset_id = asset_id
    _invalidate_from(project, "timeline")
    store.save(project, message=f"{project.project_id}: 选中候选图 {asset_id}")
    return project


def add_reference_image(
    store: ProjectStore,
    project: Project,
    path: Path,
    *,
    angle: str = "",
    role: str = "",
) -> Project:
    """参考图入项目：拷贝到 assets/refs/<id>.png（多角度图组，angle/role 打标签）。

    文件落项目目录（不依赖上传临时文件）；id 递增 ref_<n>（删除过也不复用）。
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"参考图文件不存在: {path}")
    # id 单调递增：删除过也不复用（计数器持久在 project.json；backfill 兼容
    # 中途版本按列表算 id 的旧数据）
    nums = [
        int(r.id.rsplit("_", 1)[1])
        for r in project.reference_images
        if r.id.startswith("ref_") and r.id.rsplit("_", 1)[1].isdigit()
    ]
    project.reference_seq = max(project.reference_seq, max(nums, default=0))
    project.reference_seq += 1
    ref_id = f"ref_{project.reference_seq}"
    dest = store.project_dir(project.project_id) / "assets" / "refs" / f"{ref_id}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    project.reference_images.append(ReferenceImage(
        id=ref_id,
        path=str(dest.relative_to(store.project_dir(project.project_id))).replace("\\", "/"),
        angle=angle,
        role=role,
    ))
    # 参考图变了 → 后续生图重跑（gen_assets 起含）
    _invalidate_from(project, "gen_assets")
    store.save(project, message=f"{project.project_id}: 添加参考图 {ref_id}")
    return project


def remove_reference_image(store: ProjectStore, project: Project, ref_id: str) -> Project:
    """移除参考图：删列表条目 + 项目内拷贝文件。"""
    refs = [r for r in project.reference_images if r.id == ref_id]
    if not refs:
        raise ValueError(f"参考图不存在: {ref_id}")
    ref = refs[0]
    project.reference_images = [r for r in project.reference_images if r.id != ref_id]
    (store.project_dir(project.project_id) / ref.path).unlink(missing_ok=True)
    # 参考图变了 → 后续生图重跑
    _invalidate_from(project, "gen_assets")
    store.save(project, message=f"{project.project_id}: 移除参考图 {ref_id}")
    return project


def add_bgm(store: ProjectStore, project: Project, path: Path) -> Project:
    """BGM 早期上传：拷贝到 assets/bgm.mp3（导出节点约定路径），export 重跑。"""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"BGM 文件不存在: {path}")
    dest = store.project_dir(project.project_id) / "assets" / BGM_FILENAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    project.pipeline["export"] = "pending"
    store.save(project, message=f"{project.project_id}: 上传 BGM")
    return project


def reroll_scene_candidates(
    store: ProjectStore, project: Project, scene_id: str, image: ImageProvider
) -> Project:
    """候选图重 roll：换新种子重生成全部候选（人类触发单次，不走 run_task）。

    覆盖同 id 资产与文件（旧候选作废）；选中重置为 v1；timeline/export 重跑。
    """
    scene = _get_scene(project, scene_id)
    _ensure_image_cache(store, project, image)
    assets_dir = store.project_dir(project.project_id) / "assets"
    n = project.config.image.candidates
    style = load_style(project.config.style)
    prompt = compose_image_prompt(style, project.brief, scene)

    candidates: list[str] = []
    cost = 0.0
    for i in range(1, n + 1):
        cid = f"img_{scene_id}_v{i}"
        gen = image.generate(
            prompt,
            assets_dir / f"{cid}.png",
            model=project.config.image.model,
            size=project.config.image.size,
            seed=random.randint(0, 10**9),          # 换新种子：与旧候选同键不冲突
        )
        project.assets[cid] = Asset(
            type="image",
            path=f"assets/{cid}.png",
            model=project.config.image.model,
            prompt_hash=prompt_hash(prompt, project.config.image.model, project.config.image.size, seed=gen.seed),
            seed=gen.seed,
            cost=gen.cost,
            status="done",
        )
        candidates.append(cid)
        cost += gen.cost

    scene.image_candidates = candidates
    scene.image_asset_id = f"img_{scene_id}_v1"
    scene.cost["image"] = cost
    _invalidate_from(project, "timeline")           # 图换了 → 时间线（含）起重跑
    store.save(project, message=f"{project.project_id}: 重 roll 候选图 {scene_id}")
    return project


def refine_scene_image(
    store: ProjectStore, project: Project, scene_id: str, image: ImageProvider
) -> Project:
    """图生图精修：选中候选作参考 + 同种子 → 新候选 v_{k+1}，自动选中。

    渠道不支持参考图注入 → ValueError（UI 提示降级：改提示词后重 roll）。
    新资产附 reference_asset_id（生成时用的参考图，供追溯与缓存键隔离）。
    """
    scene = _get_scene(project, scene_id)
    if not scene.image_asset_id:
        raise ValueError(f"scene {scene_id} 尚未生成候选图，先跑 gen_assets")
    if not image.supports_reference_image:
        raise ValueError(
            f"当前渠道不支持参考图注入（supports_reference_image=False），"
            f"图生图精修不可用；可改提示词后重 roll"
        )
    selected = project.assets.get(scene.image_asset_id)
    if selected is None:
        raise ValueError(f"选中图资产不存在: {scene.image_asset_id}")
    ref_path = store.project_dir(project.project_id) / selected.path
    if not ref_path.is_file():
        raise ValueError(f"选中图文件缺失: {ref_path}")

    _ensure_image_cache(store, project, image)
    assets_dir = store.project_dir(project.project_id) / "assets"
    style = load_style(project.config.style)
    prompt = compose_image_prompt(style, project.brief, scene)
    k = len(scene.image_candidates) + 1
    cid = f"img_{scene_id}_v{k}"
    gen = image.generate(
        prompt,
        assets_dir / f"{cid}.png",
        model=project.config.image.model,
        size=project.config.image.size,
        seed=selected.seed,                         # 同种子：确定性重绘，精修可复现
        reference_png=ref_path,
    )
    project.assets[cid] = Asset(
        type="image",
        path=f"assets/{cid}.png",
        model=project.config.image.model,
        prompt_hash=prompt_hash(prompt, project.config.image.model, project.config.image.size, seed=gen.seed),
        seed=gen.seed,
        cost=gen.cost,
        status="done",
        reference_asset_id=scene.image_asset_id,
    )
    scene.image_candidates.append(cid)
    scene.image_asset_id = cid                      # 精修结果自动选中
    scene.cost["image"] = scene.cost.get("image", 0.0) + gen.cost
    _invalidate_from(project, "timeline")           # 图换了 → 时间线（含）起重跑
    store.save(project, message=f"{project.project_id}: 精修 {scene_id} → {cid}")
    return project


def rewrite_scene(
    store: ProjectStore,
    project: Project,
    director: Director,
    scene_id: str,
    instructions: str = "",
) -> Project:
    """单场景 AI 重写（分镜编辑器「AI 重写」）：narration 默认不动，scene_id 保持。

    LLM 成本并入该场景累计；direct 下游全量 pending（文案/提示词可能变化 →
    素材重跑；未变场景走 TTS sidecar / 生图缓存）。
    """
    if not instructions.strip():
        raise ValueError("重写指令为空")
    scene = _get_scene(project, scene_id)
    style = load_style(project.config.style)
    new_scene, cost = director.rewrite_scene(
        scene, instructions, brief=project.brief, shot_size_hint=style.shot_size_hint
    )
    new_scene.cost = dict(scene.cost)               # 保留历史成本，累加重写 LLM 费
    new_scene.cost["llm"] = new_scene.cost.get("llm", 0.0) + cost
    project.scenes[project.scenes.index(scene)] = new_scene
    _invalidate_downstream(project, "direct")
    store.save(project, message=f"{project.project_id}: 重写分镜 {scene_id}")
    return project
