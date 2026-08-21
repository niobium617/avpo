"""M2-3.2 导出扩展测试（IMPLEMENTATION_PLAN 3.2）。

验收：字幕带统一样式（6 号/居中/黑描边/低位 y=-0.8）；配音段带 300ms 淡入淡出；
草稿元信息 draft_name / tm_duration 正确；封面由剪映取首帧（打开验证 3.2d）。
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

# 极简 MP3：MPEG-1 Layer III 128kbps@44.1kHz 单帧 417 字节（同 test_timeline）
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413


def _make_png(path: Path) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    w = h = 8
    raw = b"".join(b"\x00" + bytes((30, 60, 120)) * w for _ in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


@pytest.fixture()
def project_dir(tmp_path: Path) -> tuple[Path, Project]:
    """单场景项目：时间线已组装（2400ms），素材齐备。"""
    proj = tmp_path / "proj"
    assets = proj / "assets"
    assets.mkdir(parents=True)
    _make_png(assets / "img_s1.png")
    (assets / "vo_s1.mp3").write_bytes(_MP3_FRAME * 100)     # ≈2.6s > 声明 2400ms

    project = Project(
        project_id="proj_m2",
        title="AI 产品口播",
        scenes=[Scene(scene_id="s1", image_asset_id="img_s1", start_ms=0, status="done")],
        voiceover=Voiceover(asset_id="vo_s1", status="done", duration_ms=2400),
        subtitles=[Subtitle(scene_id="s1", start_ms=100, end_ms=2300, text="AI 正在改变内容创作的方式。")],
        timeline=Timeline(
            video=[VideoClip(asset_id="img_s1", start_ms=0, duration_ms=2400, motion="none")],
            voiceover=[AudioClip(asset_id="vo_s1", offset_ms=0, duration_ms=2400)],
        ),
        assets={
            "img_s1": Asset(type="image", path="assets/img_s1.png", status="done"),
            "vo_s1": Asset(type="audio", path="assets/vo_s1.mp3", model="edge-tts", status="done"),
        },
    )
    return proj, project


@pytest.fixture()
def draft(project_dir: tuple[Path, Project]) -> dict:
    proj, project = project_dir
    draft_dir = export(project, proj, proj / "exports", zip_archive=False)
    return json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))


def _find_material(draft: dict, type_: str) -> dict:
    for mats in draft["materials"].values():
        for m in mats:
            if m.get("type") == type_:
                return m
    raise AssertionError(f"materials 中没有 type={type_} 的素材")


# ---------------------------------------------------------------- 字幕样式

def test_subtitle_style_size_align_border(draft: dict) -> None:
    """字幕：6 号、居中（alignment=1）、黑描边。"""
    text_mat = _find_material(draft, "text")
    content = json.loads(text_mat["content"])
    style = content["styles"][0]
    assert style["size"] == 6.0
    assert text_mat["alignment"] == 1
    assert len(style["strokes"]) == 1
    stroke = style["strokes"][0]
    assert stroke["content"]["solid"]["color"] == [0.0, 0.0, 0.0]


def test_subtitle_position_low(draft: dict) -> None:
    """字幕片段 transform_y = -0.8（下移到画布下部）。"""
    text_seg = [t for t in draft["tracks"] if t["type"] == "text"][0]["segments"][0]
    assert text_seg["clip"]["transform"]["y"] == pytest.approx(-0.8)


# ---------------------------------------------------------------- 音频淡入淡出

def test_audio_fade_in_out(draft: dict) -> None:
    """每个配音段带 300ms 淡入淡出（微秒 300000）。"""
    audio_seg = [t for t in draft["tracks"] if t["type"] == "audio"][0]["segments"][0]
    fade = _find_material(draft, "audio_fade")
    assert fade["id"] in audio_seg["extra_material_refs"]
    assert fade["fade_in_duration"] == 300_000
    assert fade["fade_out_duration"] == 300_000


# ---------------------------------------------------------------- 草稿元信息

def test_meta_info_name_and_duration(project_dir: tuple[Path, Project]) -> None:
    """draft_meta_info.json：draft_name = 标题，tm_duration = 总时长（微秒）。"""
    proj, project = project_dir
    draft_dir = export(project, proj, proj / "exports", zip_archive=False)
    meta = json.loads((draft_dir / "draft_meta_info.json").read_text(encoding="utf-8"))

    assert meta["draft_name"] == "AI 产品口播"
    assert meta["tm_duration"] == 2_400_000
