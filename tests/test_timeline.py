"""M2-3.1 时间线组装测试（IMPLEMENTATION_PLAN 3.1）。

验收：scene 时长 = 配音实际时长；场景首尾相接无缝隙；总时长 = 配音总长；
字幕按 scene.start_ms 平移到全局时间轴；缺素材抛 FatalError 带修复提示；幂等。
"""

import json
import struct
import zlib
from pathlib import Path

import pytest

from app.core.pipeline import run_timeline
from app.core.schema import (
    Asset,
    Project,
    Scene,
    Subtitle,
)
from app.core.state import FatalError
from app.export.jianying import export
from app.timeline import builder

# 极简 MP3：MPEG-1 Layer III 128kbps@44.1kHz 帧头 + 静音数据。
# 单帧 = floor(144*128000/44100) = 417 字节（payload 413）——帧长必须正确，mutagen 才能同步。
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413
_MP3_FRAMES = 20          # 固定 20 帧 → 文件大小固定 → mutagen 估算时长确定


def _make_png(path: Path, rgb: tuple = (30, 60, 120)) -> None:
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


def _make_assets(root: Path) -> None:
    """root/assets 下放 2 场景素材（vo_*.mp3 + img_*.png）。"""
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "vo_s1.mp3").write_bytes(_MP3_FRAME * _MP3_FRAMES)
    (assets / "vo_s2.mp3").write_bytes(_MP3_FRAME * _MP3_FRAMES)
    _make_png(assets / "img_s1.png")
    _make_png(assets / "img_s2.png", rgb=(180, 90, 40))


def _make_project() -> Project:
    """2 场景素材齐备、时间线未组装的项目对象。"""
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
        subtitles=[
            Subtitle(scene_id="s1", start_ms=100, end_ms=2362, text="AI 正在改变内容创作的方式。"),
            Subtitle(scene_id="s2", start_ms=100, end_ms=2288, text="现在，一个人也能做视频。"),
        ],
        assets={
            "vo_s1": Asset(type="audio", path="assets/vo_s1.mp3", model="edge-tts", status="done"),
            "vo_s2": Asset(type="audio", path="assets/vo_s2.mp3", model="edge-tts", status="done"),
            "img_s1": Asset(type="image", path="assets/img_s1.png", status="done"),
            "img_s2": Asset(type="image", path="assets/img_s2.png", status="done"),
        },
    )


@pytest.fixture()
def project_dir(tmp_path: Path) -> tuple[Path, Project]:
    proj = tmp_path / "proj"
    _make_assets(proj)
    return proj, _make_project()


def _patch_durations(monkeypatch, durations: dict[str, int]) -> list[str]:
    """monkeypatch 配音时长读取，返回被读取的文件名序列。"""
    calls: list[str] = []

    def fake(path: Path) -> int:
        calls.append(path.name)
        return durations[path.name]

    monkeypatch.setattr(builder, "_audio_duration_ms", fake)
    return calls


# ---------------------------------------------------------------- 组装

def test_build_timeline(project_dir, monkeypatch) -> None:
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})

    builder.build_timeline(project, proj)

    assert [c.asset_id for c in project.timeline.video] == ["img_s1", "img_s2"]
    assert [(c.start_ms, c.duration_ms) for c in project.timeline.video] == [(0, 2400), (2400, 1800)]
    assert [c.motion for c in project.timeline.video] == ["zoom_in_slow", "pan_right"]
    assert [(c.asset_id, c.offset_ms, c.duration_ms) for c in project.timeline.voiceover] == [
        ("vo_s1", 0, 2400), ("vo_s2", 2400, 1800),
    ]
    assert [s.start_ms for s in project.scenes] == [0, 2400]
    assert project.voiceover.duration_ms == 4200
    assert project.voiceover.status == "done"


def test_build_contiguous_no_gaps(project_dir, monkeypatch) -> None:
    """验收：总时长 = 配音总长，场景首尾相接无缝隙。"""
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})

    builder.build_timeline(project, proj)

    video = project.timeline.video
    for prev, cur in zip(video, video[1:]):
        assert prev.start_ms + prev.duration_ms == cur.start_ms
    total = video[-1].start_ms + video[-1].duration_ms
    assert total == project.voiceover.duration_ms == 4200


