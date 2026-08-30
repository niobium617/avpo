"""M7 动态化验收：运镜关键帧计划（animate 节点）+ 首尾帧尾拍 + 旧项目 shim。

覆盖链路：resolve_motion_plan（唯一解析点）→ run_animate（落盘+失效）→
build_timeline（尾拍组装）→ 剪映导出（关键帧运镜/尾拍段/旧项目兜底）→
edits 首尾帧编辑 → ProjectStore.load shim（旧 5 节点 pipeline 补 animate）。
"""

import json
import struct
import zlib
from pathlib import Path

import pytest

from app.core.edits import clear_end_frame, select_end_frame
from app.core.motion import END_FRAME_MS, PAN_EXTENT, ZOOM_SCALE, resolve_motion_plan
from app.core.pipeline import run_animate
from app.core.progress import ProgressEvent
from app.core.project import ProjectStore
from app.core.schema import (
    Asset,
    MotionPlan,
    PIPELINE_NODES,
    Project,
    Scene,
)
from app.core.state import FatalError
from app.export.jianying import export
from app.timeline.builder import build_timeline

# 极简 MP3：MPEG-1 Layer III 帧头 + 静音数据（128kbps @ 44100Hz）。
# 帧长必须与头声明一致（144×128000/44100 = 417B），mutagen 严格按帧长同步，
# 帧内补零 413B；20 帧 ≈ 521ms（mutagen 实测），够配音段用。
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413
_MP3_FRAMES = 20
_VO_MS = 521


