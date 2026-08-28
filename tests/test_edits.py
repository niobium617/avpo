"""M6-7.6 编辑 API 测试：分镜编辑/简报/候选改选/参考图/BGM/重 roll/精修/重写。

全部离线：生图用假渠道（计数 + 记录 seed/参考图），重写用假 Director。
ad-hoc 操作（reroll/refine/rewrite）不走 run_task —— 断言直接落盘 + 按需失效。
"""

from pathlib import Path

import pytest

from app.core import edits
from app.core.schema import (
    PIPELINE_NODES,
    Asset,
    Brief,
    ImageConfig,
    Project,
    ProjectConfig,
    Scene,
    Subtitle,
)
from app.director.director import Director
from app.vision.base import PNG_MAGIC, ImageProvider


class _FakeImage(ImageProvider):
    """计数生图渠道：记录 seed/参考图调用，PNG 内容带序号可辨新旧。"""

    cost_per_image = 0.02
    supports_reference_image = True

    def __init__(self):
        super().__init__()
        self.calls = 0
        self.requested_seeds: list[int | None] = []
        self.requested_refs: list[Path | None] = []

    def _request_png(self, prompt, model, pixel_size, seed):
        self.calls += 1
        self.requested_seeds.append(seed)
        self.requested_refs.append(None)
        return PNG_MAGIC + f"img-{self.calls}".encode()

    def _request_png_ref(self, prompt, model, pixel_size, seed, reference_png):
        self.calls += 1
        self.requested_seeds.append(seed)
        self.requested_refs.append(Path(reference_png))
        return PNG_MAGIC + f"ref-{self.calls}".encode()


class _FakeDirector(Director):
    """假导演：rewrite_scene 原样返回预设场景，记录入参。"""

    def __init__(self, new_scene: Scene | None = None):
        self.new_scene = new_scene or Scene(
            scene_id="s1", narration="保持文案。", visual="新画面",
            image_prompt="new prompt", motion="pan_right",
        )
        self.calls: list[tuple] = []

    def rewrite_scene(self, scene, instructions, *, brief=None, shot_size_hint=""):
        self.calls.append((scene, instructions, brief, shot_size_hint))
        return self.new_scene.model_copy(deep=True), 0.004


def _project_with_candidates(store, *, n: int = 3, scene_ids=("s1",)) -> Project:
    """模拟 gen_assets 后的项目：候选资产 + 落盘文件 + 场景状态齐备、管线全 done。"""
    project = Project(
        project_id="proj_edit",
        config=ProjectConfig(image=ImageConfig(candidates=n)),
    )
    scenes, assets = [], {}
    for sid in scene_ids:
        candidates = []
        for i in range(1, n + 1):
            cid = f"img_{sid}_v{i}"
            assets[cid] = Asset(
                type="image", path=f"assets/{cid}.png", model="wanx2.1-t2i-turbo",
                seed=100 + i, cost=0.02, status="done",
            )
            candidates.append(cid)
        scenes.append(Scene(
            scene_id=sid, narration=f"{sid} 文案。", image_prompt=f"prompt {sid}",
            image_asset_id=f"img_{sid}_v1", image_candidates=candidates,
            cost={"image": 0.06}, status="done",
        ))
    project.assets = assets                                  # 先资产后场景：赋值即校验，场景引用需要资产在位
    project.scenes = scenes
    project.pipeline = {node: "done" for node in PIPELINE_NODES}
    store.create(project)
    assets_dir = store.project_dir(project.project_id) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for cid, asset in assets.items():
        (assets_dir / f"{cid}.png").write_bytes(PNG_MAGIC + cid.encode())
    return project


# ---------------------------------------------------------------- 分镜编辑

def test_update_scenes_delete_scene_cleans_refs(store):
    """删除场景：字幕/资产/文件一并清理（先清引用再赋值，校验器重跑不炸）。"""
    project = _project_with_candidates(store, scene_ids=("s1", "s2", "s3"))
    project.subtitles = [
        Subtitle(scene_id="s1", start_ms=0, end_ms=1000, text="s1 字幕"),
        Subtitle(scene_id="s3", start_ms=1000, end_ms=2000, text="s3 字幕"),
    ]
    project.assets["vo_s3"] = Asset(type="audio", path="assets/vo_s3.mp3", model="edge-tts", status="done")
    (store.project_dir("proj_edit") / "assets" / "vo_s3.mp3").write_bytes(b"mp3")
    store.save(project, message="补 s3 素材")

    keep = [s for s in project.scenes if s.scene_id != "s3"]
    edits.update_scenes(store, project, keep)

    loaded = store.load("proj_edit")
    assert [s.scene_id for s in loaded.scenes] == ["s1", "s2"]
    assert all(sub.scene_id != "s3" for sub in loaded.subtitles)
    assert not any(cid.startswith("img_s3") or cid == "vo_s3" for cid in loaded.assets)
    project_dir = store.project_dir("proj_edit")
    assert not (project_dir / "assets" / "img_s3_v1.png").is_file()
    assert not (project_dir / "assets" / "vo_s3.mp3").is_file()
    # 直接下游 pending（direct 起全重跑）
    assert loaded.pipeline["direct"] == "done"
    assert loaded.pipeline["gen_assets"] == "pending"
    assert loaded.pipeline["export"] == "pending"


