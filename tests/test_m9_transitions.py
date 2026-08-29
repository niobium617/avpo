"""M9 止损转场测试：Scene/VideoClip.transition + 切点转场（闪白/震动 0.3s）。

覆盖：
- schema 0.5：新字段默认值 + 非法取值拒绝；
- timeline：auto 止损（无结束帧的切点自动闪白）+ 有结束帧保持硬切 + 显式覆盖
  + 末场景无切点 + 有尾拍时转场落在尾拍 clip + 组装幂等；
- edits：set_scene_transition 落盘 + timeline 起重跑（animate 不动）+ 非法值；
- export：materials.transitions 条目（名称/时长 300ms）+ 前段 extra_material_refs
  携带转场 id + 全 none 时无转场材料。
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import edits
from app.core.schema import PIPELINE_NODES, Asset, MotionPlan, Project, Scene, VideoClip
from app.export.jianying import export
from app.timeline import builder

# 同 test_m8_audio 的极简 MP3 帧（417B/帧，帧长与头声明一致）
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413


def _make_png(path: Path, rgb: tuple = (30, 60, 120)) -> None:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    w = h = 8
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _make_tl_project() -> Project:
    """2 场景素材齐备、时间线未组装的项目（同 test_m8_audio._make_tl_project 形状）。"""
    return Project(
        project_id="proj_tl",
        scenes=[
            Scene(
                scene_id="s1", narration="AI 正在改变内容创作的方式。",
                image_asset_id="img_s1", motion="zoom_in_slow", status="done",
            ),
            Scene(
                scene_id="s2", narration="现在，一个人也能做视频。",
                image_asset_id="img_s2", motion="pan_right", status="done",
            ),
        ],
        assets={
            "vo_s1": Asset(type="audio", path="assets/vo_s1.mp3", model="edge-tts", status="done"),
            "vo_s2": Asset(type="audio", path="assets/vo_s2.mp3", model="edge-tts", status="done"),
            "img_s1": Asset(type="image", path="assets/img_s1.png", status="done"),
            "img_s2": Asset(type="image", path="assets/img_s2.png", status="done"),
        },
    )


@pytest.fixture()
def tl_dir(tmp_path: Path) -> tuple[Path, Project]:
    proj = tmp_path / "proj"
    assets = proj / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "vo_s1.mp3").write_bytes(_MP3_FRAME * 20)
    (assets / "vo_s2.mp3").write_bytes(_MP3_FRAME * 20)
    _make_png(assets / "img_s1.png")
    _make_png(assets / "img_s2.png", rgb=(180, 90, 40))
    project = _make_tl_project()
    return proj, project


def _patch_durations(monkeypatch, durations: dict[str, int]) -> None:
    monkeypatch.setattr(builder, "_audio_duration_ms", lambda p: durations[p.name])


def _set_end_frame(proj: Path, project: Project, scene_id: str, end_ms: int = 400) -> None:
    """给场景设结束帧：新增资产文件 + 注册 + MotionPlan(end_frame_ms)（模拟 animate 产物）。"""
    end_id = f"img_{scene_id}_end"
    _make_png(proj / "assets" / f"{end_id}.png", rgb=(200, 200, 200))
    project.assets[end_id] = Asset(type="image", path=f"assets/{end_id}.png", status="done")
    for s in project.scenes:
        if s.scene_id == scene_id:
            s.end_image_asset_id = end_id
            s.motion_plan = MotionPlan(end_frame_ms=end_ms)


def _export_draft(proj: Path, project: Project) -> dict:
    draft_dir = export(project, proj, proj / "exports", zip_archive=False)
    return json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))


# ================================================================ schema 0.5

def test_schema_transition_defaults() -> None:
    """新字段默认值：Scene.transition = auto（止损），VideoClip.transition = none。"""
    scene = Scene(scene_id="s1")
    assert scene.transition == "auto"
    clip = VideoClip(asset_id="img_x", duration_ms=1000)
    assert clip.transition == "none"


def test_schema_rejects_invalid_transition() -> None:
    """非法转场取值 → 构造即报错（Scene 与 VideoClip 均拒绝）。"""
    with pytest.raises(ValidationError):
        Scene(scene_id="s1", transition="wipe")
    with pytest.raises(ValidationError):
        VideoClip(asset_id="img_x", duration_ms=1000, transition="auto")   # clip 无 auto（已解析）


# ================================================================ timeline 止损

def test_timeline_auto_flash_white_without_end_frame(tl_dir, monkeypatch) -> None:
    """止损默认：无结束帧的切点自动闪白；末场景无切点恒 none。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})

    builder.build_timeline(project, proj)

    assert [(c.scene_id, c.transition) for c in project.timeline.video] == [
        ("s1", "flash_white"),
        ("s2", "none"),
    ]


