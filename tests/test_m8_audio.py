"""M8 音频测试：音效素材（sfx）引用 + BGM 卡点对齐（节拍检测）。

覆盖：
- app/audio/beats.py：合成节拍 wav 检测、静音、解码失败；
- edits：add_sfx / remove_sfx / select_scene_sfx / set_beat_sync / add_bgm 失效升级；
- timeline：音效轨组装 + 卡点向前 snap（只前移不重叠）+ 降级路径；
- export：sfx 轨（无淡入淡出）+ BGM 来源解耦（项目上传优先、模板兜底）；
- schema 0.4：sfx 引用校验 + 新字段默认值；
- web：策划页音效库/卡点开关 + 分镜页切点音效 selectbox。
"""

import json
import math
import struct
import wave
import zlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.audio.beats import BeatDetectionError, detect_beats
from app.core import edits
from app.core.project import ProjectStore
from app.core.schema import (
    PIPELINE_NODES,
    Asset,
    AudioClip,
    MotionPlan,
    Project,
    Scene,
    Subtitle,
    Timeline,
    VideoClip,
)
from app.core.state import FatalError
from app.export.jianying import export
from app.timeline import builder

# 极简 MP3：MPEG-1 Layer III 128kbps@44.1kHz 帧头 + 静音数据（同 test_timeline）。
# 单帧 417 字节 —— 帧长必须与头声明一致，mutagen/miniaudio 才能解析。
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413


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


def _make_beats_wav(path: Path, beat_seconds: list[float], total_s: float = 3.0) -> None:
    """合成「节拍 wav」：静音底 + 各节拍点 50ms 440Hz 正弦突发（44.1kHz mono 16-bit）。"""
    rate = 44100
    t = [i / rate for i in range(int(rate * total_s))]
    sig = [0.0] * len(t)
    for b in beat_seconds:
        i0 = int(b * rate)
        for i in range(i0, min(i0 + int(0.05 * rate), len(t))):
            sig[i] = 0.8 * math.sin(2 * math.pi * 440 * (i - i0) / rate)
    pcm = struct.pack(f"<{len(sig)}h", *[int(s * 32767) for s in sig])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def _make_tl_assets(root: Path) -> None:
    """root/assets 下放 2 场景素材（vo_*.mp3 + img_*.png）。"""
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "vo_s1.mp3").write_bytes(_MP3_FRAME * 20)
    (assets / "vo_s2.mp3").write_bytes(_MP3_FRAME * 20)
    _make_png(assets / "img_s1.png")
    _make_png(assets / "img_s2.png", rgb=(180, 90, 40))


def _make_tl_project() -> Project:
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
    _make_tl_assets(proj)
    return proj, _make_tl_project()


def _patch_durations(monkeypatch, durations: dict[str, int]) -> None:
    monkeypatch.setattr(builder, "_audio_duration_ms", lambda p: durations[p.name])


def _bind_sfx(proj: Path, project: Project, name: str = "whoosh", scene_id: str = "s1",
              frames: int = 10) -> str:
    """放音效素材文件 + 注册资产 + 绑定到场景（模拟 add_sfx + select_scene_sfx 产物）。"""
    sfx_dir = proj / "assets" / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    (sfx_dir / f"{name}.mp3").write_bytes(_MP3_FRAME * frames)
    sfx_id = f"sfx_{name}"
    project.assets[sfx_id] = Asset(type="audio", path=f"assets/sfx/{name}.mp3", status="done")
    for s in project.scenes:
        if s.scene_id == scene_id:
            s.sfx_asset_id = sfx_id
    return sfx_id


# ================================================================ 节拍检测

def test_detect_beats_synthetic_wav(tmp_path: Path) -> None:
    """合成 5 个节拍突发：全部检出，误差 < 60ms，升序。"""
    wav = tmp_path / "beats.wav"
    ground = [0.5, 1.0, 1.5, 2.0, 2.5]
    _make_beats_wav(wav, ground)

    beats = detect_beats(wav)

    assert len(beats) == len(ground)
    for got, want in zip(beats, ground):
        assert abs(got - want * 1000) < 60
    assert beats == sorted(beats)


