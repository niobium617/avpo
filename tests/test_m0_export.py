"""M0-1.6 golden 测试：project.json → 剪映草稿目录，断言结构与字段。

素材为测试内生成的最小合法文件（PNG 纯 Python 生成、MP3 单帧），完全离线。
验收标准（IMPLEMENTATION_PLAN 1.6）：目录结构正确 + 三轨 + 素材自包含。
"""

import json
import struct
import zlib
from pathlib import Path

import pytest

from app.core.schema import (
    Asset,
    AudioClip,
    Project,
    Scene,
    Subtitle,
    Timeline,
    VideoClip,
    Voiceover,
)
from app.export.jianying import export

# 极简 MP3：MPEG-1 Layer III 帧头 + 静音数据（128kbps @ 44100Hz，单帧 ~417B）
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 400
_MP3_FRAMES = 20          # ≈ 480ms，够配音段用
_MP3_DURATION_US = 480_000


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


@pytest.fixture()
def project_dir(tmp_path: Path) -> tuple[Path, Project]:
    """项目目录：assets/ 拷入 2 图 + 1 配音，返回 (项目根, 合法 Project)。"""
    proj = tmp_path / "proj"
    assets = proj / "assets"
    assets.mkdir(parents=True)

    _make_png(assets / "s1_v1.png", rgb=(30, 60, 120))
    _make_png(assets / "s2_v1.png", rgb=(180, 90, 40))
    (assets / "vo_v1.mp3").write_bytes(_MP3_FRAME * _MP3_FRAMES)

    project = Project(
        project_id="proj_001",
        title="AI 产品口播",
        scenes=[
            Scene(
                scene_id="s1", narration="AI 正在改变内容创作的方式。",
                visual="都市夜景, 数字光效, 电影感", image_prompt="cinematic city night",
                image_asset_id="a_img_1", motion="zoom_in_slow", status="done",
            ),
            Scene(
                scene_id="s2", narration="现在,用 AVPO 一个人也能做视频。",
                visual="一人对着电脑, 屏幕发光", image_prompt="one person at desk",
                image_asset_id="a_img_2", motion="pan_left", status="done",
            ),
        ],
        voiceover=Voiceover(asset_id="a_vo", status="done", duration_ms=480),
        subtitles=[
            Subtitle(scene_id="s1", start_ms=0, end_ms=200, text="AI 正在改变"),
            Subtitle(scene_id="s1", start_ms=200, end_ms=480, text="内容创作的方式"),
        ],
        timeline=Timeline(
            video=[
                VideoClip(asset_id="a_img_1", start_ms=0, duration_ms=200, motion="zoom_in_slow"),
                VideoClip(asset_id="a_img_2", start_ms=200, duration_ms=280, motion="pan_left"),
            ],
            voiceover=[AudioClip(asset_id="a_vo", offset_ms=0)],
        ),
        assets={
            "a_img_1": Asset(type="image", path="assets/s1_v1.png", model="FLUX.1-schnell", seed=1234, cost=0.02, status="done"),
            "a_img_2": Asset(type="image", path="assets/s2_v1.png", model="FLUX.1-schnell", seed=5678, cost=0.02, status="done"),
            "a_vo": Asset(type="audio", path="assets/vo_v1.mp3", model="edge-tts", status="done"),
        },
    )
    return proj, project


def _load_draft(draft_dir: Path) -> dict:
    return json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 结构

def test_export_directory_structure(project_dir: tuple[Path, Project]) -> None:
    proj, project = project_dir
    draft = export(project, proj, proj / "exports")

    assert draft.name == "proj_001_draft"
    assert (draft / "draft_content.json").is_file()
    assert (draft / "draft_meta_info.json").is_file()
    # 素材全部拷入草稿 materials/
    assert (draft / "materials" / "s1_v1.png").is_file()
    assert (draft / "materials" / "s2_v1.png").is_file()
    assert (draft / "materials" / "vo_v1.mp3").is_file()