def test_timeline_auto_hard_cut_with_end_frame(tl_dir, monkeypatch) -> None:
    """有结束帧的切点保持 M7 硬切：auto 解析为 none，主镜头与尾拍都不带转场。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    _set_end_frame(proj, project, "s1")

    builder.build_timeline(project, proj)

    assert [(c.asset_id, c.transition) for c in project.timeline.video] == [
        ("img_s1", "none"),
        ("img_s1_end", "none"),
        ("img_s2", "none"),
    ]


def test_timeline_explicit_none_overrides_auto(tl_dir, monkeypatch) -> None:
    """显式 none：无结束帧也不止损（创作者逐镜覆盖优先）。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.scenes[0].transition = "none"

    builder.build_timeline(project, proj)

    assert [c.transition for c in project.timeline.video] == ["none", "none"]


def test_timeline_explicit_shake_with_end_frame_lands_on_tail_clip(tl_dir, monkeypatch) -> None:
    """显式 shake 且有尾拍：转场落在尾拍 clip（该场景最后一个 clip），主镜头保持 none。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    _set_end_frame(proj, project, "s1")
    project.scenes[0].transition = "shake"

    builder.build_timeline(project, proj)

    assert [(c.asset_id, c.transition) for c in project.timeline.video] == [
        ("img_s1", "none"),
        ("img_s1_end", "shake"),
        ("img_s2", "none"),
    ]


def test_timeline_explicit_transition_last_scene_ignored(tl_dir, monkeypatch) -> None:
    """末场景显式转场无效：无切点，恒 none。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.scenes[1].transition = "flash_white"

    builder.build_timeline(project, proj)

    assert [c.transition for c in project.timeline.video] == ["flash_white", "none"]


def test_timeline_transitions_idempotent(tl_dir, monkeypatch) -> None:
    """转场解析确定性：重复组装结果一致（含显式 shake + 结束帧路径）。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    _set_end_frame(proj, project, "s1")
    project.scenes[0].transition = "shake"

    builder.build_timeline(project, proj)
    first = project.model_dump(mode="json")
    builder.build_timeline(project, proj)
    assert project.model_dump(mode="json") == first


# ================================================================ edits

def _edit_project(store) -> Project:
    """素材链路已 done 的项目（2 场景，无资产引用）。"""
    project = Project(
        project_id="proj_edit",
        scenes=[Scene(scene_id="s1", narration="场景一。"), Scene(scene_id="s2", narration="场景二。")],
    )
    project.pipeline = {node: "done" for node in PIPELINE_NODES}
    store.create(project)
    return project


def test_set_scene_transition_saves_and_invalidates_timeline(store) -> None:
    """转场设置落盘 + timeline 起重跑，animate/gen_assets 保持 done。"""
    project = _edit_project(store)

    edits.set_scene_transition(store, project, "s1", "shake")

    assert project.scenes[0].transition == "shake"
    assert project.pipeline["timeline"] == "pending"
    assert project.pipeline["animate"] == "done"      # 素材/运镜上游不动
    loaded = store.load("proj_edit")
    assert loaded.scenes[0].transition == "shake"     # 落盘可重读


def test_set_scene_transition_rejects_invalid_value(store) -> None:
    """非法取值：显式 ValueError（web 捕获路径），不改状态。"""
    project = _edit_project(store)

    with pytest.raises(ValueError, match="非法转场类型"):
        edits.set_scene_transition(store, project, "s1", "wipe")
    assert project.scenes[0].transition == "auto"


# ================================================================ export

def _transition_entries(draft: dict) -> list[dict]:
    return [m for m in draft["materials"].get("transitions", []) if m.get("type") == "transition"]


def test_export_transition_material_and_refs(tl_dir, monkeypatch) -> None:
    """导出转场：materials.transitions 条目（名称/时长 300ms）+ 前段 extra_material_refs 携带。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.scenes[0].transition = "shake"
    builder.build_timeline(project, proj)

    draft = _export_draft(proj, project)

    entries = _transition_entries(draft)
    assert len(entries) == 1
    assert entries[0]["name"] == "震动"
    assert entries[0]["duration"] == 300_000          # 0.3s（覆盖素材默认 1s）
    segs = {t["name"]: t["segments"] for t in draft["tracks"]}["v1"]
    assert len(segs) == 2
    assert entries[0]["id"] in segs[0]["extra_material_refs"]      # 前段携带
    assert entries[0]["id"] not in segs[1]["extra_material_refs"]


def test_export_flash_white_name_and_duration(tl_dir, monkeypatch) -> None:
    """闪白映射：materials.transitions 名称闪白，时长 0.3s（覆盖素材默认 0.5s）。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.scenes[0].transition = "flash_white"
    builder.build_timeline(project, proj)

    draft = _export_draft(proj, project)

    entries = _transition_entries(draft)
    assert len(entries) == 1
    assert entries[0]["name"] == "闪白"
    assert entries[0]["duration"] == 300_000


def test_export_no_transitions_when_all_none(tl_dir, monkeypatch) -> None:
    """全 none（两场景都显式关闭）：无任何转场材料。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    for s in project.scenes:
        s.transition = "none"
    builder.build_timeline(project, proj)

    draft = _export_draft(proj, project)

    assert _transition_entries(draft) == []