def test_detect_beats_silence_returns_empty(tmp_path: Path) -> None:
    """全静音：无 onset 峰值 → 空列表（不把噪声当节拍）。"""
    wav = tmp_path / "silence.wav"
    _make_beats_wav(wav, [], total_s=1.0)

    assert detect_beats(wav) == []


def test_detect_beats_decode_error(tmp_path: Path) -> None:
    """非音频文件：解码失败 → BeatDetectionError（卡点降级信号）。"""
    bad = tmp_path / "not_audio.mp3"
    bad.write_bytes(b"this is not an mp3 at all")

    with pytest.raises(BeatDetectionError):
        detect_beats(bad)


# ================================================================ edits

def _edit_project(store: ProjectStore) -> Project:
    """素材链路已 done 的项目（2 场景，无资产引用）。"""
    project = Project(
        project_id="proj_edit",
        scenes=[Scene(scene_id="s1", narration="场景一。"), Scene(scene_id="s2", narration="场景二。")],
    )
    project.pipeline = {node: "done" for node in PIPELINE_NODES}
    store.create(project)
    return project


def test_add_sfx_registers_asset_and_invalidates(store, tmp_path) -> None:
    project = _edit_project(store)
    src = tmp_path / "whoosh.wav"
    src.write_bytes(b"wav-body")

    edits.add_sfx(store, project, src)

    dest = store.project_dir("proj_edit") / "assets" / "sfx" / "whoosh.wav"
    assert dest.read_bytes() == b"wav-body"
    loaded = store.load("proj_edit")
    asset = loaded.assets["sfx_whoosh"]
    assert (asset.type, asset.cost, asset.status) == ("audio", 0.0, "done")
    assert asset.path == "assets/sfx/whoosh.wav"
    assert loaded.pipeline["timeline"] == "pending"
    assert loaded.pipeline["export"] == "pending"
    assert loaded.pipeline["animate"] == "done"          # 素材上游不动


def test_add_sfx_rejects_unsupported_extension(store, tmp_path) -> None:
    project = _edit_project(store)
    src = tmp_path / "whoosh.txt"
    src.write_bytes(b"text")

    with pytest.raises(ValueError, match="mp3/wav"):
        edits.add_sfx(store, project, src)


def test_select_scene_sfx_bind_and_unbind(store) -> None:
    project = _edit_project(store)
    project.assets["sfx_whoosh"] = Asset(type="audio", path="assets/sfx/whoosh.mp3", status="done")
    store.save(project, message="补音效资产")

    edits.select_scene_sfx(store, project, "s1", "sfx_whoosh")
    loaded = store.load("proj_edit")
    assert loaded.scenes[0].sfx_asset_id == "sfx_whoosh"
    assert loaded.scenes[1].sfx_asset_id is None
    assert loaded.pipeline["timeline"] == "pending"

    edits.select_scene_sfx(store, loaded, "s1", None)     # 解绑
    loaded = store.load("proj_edit")
    assert loaded.scenes[0].sfx_asset_id is None
    assert loaded.pipeline["timeline"] == "pending"


def test_select_scene_sfx_rejects_non_audio(store) -> None:
    project = _edit_project(store)
    project.assets["img_x"] = Asset(type="image", path="assets/img_x.png", status="done")
    store.save(project, message="补图资产")

    with pytest.raises(ValueError, match="不是音频资产"):
        edits.select_scene_sfx(store, project, "s1", "img_x")


def test_select_scene_sfx_rejects_unknown(store) -> None:
    project = _edit_project(store)

    with pytest.raises(ValueError, match="音效资产不存在"):
        edits.select_scene_sfx(store, project, "s1", "sfx_ghost")