def test_build_uses_real_mp3_duration(project_dir) -> None:
    """不 monkeypatch：mutagen 读假 MP3 也得正时长（文件固定 → 值确定）。"""
    proj, project = project_dir
    builder.build_timeline(project, proj)

    total = project.voiceover.duration_ms
    assert total and total > 0
    assert total == sum(c.duration_ms for c in project.timeline.video)


def test_build_idempotent(project_dir, monkeypatch) -> None:
    """重复组装覆盖写，结果一致（失败重跑不叠加、不错位）。"""
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})

    builder.build_timeline(project, proj)
    first = project.model_dump(mode="json")
    builder.build_timeline(project, proj)
    assert project.model_dump(mode="json") == first


# ---------------------------------------------------------------- 错误路径

def test_build_no_scenes(tmp_path: Path) -> None:
    project = Project(project_id="proj_empty")
    with pytest.raises(FatalError, match="没有分镜"):
        builder.build_timeline(project, tmp_path)


def test_build_missing_vo_asset(project_dir, monkeypatch) -> None:
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.assets.pop("vo_s2")

    with pytest.raises(FatalError, match="缺少配音资产 vo_s2"):
        builder.build_timeline(project, proj)


def test_build_missing_vo_file(project_dir, monkeypatch) -> None:
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.assets["vo_s2"].path = "assets/vo_s2_missing.mp3"

    with pytest.raises(FatalError, match="配音文件缺失"):
        builder.build_timeline(project, proj)


def test_build_missing_img_asset(project_dir, monkeypatch) -> None:
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.assets.pop("img_s1")

    with pytest.raises(FatalError, match="缺少图资产 img_s1"):
        builder.build_timeline(project, proj)


def test_build_missing_img_file(project_dir, monkeypatch) -> None:
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.assets["img_s1"].path = "assets/img_s1_missing.png"

    with pytest.raises(FatalError, match="图文件缺失"):
        builder.build_timeline(project, proj)


# ---------------------------------------------------------------- 编排

def test_run_timeline_requires_gen_assets(store, tmp_path: Path) -> None:
    """gen_assets 未完成 → timeline 节点标 failed 并记录提示，不崩溃。"""
    project = _make_project()
    store.create(project)

    ok = run_timeline(store, project)

    assert ok is False
    assert project.pipeline["timeline"] == "failed"
    assert any("gen_assets" in e.error for e in project.errors)


def test_run_timeline_success_and_skip(store, tmp_path: Path, monkeypatch) -> None:
    project = _make_project()
    project.pipeline["gen_assets"] = "done"
    store.create(project)
    _make_assets(store.project_dir(project.project_id))
    calls = _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})

    assert run_timeline(store, project) is True
    assert project.pipeline["timeline"] == "done"
    assert len(calls) == 2

    # done 跳过：重跑不再读时长、不再组装
    assert run_timeline(store, project) is True
    assert len(calls) == 2


# ---------------------------------------------------------------- 导出平移

def test_export_subtitle_shift_to_global(project_dir, monkeypatch) -> None:
    """字幕存场景相对时间戳，导出按 scene.start_ms 平移到全局时间轴。"""
    proj, project = project_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    builder.build_timeline(project, proj)

    draft_dir = export(project, proj, proj / "exports", zip_archive=False)
    draft = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))

    text_segments = [t for t in draft["tracks"] if t["type"] == "text"][0]["segments"]
    texts = {m["id"]: json.loads(m["content"])["text"] for m in draft["materials"]["texts"]}
    starts = [(s["target_timerange"]["start"], texts[s["material_id"]]) for s in text_segments]
    assert starts == [
        (100_000, "AI 正在改变内容创作的方式。"),
        (2_500_000, "现在，一个人也能做视频。"),    # s2 起点 2400ms + 场景内 100ms
    ]