def _make_png(path: Path, w: int = 64, h: int = 64, rgb: tuple = (30, 60, 120)) -> None:
    """纯 Python 生成最小合法 PNG（zlib + struct，无第三方依赖）。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _load_draft(draft_dir: Path) -> dict:
    return json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))


def _video_segments(draft: dict) -> list[dict]:
    return [t for t in draft["tracks"] if t["type"] == "video"][0]["segments"]


def _keyframe_map(segment: dict) -> dict[str, list[tuple[int, float]]]:
    """按属性名取关键帧列表: {property_type: [(time_offset_us, value), ...]}。"""
    out: dict[str, list[tuple[int, float]]] = {}
    for kf_list in segment.get("common_keyframes", []):
        out[kf_list["property_type"]] = [
            (kf["time_offset"], kf["values"][0]) for kf in kf_list["keyframe_list"]
        ]
    return out


def _scene(motion: str, end_frame: str | None = None, scene_id: str = "s1", **kw) -> Scene:
    data = dict(scene_id=scene_id, narration="你好。", visual="v", image_prompt="p",
                motion=motion, status="done", **kw)
    if end_frame:
        data["end_image_asset_id"] = end_frame
    return Scene(**data)


# ---------------------------------------------------------------- motion 解析

@pytest.mark.parametrize("motion,expect", [
    ("zoom_in_slow", dict(scale_from=1.0, scale_to=ZOOM_SCALE, pan_from=0.0, pan_to=0.0)),
    ("zoom_out", dict(scale_from=ZOOM_SCALE, scale_to=1.0, pan_from=0.0, pan_to=0.0)),
    ("pan_left", dict(scale_from=ZOOM_SCALE, scale_to=ZOOM_SCALE, pan_from=-PAN_EXTENT, pan_to=PAN_EXTENT)),
    ("pan_right", dict(scale_from=ZOOM_SCALE, scale_to=ZOOM_SCALE, pan_from=PAN_EXTENT, pan_to=-PAN_EXTENT)),
    ("none", dict(scale_from=1.0, scale_to=1.0, pan_from=0.0, pan_to=0.0)),
])
def test_resolve_motion_plan_kinds(motion: str, expect: dict) -> None:
    plan = resolve_motion_plan(_scene(motion))
    assert plan.model_dump(exclude={"end_frame_ms"}) == expect


def test_resolve_motion_plan_end_frame() -> None:
    assert resolve_motion_plan(_scene("none")).end_frame_ms == 0
    plan = resolve_motion_plan(_scene("zoom_in_slow", end_frame="img_s1_v2"))
    assert plan.end_frame_ms == END_FRAME_MS
    assert plan.scale_to == ZOOM_SCALE           # 运镜与尾拍时长互不影响


# ---------------------------------------------------------------- animate 节点

def _animate_project(store: ProjectStore, motions: list[str]) -> Project:
    project = Project(project_id="proj_a", title="animate 测试")
    project.pipeline.update({"gen_assets": "done", "confirm": "done", "direct": "done"})
    project.assets = {   # end_frame 引用需存在资产（校验器逐次重跑：资产先于场景）
        "img_s1_v2": Asset(type="image", path="assets/img_s1_v2.png", status="done"),
        "img_s1_v3": Asset(type="image", path="assets/img_s1_v3.png", status="done"),
    }
    project.scenes = [
        _scene(m, end_frame="img_s1_v2" if m == "zoom_in_slow" else None, scene_id=f"s{i}")
        for i, m in enumerate(motions, start=1)
    ]
    store.create(project)
    return project


def test_run_animate_writes_plans_and_invalidates(store) -> None:
    project = _animate_project(store, ["zoom_in_slow", "pan_left", "none"])

    assert run_animate(store, project) is True

    loaded = store.load("proj_a")
    assert loaded.scenes[0].motion_plan.model_dump() == resolve_motion_plan(
        loaded.scenes[0]).model_dump()
    assert loaded.scenes[1].motion_plan.pan_from == -PAN_EXTENT
    assert loaded.scenes[2].motion_plan.end_frame_ms == 0
    assert loaded.scenes[0].motion_plan.end_frame_ms == END_FRAME_MS
    # 下游失效：timeline/export 重置，animate 自身 done
    assert loaded.pipeline["animate"] == "done"
    assert loaded.pipeline["timeline"] == "pending"
    assert loaded.pipeline["export"] == "pending"


def test_run_animate_requires_gen_assets(store) -> None:
    project = Project(project_id="proj_a", scenes=[_scene("none")])
    store.create(project)

    assert run_animate(store, project) is False
    assert store.load("proj_a").pipeline["animate"] == "failed"
    assert any("gen_assets 未完成" in e.error for e in store.load("proj_a").errors)


def test_run_animate_done_skips_rerun(store) -> None:
    project = _animate_project(store, ["zoom_in_slow"])
    assert run_animate(store, project) is True
    loaded = store.load("proj_a")
    plan_before = loaded.scenes[0].motion_plan.model_dump()

    assert run_animate(store, loaded) is True    # done 跳过，不重写
    assert store.load("proj_a").scenes[0].motion_plan.model_dump() == plan_before


def test_run_animate_progress_events(store) -> None:
    project = _animate_project(store, ["none", "zoom_out"])
    events: list[ProgressEvent] = []

    assert run_animate(store, project, progress=events.append) is True

    assert [(e.node, e.percent) for e in events] == [("animate", 0.5), ("animate", 1.0)]
    assert all(e.message.startswith("解析运镜") for e in events)


# ---------------------------------------------------------------- load shim

def _write_raw_json(store: ProjectStore, pid: str, data: dict) -> None:
    path = store.json_path(pid)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_load_shim_inserts_animate_node(store) -> None:
    """0.2 旧项目：pipeline 只有 5 键 → 加载后补 animate/transcribe（pending）并按序重建。"""
    old_nodes = [n for n in PIPELINE_NODES if n not in ("animate", "transcribe")]
    _write_raw_json(store, "legacy", {
        "project_id": "legacy",
        "schema_version": "0.2",
        "pipeline": {n: "done" for n in old_nodes},
        "scenes": [{"scene_id": "s1", "narration": "hi", "motion": "none"}],
    })

    project = store.load("legacy")
    assert list(project.pipeline) == list(PIPELINE_NODES)
    assert project.pipeline["animate"] == "pending"
    assert project.pipeline["transcribe"] == "pending"   # M10 新节点补键
    for n in old_nodes:
        assert project.pipeline[n] == "done"     # 旧状态保留，断点续跑语义不破坏
    assert project.schema_version == "0.6"       # M10：内存升版到 0.6


def test_load_keeps_030_project_untouched(store) -> None:
    project = Project(project_id="p3", scenes=[_scene("none")])
    store.create(project)
    loaded = store.load("p3")                    # 0.3 项目：pipeline 键不动，版本升 0.6
    assert list(loaded.pipeline) == list(PIPELINE_NODES)
    assert loaded.schema_version == "0.6"


# ---------------------------------------------------------------- timeline 尾拍

def _tail_project(store: ProjectStore) -> tuple[Path, Project]:
    """s1 有首尾帧（尾拍 400ms）+ 运镜；s2 无尾拍。资产/文件齐全。"""
    proj_dir = store.project_dir("proj_t")
    (proj_dir / "assets").mkdir(parents=True)
    for f in ("img_s1_v1.png", "img_s1_v2.png", "img_s2_v1.png"):
        _make_png(proj_dir / "assets" / f)
    for f in ("vo_s1.mp3", "vo_s2.mp3"):
        (proj_dir / "assets" / f).write_bytes(_MP3_FRAME * _MP3_FRAMES)

    project = Project(project_id="proj_t", title="tail")
    project.assets = {
        "img_s1_v1": Asset(type="image", path="assets/img_s1_v1.png", seed=1, status="done"),
        "img_s1_v2": Asset(type="image", path="assets/img_s1_v2.png", seed=2, status="done"),
        "img_s2_v1": Asset(type="image", path="assets/img_s2_v1.png", seed=3, status="done"),
        "vo_s1": Asset(type="audio", path="assets/vo_s1.mp3", status="done"),
        "vo_s2": Asset(type="audio", path="assets/vo_s2.mp3", status="done"),
    }
    project.scenes = [
        Scene(scene_id="s1", narration="一。", visual="v1", image_prompt="p1",
              image_asset_id="img_s1_v1", end_image_asset_id="img_s1_v2",
              motion="zoom_in_slow", status="done",
              motion_plan=MotionPlan(scale_from=1.0, scale_to=ZOOM_SCALE, end_frame_ms=400)),
        Scene(scene_id="s2", narration="二。", visual="v2", image_prompt="p2",
              image_asset_id="img_s2_v1", motion="none", status="done"),
    ]
    store.create(project)
    return proj_dir, project


def test_build_timeline_appends_tail(store) -> None:
    proj_dir, project = _tail_project(store)
    build_timeline(project, proj_dir)

    clips = project.timeline.video
    assert len(clips) == 3
    main1, tail, main2 = clips
    assert (main1.asset_id, main1.start_ms, main1.duration_ms, main1.motion, main1.scene_id) \
        == ("img_s1_v1", 0, _VO_MS, "zoom_in_slow", "s1")
    assert (tail.asset_id, tail.start_ms, tail.duration_ms, tail.motion, tail.scene_id) \
        == ("img_s1_v2", _VO_MS, 400, "none", "s1")
    assert (main2.asset_id, main2.start_ms, main2.duration_ms, main2.motion, main2.scene_id) \
        == ("img_s2_v1", _VO_MS + 400, _VO_MS, "none", "s2")
    # 场景起点顺延尾拍；配音轨不变；整片时长含尾拍
    assert project.scenes[0].start_ms == 0
    assert project.scenes[1].start_ms == _VO_MS + 400
    assert len(project.timeline.voiceover) == 2
    assert project.voiceover.duration_ms == 2 * _VO_MS + 400


def test_build_timeline_no_plan_no_tail(store) -> None:
    """旧项目（无 motion_plan）：每镜单 clip，行为与 M6 一致。"""
    proj_dir, project = _tail_project(store)
    for s in project.scenes:
        s.motion_plan = None
        s.end_image_asset_id = None
    build_timeline(project, proj_dir)

    assert len(project.timeline.video) == 2
    assert project.voiceover.duration_ms == 2 * _VO_MS
    assert project.scenes[1].start_ms == _VO_MS


def test_build_timeline_tail_without_end_asset_fails(store) -> None:
    """计划有尾拍但结束帧字段为空 → FatalError（手工改 JSON 的兜底拦截）。"""
    proj_dir, project = _tail_project(store)
    project.scenes[0].end_image_asset_id = None   # 计划仍带 end_frame_ms
    with pytest.raises(FatalError, match="未设结束帧"):
        build_timeline(project, proj_dir)


# ---------------------------------------------------------------- 导出

def test_export_uses_motion_plan_keyframes_and_tail(store) -> None:
    """animate 产出的计划 → 关键帧；尾拍段单独导出且无动画。"""
    proj_dir, project = _tail_project(store)
    build_timeline(project, proj_dir)
    draft_dir = export(project, proj_dir, proj_dir / "exports")

    segments = _video_segments(_load_draft(draft_dir))
    assert len(segments) == 3
    main1, tail, main2 = segments
    # 时间线毫秒 → 草稿微秒
    assert main1["target_timerange"] == {"start": 0, "duration": _VO_MS * 1000}
    assert tail["target_timerange"] == {"start": _VO_MS * 1000, "duration": 400_000}
    assert main2["target_timerange"] == {"start": (_VO_MS + 400) * 1000, "duration": _VO_MS * 1000}
    # 主段有缩放关键帧；尾拍段与无运镜段不加任何动画
    assert _keyframe_map(main1)["KFTypeScaleX"] == [(0, 1.0), (_VO_MS * 1000, ZOOM_SCALE)]
    assert _keyframe_map(tail) == {}
    assert _keyframe_map(main2) == {}
    assert _load_draft(draft_dir)["materials"]["material_animations"] == []


def test_export_old_data_fallback_by_asset_prefix(store) -> None:
    """0.2 旧数据：clip 无 scene_id、无 motion_plan → 按图资产 id 前缀找回场景出运镜。"""
    proj_dir = store.project_dir("proj_o")
    (proj_dir / "assets").mkdir(parents=True)
    _make_png(proj_dir / "assets" / "img_s1_v1.png")
    project = Project(project_id="proj_o", title="old")
    project.assets = {
        "img_s1_v1": Asset(type="image", path="assets/img_s1_v1.png", seed=1, status="done"),
    }
    project.scenes = [
        Scene(scene_id="s1", narration="一。", visual="v", image_prompt="p",
              image_asset_id="img_s1_v1", motion="pan_left", status="done"),
    ]
    from app.core.schema import VideoClip
    project.timeline.video = [VideoClip(asset_id="img_s1_v1", start_ms=0, duration_ms=500,
                                        motion="pan_left")]   # 无 scene_id（旧式）
    store.create(project)

    draft_dir = export(project, proj_dir, proj_dir / "exports")
    kf = _keyframe_map(_video_segments(_load_draft(draft_dir))[0])
    assert kf["KFTypePositionX"] == [(0, -PAN_EXTENT), (500_000, PAN_EXTENT)]
    assert kf["KFTypeScaleX"] == [(0, ZOOM_SCALE), (500_000, ZOOM_SCALE)]


# ---------------------------------------------------------------- edits 首尾帧

def _edits_project(store: ProjectStore) -> Project:
    project = Project(project_id="proj_e", title="edits")
    project.assets = {
        "img_s1_v1": Asset(type="image", path="assets/img_s1_v1.png", seed=1, status="done"),
        "img_s1_v2": Asset(type="image", path="assets/img_s1_v2.png", seed=2, status="done"),
    }
    project.scenes = [
        Scene(scene_id="s1", narration="一。", visual="v", image_prompt="p",
              image_candidates=["img_s1_v1", "img_s1_v2"], image_asset_id="img_s1_v1",
              status="done"),
    ]
    project.pipeline.update({"animate": "done", "timeline": "done", "export": "done"})
    store.create(project)
    return project


def test_select_end_frame_sets_and_invalidates(store) -> None:
    project = _edits_project(store)

    select_end_frame(store, project, "s1", "img_s1_v2")

    loaded = store.load("proj_e")
    assert loaded.scenes[0].end_image_asset_id == "img_s1_v2"
    assert loaded.pipeline["animate"] == "pending"    # 计划要重解析（尾拍时长变化）
    assert loaded.pipeline["timeline"] == "pending"
    assert loaded.pipeline["export"] == "pending"


def test_select_end_frame_rejects_non_candidate(store) -> None:
    project = _edits_project(store)
    with pytest.raises(ValueError, match="不在.*候选清单"):
        select_end_frame(store, project, "s1", "img_ghost")


def test_clear_end_frame(store) -> None:
    project = _edits_project(store)
    select_end_frame(store, project, "s1", "img_s1_v2")

    clear_end_frame(store, store.load("proj_e"), "s1")

    loaded = store.load("proj_e")
    assert loaded.scenes[0].end_image_asset_id is None
    assert loaded.pipeline["animate"] == "pending"