def test_update_scenes_append_and_reorder(store):
    """新增场景 + 换序：按给定顺序落盘。"""
    project = _project_with_candidates(store)
    new_scene = Scene(scene_id="s2", narration="新增场景。", image_prompt="p new")
    scenes = [new_scene, project.scenes[0]]

    edits.update_scenes(store, project, scenes)

    assert [s.scene_id for s in store.load("proj_edit").scenes] == ["s2", "s1"]


def test_update_scenes_rejects_duplicate_scene_id(store):
    project = _project_with_candidates(store)
    scenes = [project.scenes[0].model_copy(deep=True), project.scenes[0].model_copy(deep=True)]
    with pytest.raises(ValueError, match="scene_id"):
        edits.update_scenes(store, project, scenes)


# ---------------------------------------------------------------- 简报/候选改选

def test_update_brief_sets_and_invalidates(store):
    project = _project_with_candidates(store)
    brief = Brief(theme="深夜电台", art_style="胶片感", lighting="volumetric light")

    edits.update_brief(store, project, brief)

    loaded = store.load("proj_edit")
    assert loaded.brief.theme == "深夜电台" and loaded.brief.lighting == "volumetric light"
    assert loaded.pipeline["direct"] == "pending"           # direct 起全下游
    assert loaded.pipeline["gen_assets"] == "pending"
    assert loaded.pipeline["export"] == "pending"


def test_select_image_candidate(store):
    project = _project_with_candidates(store)

    edits.select_image_candidate(store, project, "s1", "img_s1_v2")

    loaded = store.load("proj_edit")
    assert loaded.scenes[0].image_asset_id == "img_s1_v2"
    assert loaded.pipeline["gen_assets"] == "done"          # 素材不重跑
    assert loaded.pipeline["timeline"] == "pending"         # 时间线改引用
    assert loaded.pipeline["export"] == "pending"


def test_select_image_candidate_rejects_non_candidate(store):
    project = _project_with_candidates(store)
    with pytest.raises(ValueError, match="不在 .* 的候选清单"):
        edits.select_image_candidate(store, project, "s1", "img_s9_v1")


# ---------------------------------------------------------------- 参考图 / BGM

def test_add_reference_image(store, tmp_path):
    project = _project_with_candidates(store)
    src = tmp_path / "ref.png"
    src.write_bytes(PNG_MAGIC + b"ref-body")

    edits.add_reference_image(store, project, src, angle="45°仰视", role="主角")

    loaded = store.load("proj_edit")
    assert [r.model_dump() for r in loaded.reference_images] == [{
        "id": "ref_1", "path": "assets/refs/ref_1.png", "angle": "45°仰视", "role": "主角",
    }]
    assert (store.project_dir("proj_edit") / "assets" / "refs" / "ref_1.png").read_bytes() == src.read_bytes()
    # 参考图变了 → 后续生图重跑（gen_assets 起含）
    assert loaded.pipeline["gen_assets"] == "pending"
    assert loaded.pipeline["export"] == "pending"


def test_remove_reference_image(store, tmp_path):
    project = _project_with_candidates(store)
    src = tmp_path / "ref.png"
    src.write_bytes(PNG_MAGIC + b"ref-body")
    edits.add_reference_image(store, project, src)

    edits.remove_reference_image(store, project, "ref_1")

    loaded = store.load("proj_edit")
    assert loaded.reference_images == []
    assert not (store.project_dir("proj_edit") / "assets" / "refs" / "ref_1.png").is_file()
    # 删除过不复用 id：再加一张 → ref_2
    edits.add_reference_image(store, project, src)
    assert store.load("proj_edit").reference_images[0].id == "ref_2"
    with pytest.raises(ValueError, match="参考图不存在"):
        edits.remove_reference_image(store, project, "ref_99")