def test_remove_sfx_clears_refs_and_file(store) -> None:
    project = _edit_project(store)
    project.assets["sfx_whoosh"] = Asset(type="audio", path="assets/sfx/whoosh.mp3", status="done")
    project.scenes[0].sfx_asset_id = "sfx_whoosh"
    store.save(project, message="补音效")
    (store.project_dir("proj_edit") / "assets" / "sfx").mkdir(parents=True)
    (store.project_dir("proj_edit") / "assets" / "sfx" / "whoosh.mp3").write_bytes(b"mp3")

    edits.remove_sfx(store, project, "sfx_whoosh")

    loaded = store.load("proj_edit")
    assert "sfx_whoosh" not in loaded.assets
    assert all(s.sfx_asset_id is None for s in loaded.scenes)   # 引用清理 → 校验不炸
    assert not (store.project_dir("proj_edit") / "assets" / "sfx" / "whoosh.mp3").is_file()
    assert loaded.pipeline["timeline"] == "pending"


def test_remove_sfx_rejects_non_sfx_asset(store) -> None:
    project = _edit_project(store)
    project.assets["vo_s1"] = Asset(type="audio", path="assets/vo_s1.mp3", status="done")
    store.save(project, message="补配音")

    with pytest.raises(ValueError, match="音效资产不存在"):
        edits.remove_sfx(store, project, "vo_s1")


def test_set_beat_sync_toggles_and_invalidates(store) -> None:
    project = _edit_project(store)

    edits.set_beat_sync(store, project, True)
    loaded = store.load("proj_edit")
    assert loaded.config.beat_sync is True
    assert loaded.pipeline["timeline"] == "pending"

    edits.set_beat_sync(store, loaded, False)
    loaded = store.load("proj_edit")
    assert loaded.config.beat_sync is False


# ================================================================ timeline 音效轨

def test_build_sfx_track_places_clip_at_scene_start(tl_dir, monkeypatch) -> None:
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    _bind_sfx(proj, project, scene_id="s1")

    builder.build_timeline(project, proj)

    assert [(c.asset_id, c.offset_ms, c.duration_ms) for c in project.timeline.sfx] == [
        ("sfx_whoosh", 0, None),          # duration None → 导出取素材自身时长
    ]


def test_build_sfx_track_missing_file(tl_dir, monkeypatch) -> None:
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    _bind_sfx(proj, project)
    (proj / "assets" / "sfx" / "whoosh.mp3").unlink()

    with pytest.raises(FatalError, match="音效文件缺失"):
        builder.build_timeline(project, proj)


# ================================================================ timeline 卡点

def test_build_beat_sync_snaps_cut_forward(tl_dir, monkeypatch) -> None:
    """卡点：s2 自然起点 2400ms → snap 到 2500ms 节拍（向前顺延，配音不重叠）。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.config.beat_sync = True
    (proj / "assets" / "bgm.mp3").write_bytes(_MP3_FRAME * 100)
    monkeypatch.setattr(builder, "detect_beats", lambda p: [0, 1000, 2500, 4300, 5000])

    builder.build_timeline(project, proj)

    assert [s.start_ms for s in project.scenes] == [0, 2500]
    assert [(c.start_ms, c.duration_ms) for c in project.timeline.video] == [(0, 2400), (2500, 1800)]
    assert [(c.offset_ms, c.duration_ms) for c in project.timeline.voiceover] == [(0, 2400), (2500, 1800)]
    # 向前顺延产生 100ms 停顿：无重叠（下一段起点 ≥ 上一段终点）
    video = project.timeline.video
    assert video[1].start_ms >= video[0].start_ms + video[0].duration_ms
    assert project.voiceover.duration_ms == 4300


def test_build_beat_sync_no_nearby_beat_keeps_natural(tl_dir, monkeypatch) -> None:
    """窗口 [2400, 2800] 内无节拍 → 切点保持自然位置。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.config.beat_sync = True
    (proj / "assets" / "bgm.mp3").write_bytes(_MP3_FRAME * 100)
    monkeypatch.setattr(builder, "detect_beats", lambda p: [0, 1000, 9000])

    builder.build_timeline(project, proj)

    assert [s.start_ms for s in project.scenes] == [0, 2400]
    assert project.voiceover.duration_ms == 4200