def test_export_zip_archive(project_dir: tuple[Path, Project]) -> None:
    proj, project = project_dir
    export(project, proj, proj / "exports")
    zip_path = proj / "exports" / "proj_001_draft.zip"
    assert zip_path.is_file()
    assert zip_path.stat().st_size > 0


# ---------------------------------------------------------------- JSON 字段

def test_export_three_tracks(project_dir: tuple[Path, Project]) -> None:
    proj, project = project_dir
    draft = _load_draft(export(project, proj, proj / "exports"))

    tracks = {t["type"]: t for t in draft["tracks"]}
    assert set(tracks) == {"video", "audio", "text"}
    assert len(tracks["video"]["segments"]) == 2
    assert len(tracks["audio"]["segments"]) == 1
    assert len(tracks["text"]["segments"]) == 2


def test_export_duration_from_voiceover(project_dir: tuple[Path, Project]) -> None:
    proj, project = project_dir
    draft = _load_draft(export(project, proj, proj / "exports"))

    assert draft["duration"] == _MP3_DURATION_US


def test_export_self_contained_materials(project_dir: tuple[Path, Project]) -> None:
    """素材引用必须指向草稿目录内部 —— 草稿自包含、zip 可迁移。"""
    proj, project = project_dir
    draft_dir = export(project, proj, proj / "exports")
    draft = _load_draft(draft_dir)

    for key in ("videos", "audios"):
        for material in draft["materials"][key]:
            path = Path(material["path"])
            assert path.is_absolute()
            assert draft_dir in path.parents, f"素材未拷入草稿目录: {path}"


def test_export_motion_mapping(project_dir: tuple[Path, Project]) -> None:
    """zoom_in_slow 段有动画引用；pan_left（暂无映射）不加动画。"""
    proj, project = project_dir
    draft = _load_draft(export(project, proj, proj / "exports"))

    video_segments = [t for t in draft["tracks"] if t["type"] == "video"][0]["segments"]
    zoom_seg, pan_seg = video_segments
    anim_ids = {m["id"] for m in draft["materials"]["material_animations"]}
    assert zoom_seg["target_timerange"]["start"] == 0
    assert anim_ids & set(zoom_seg["extra_material_refs"]), "zoom 段应引用动画素材"
    assert not (anim_ids & set(pan_seg["extra_material_refs"])), "pan 段不应有动画"


def test_export_timerange_conversion(project_dir: tuple[Path, Project]) -> None:
    """project.json 毫秒 → 草稿微秒（×1000）。"""
    proj, project = project_dir
    draft = _load_draft(export(project, proj, proj / "exports"))

    video_segments = [t for t in draft["tracks"] if t["type"] == "video"][0]["segments"]
    assert video_segments[0]["target_timerange"] == {"start": 0, "duration": 200_000}
    assert video_segments[1]["target_timerange"] == {"start": 200_000, "duration": 280_000}


# ---------------------------------------------------------------- 错误路径

def test_export_missing_asset(project_dir: tuple[Path, Project]) -> None:
    proj, project = project_dir
    project.assets["a_vo"].path = "assets/vo_missing.mp3"
    with pytest.raises(FileNotFoundError, match="素材缺失"):
        export(project, proj, proj / "exports")


def test_export_wrong_asset_type(project_dir: tuple[Path, Project]) -> None:
    proj, project = project_dir
    project.assets["a_vo"].type = "image"      # 音频轨引用图片素材
    with pytest.raises(ValueError, match="音频轨素材类型不符"):
        export(project, proj, proj / "exports")


# ---------------------------------------------------------------- 幂等

def test_export_idempotent(project_dir: tuple[Path, Project]) -> None:
    """重复导出（覆盖旧草稿）不报错，且结果一致。"""
    proj, project = project_dir
    first = export(project, proj, proj / "exports")
    second = export(project, proj, proj / "exports")

    assert _load_draft(first)["duration"] == _load_draft(second)["duration"]
    assert (second / "draft_content.json").is_file()