def test_add_bgm(store, tmp_path):
    project = _project_with_candidates(store)
    src = tmp_path / "bgm.mp3"
    src.write_bytes(b"mp3-body")

    edits.add_bgm(store, project, src)

    dest = store.project_dir("proj_edit") / "assets" / "bgm.mp3"
    assert dest.read_bytes() == b"mp3-body"
    loaded = store.load("proj_edit")
    assert loaded.pipeline["export"] == "pending"
    assert loaded.pipeline["timeline"] == "pending"         # M8：卡点对齐依赖 BGM → timeline 起重跑
    assert loaded.pipeline["animate"] == "done"             # 素材上游不动


# ---------------------------------------------------------------- 重 roll / 精修 / 重写

def test_reroll_scene_candidates(store):
    project = _project_with_candidates(store)
    image = _FakeImage()

    edits.reroll_scene_candidates(store, project, "s1", image)

    loaded = store.load("proj_edit")
    scene = loaded.scenes[0]
    assert image.calls == 3                                  # 全部重生成（新种子新键）
    assert scene.image_candidates == ["img_s1_v1", "img_s1_v2", "img_s1_v3"]
    assert scene.image_asset_id == "img_s1_v1"               # 选中重置 v1
    assert scene.cost["image"] == pytest.approx(0.06)
    # 文件被新内容覆盖（假渠道 PNG 带调用序号）
    new_bytes = (store.project_dir("proj_edit") / "assets" / "img_s1_v1.png").read_bytes()
    assert b"img-1" in new_bytes and b"img_s1_v1" not in new_bytes
    assert loaded.pipeline["timeline"] == "pending" and loaded.pipeline["export"] == "pending"
    assert loaded.pipeline["gen_assets"] == "done"


def test_refine_scene_image(store):
    project = _project_with_candidates(store)
    scene = project.scenes[0]
    scene.image_asset_id = "img_s1_v2"                       # 先改选 v2 再精修
    store.save(project)
    image = _FakeImage()

    edits.refine_scene_image(store, project, "s1", image)

    loaded = store.load("proj_edit")
    scene = loaded.scenes[0]
    assert image.calls == 1
    assert image.requested_refs[0].name == "img_s1_v2.png"   # 选中图作参考
    assert image.requested_seeds[0] == 102                   # 同种子（v2 的 seed）
    assert scene.image_candidates == ["img_s1_v1", "img_s1_v2", "img_s1_v3", "img_s1_v4"]
    assert scene.image_asset_id == "img_s1_v4"               # 精修结果自动选中
    assert loaded.assets["img_s1_v4"].reference_asset_id == "img_s1_v2"
    assert scene.cost["image"] == pytest.approx(0.08)        # 0.06 + 0.02 累计
    assert loaded.pipeline["timeline"] == "pending" and loaded.pipeline["export"] == "pending"


def test_refine_requires_capability(store):
    project = _project_with_candidates(store)
    image = _FakeImage()
    image.supports_reference_image = False                   # 模拟 FLUX 渠道降级

    with pytest.raises(ValueError, match="不支持参考图注入"):
        edits.refine_scene_image(store, project, "s1", image)
    assert image.calls == 0


def test_refine_before_assets(store):
    project = _project_with_candidates(store)
    project.scenes[0].image_asset_id = None                  # 尚未生成候选
    store.save(project)

    with pytest.raises(ValueError, match="尚未生成候选图"):
        edits.refine_scene_image(store, project, "s1", _FakeImage())


def test_rewrite_scene(store):
    project = _project_with_candidates(store)
    director = _FakeDirector()

    edits.rewrite_scene(store, project, director, "s1", "改成赛博朋克风")

    loaded = store.load("proj_edit")
    scene = loaded.scenes[0]
    assert director.calls[0][1] == "改成赛博朋克风"           # 指令透传
    assert scene.scene_id == "s1" and scene.narration == "保持文案。"   # narration 默认不动
    assert scene.image_prompt == "new prompt" and scene.motion == "pan_right"
    assert scene.image_asset_id is None and scene.image_candidates == []
    assert scene.cost == {"image": 0.06, "llm": 0.004}       # 历史成本保留 + LLM 费累计
    assert loaded.pipeline["direct"] == "done"               # 重写产物即 direct 产物
    assert loaded.pipeline["confirm"] == "pending"           # 下游全量重跑
    assert loaded.pipeline["gen_assets"] == "pending"


def test_rewrite_scene_empty_instructions(store):
    project = _project_with_candidates(store)
    with pytest.raises(ValueError, match="重写指令为空"):
        edits.rewrite_scene(store, project, _FakeDirector(), "s1", "   ")