def test_build_beat_sync_off_ignores_bgm(tl_dir, monkeypatch) -> None:
    """开关关闭：即使有 BGM 有节拍也不动切点（默认行为 = M7 及以前）。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    (proj / "assets" / "bgm.mp3").write_bytes(_MP3_FRAME * 100)
    monkeypatch.setattr(builder, "detect_beats", lambda p: [0, 2500])

    builder.build_timeline(project, proj)

    assert [s.start_ms for s in project.scenes] == [0, 2400]


def test_build_beat_sync_without_bgm_file_skips(tl_dir, monkeypatch) -> None:
    """开关开但 BGM 未上传：静默跳过卡点（BGM 属装饰，不报错）。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.config.beat_sync = True

    builder.build_timeline(project, proj)

    assert [s.start_ms for s in project.scenes] == [0, 2400]


def test_build_beat_sync_decode_failure_degrades(tl_dir, monkeypatch) -> None:
    """BGM 解码失败：降级不卡点，不阻塞时间线组装。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.config.beat_sync = True
    (proj / "assets" / "bgm.mp3").write_bytes(b"garbage not audio")

    builder.build_timeline(project, proj)

    assert [s.start_ms for s in project.scenes] == [0, 2400]


def test_build_beat_sync_snaps_after_tail(tl_dir, monkeypatch) -> None:
    """首尾帧尾拍参与边界：s1 尾拍后 s2 起点（2800ms）snap 到 2900ms 节拍。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.config.beat_sync = True
    (proj / "assets" / "bgm.mp3").write_bytes(_MP3_FRAME * 100)
    monkeypatch.setattr(builder, "detect_beats", lambda p: [0, 2900, 4700])
    # s1 设首尾帧：动画计划尾拍 400ms + 结束帧资产
    project.assets["img_s1_end"] = Asset(type="image", path="assets/img_s1_end.png", status="done")
    _make_png(proj / "assets" / "img_s1_end.png", rgb=(10, 200, 30))
    project.scenes[0].end_image_asset_id = "img_s1_end"
    project.scenes[0].motion_plan = MotionPlan(end_frame_ms=400)

    builder.build_timeline(project, proj)

    # s2 自然起点 = 2400 + 400 尾拍 = 2800 → snap 2900
    assert [s.start_ms for s in project.scenes] == [0, 2900]
    assert (project.timeline.video[1].start_ms, project.timeline.video[1].duration_ms) == (2400, 400)
    assert project.timeline.video[2].start_ms == 2900          # s2 主镜头（尾拍止于 2800）
    assert project.voiceover.duration_ms == 4700


def test_build_beat_sync_idempotent(tl_dir, monkeypatch) -> None:
    """卡点组装幂等：同节拍两次组装结果一致。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    project.config.beat_sync = True
    (proj / "assets" / "bgm.mp3").write_bytes(_MP3_FRAME * 100)
    monkeypatch.setattr(builder, "detect_beats", lambda p: [0, 2500, 4300])

    builder.build_timeline(project, proj)
    first = project.model_dump(mode="json")
    builder.build_timeline(project, proj)
    assert project.model_dump(mode="json") == first


# ================================================================ export

def _export_draft(proj: Path, project: Project) -> dict:
    draft_dir = export(project, proj, proj / "exports", zip_archive=False)
    return json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))


def test_export_sfx_track_no_fade(tl_dir, monkeypatch) -> None:
    """sfx 轨导出：独立音轨 + 切点定位 + 素材自身时长 + 无淡入淡出。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    _bind_sfx(proj, project, scene_id="s2")          # 音效在 s2 切点（2400ms）
    builder.build_timeline(project, proj)

    draft = _export_draft(proj, project)

    tracks = {t["name"]: t for t in draft["tracks"]}
    assert "sfx" in tracks
    seg = tracks["sfx"]["segments"][0]
    assert seg["target_timerange"]["start"] == 2_400_000     # s2 全局起点
    assert seg["target_timerange"]["duration"] > 0
    # 无淡入淡出：引用的额外材质（默认 1.0 倍速曲线）中不含 audio_fade
    fade_ids = {
        m["id"] for mats in draft["materials"].values() for m in mats
        if m.get("type") == "audio_fade"
    }
    assert not fade_ids & set(seg["extra_material_refs"])
    audio_names = [a.get("path", "").replace("\\", "/").split("/")[-1]
                   for a in draft["materials"]["audios"]]
    assert "whoosh.mp3" in audio_names


def test_export_bgm_uploaded_without_style_bgm(tl_dir, monkeypatch) -> None:
    """M8 解耦：default 模板（无 style.bgm）但项目上传了 bgm.mp3 → 仍加 BGM 轨。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"vo_s1.mp3": 2400, "vo_s2.mp3": 1800})
    (proj / "assets" / "bgm.mp3").write_bytes(_MP3_FRAME * 100)
    builder.build_timeline(project, proj)

    draft = _export_draft(proj, project)

    tracks = {t["name"]: t for t in draft["tracks"]}
    assert "bgm" in tracks
    seg = tracks["bgm"]["segments"][0]
    assert seg["target_timerange"]["start"] == 0
    assert seg["target_timerange"]["duration"] <= project.voiceover.duration_ms * 1000


def test_export_bgm_style_fallback_still_works(tmp_path: Path) -> None:
    """老约定兜底：无项目上传 BGM 时 style.bgm（emotional = assets/bgm.mp3）仍生效。"""
    proj = tmp_path / "proj"
    assets = proj / "assets"
    assets.mkdir(parents=True)
    _make_png(assets / "img_s1.png")
    (assets / "vo_s1.mp3").write_bytes(_MP3_FRAME * 20)
    (assets / "bgm.mp3").write_bytes(_MP3_FRAME * 50)
    project = Project(
        project_id="proj_style",
        config={"style": "emotional"},               # style.bgm = assets/bgm.mp3
        scenes=[Scene(scene_id="s1", image_asset_id="img_s1", start_ms=0, status="done")],
        timeline=Timeline(
            video=[VideoClip(asset_id="img_s1", start_ms=0, duration_ms=2000, motion="none")],
            voiceover=[AudioClip(asset_id="vo_s1", offset_ms=0, duration_ms=2000)],
        ),
        assets={
            "img_s1": Asset(type="image", path="assets/img_s1.png", status="done"),
            "vo_s1": Asset(type="audio", path="assets/vo_s1.mp3", model="edge-tts", status="done"),
        },
    )

    draft = _export_draft(proj, project)

    tracks = {t["name"]: t for t in draft["tracks"]}
    assert "bgm" in tracks


# ================================================================ schema 0.4

def test_schema_rejects_dangling_sfx_ref() -> None:
    """场景音效引用不存在的资产 → 构造即报错。"""
    with pytest.raises(ValidationError, match="音效引用"):
        Project(
            project_id="p",
            scenes=[Scene(scene_id="s1", sfx_asset_id="sfx_ghost")],
        )


def test_schema_rejects_timeline_sfx_non_audio() -> None:
    """音效轨引用图片资产 → 构造即报错。"""
    with pytest.raises(ValidationError, match="非 audio 资产"):
        Project(
            project_id="p",
            timeline=Timeline(sfx=[AudioClip(asset_id="img_x", offset_ms=0)]),
            assets={"img_x": Asset(type="image", path="assets/img_x.png", status="done")},
        )
