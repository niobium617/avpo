"""M10 本地字幕测试：用户自带音频 → faster-whisper 转写 → 字幕上时间线。

覆盖：
- app/audio/whisper.py：哈希/缓存三键校验、模型目录（env 覆盖）、转写错误分类、
  懒加载单例（全部 mock，不打真模型）；
- schema 0.6：user_audio 引用校验 + whisper 配置字段；
- edits：add/remove_user_audio（失效语义：上传 transcribe 起、移除 gen_assets 起）
  + set_whisper_config；
- pipeline：transcribe 节点（跳过/转写/缓存命中/空结果/模型下载失败/文件缺失）
  + gen_assets 跳过自带音频场景并保留其字幕；
- timeline：自带音频替代 TTS 配音（时长=实际音频）+ 转写缓存必须有效；
- export：用户音频轨 + whisper 字幕走既有字幕通道（淡入淡出一致）。
"""

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.audio import whisper
from app.audio.whisper import (
    WhisperDecodeError,
    WhisperModelError,
    WhisperSegment,
    audio_sha256,
    load_whisper_cache,
    model_cache_dir,
    save_whisper_cache,
)
from app.core import edits
from app.core.pipeline import run_gen_assets, run_transcribe
from app.core.project import ProjectStore
from app.core.schema import (
    PIPELINE_NODES,
    Asset,
    AudioClip,
    Project,
    Scene,
    Subtitle,
    Timeline,
    VideoClip,
)
from app.core.state import FatalError
from app.export.jianying import export
from app.timeline import builder

# 极简 MP3：MPEG-1 Layer III 128kbps@44.1kHz 帧头 + 静音数据（同 test_m8_audio）。
# 单帧 417 字节 —— 帧长必须与头声明一致，mutagen/miniaudio 才能解析。
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


def _seed_whisper_cache(proj: Path, project: Project, scene_id: str,
                        segments: list[WhisperSegment] | None = None) -> Path:
    """按当前配置写有效转写缓存（模拟 transcribe 产物）。"""
    audio = proj / "assets" / "user_audio" / f"{scene_id}.mp3"
    return save_whisper_cache(
        proj / "assets", scene_id, audio_sha256(audio),
        project.config.whisper_model, project.config.whisper_language,
        segments if segments is not None else [WhisperSegment(0, 900, "第一句。")],
    )


# ================================================================ whisper 引擎

def test_audio_sha256_changes_with_content(tmp_path: Path) -> None:
    f = tmp_path / "a.mp3"
    f.write_bytes(b"content-1")
    h1 = audio_sha256(f)
    f.write_bytes(b"content-2")
    assert audio_sha256(f) != h1
    assert len(h1) == 16


def test_whisper_cache_roundtrip_and_key_mismatch(tmp_path: Path) -> None:
    segs = [WhisperSegment(0, 900, "第一句。")]
    save_whisper_cache(tmp_path, "s1", "hash1", "small", "zh", segs)

    assert load_whisper_cache(tmp_path, "s1", "hash1", "small", "zh") == segs
    # 三键任一不符 → 视为无缓存（重新转写）
    assert load_whisper_cache(tmp_path, "s1", "hash2", "small", "zh") is None
    assert load_whisper_cache(tmp_path, "s1", "hash1", "base", "zh") is None
    assert load_whisper_cache(tmp_path, "s1", "hash1", "small", "en") is None
    # 空段视为无缓存（转写成功后不允许空字幕落盘）
    save_whisper_cache(tmp_path, "s1", "hash1", "small", "zh", [])
    assert load_whisper_cache(tmp_path, "s1", "hash1", "small", "zh") is None


def test_whisper_cache_corrupt_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / "whisper_s1.json").write_text("{ 损坏", encoding="utf-8")
    assert load_whisper_cache(tmp_path, "s1", "h", "small", "zh") is None


def test_model_cache_dir_env_override(tmp_path: Path, monkeypatch) -> None:
    assert model_cache_dir(tmp_path / "data") == tmp_path / "data" / "whisper_models"
    monkeypatch.setenv("AVPO_WHISPER_CACHE", str(tmp_path / "custom"))
    assert model_cache_dir(tmp_path / "data") == tmp_path / "custom"


class _FakeModel:
    """假 WhisperModel：transcribe 返回 (segments, info)。"""

    def __init__(self, segments=None, fail: Exception | None = None):
        self._segments = segments or [type("S", (), {
            "start": 0.0, "end": 0.9, "text": " 第一句。 ",
        })]
        self._fail = fail

    def transcribe(self, path, language=None):
        if self._fail:
            raise self._fail
        return iter(self._segments), {}


def test_transcribe_audio_returns_ms_segments(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"mp3")
    model = _FakeModel()
    monkeypatch.setattr(whisper, "get_model", lambda m, root: model)

    segs = whisper.transcribe_audio(audio, "small", "zh", tmp_path / "models")

    assert segs == [WhisperSegment(start_ms=0, end_ms=900, text="第一句。")]


def test_transcribe_audio_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WhisperDecodeError, match="缺失"):
        whisper.transcribe_audio(tmp_path / "nope.mp3", "small", "zh", tmp_path)


def test_transcribe_audio_model_download_error(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"mp3")

    def boom(model, root):
        raise WhisperModelError("whisper 模型 small 下载失败: ConnectionError")

    monkeypatch.setattr(whisper, "get_model", boom)

    with pytest.raises(WhisperModelError, match="下载失败"):
        whisper.transcribe_audio(audio, "small", "zh", tmp_path)


def test_transcribe_audio_decode_error(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"mp3")
    monkeypatch.setattr(whisper, "get_model", lambda m, root: _FakeModel(fail=RuntimeError("bad audio")))

    with pytest.raises(WhisperDecodeError, match="转写失败"):
        whisper.transcribe_audio(audio, "small", "zh", tmp_path)


def test_get_model_lazy_singleton(monkeypatch, tmp_path: Path) -> None:
    """同一（模型, 目录）只加载一次；下载失败抛 WhisperModelError。"""
    loads: list[str] = []

    class FakeWhisperModel:
        def __init__(self, model_dir, **kw):
            loads.append(model_dir)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    monkeypatch.setattr("faster_whisper.utils.download_model", lambda m, cache_dir: f"dl/{m}")

    m1 = whisper.get_model("small", tmp_path)
    m2 = whisper.get_model("small", tmp_path)
    m3 = whisper.get_model("base", tmp_path)
    assert m1 is m2
    assert m1 is not m3
    assert loads == ["dl/small", "dl/base"]

    def dl_fail(m, cache_dir):
        raise ConnectionError("offline")

    monkeypatch.setattr("faster_whisper.utils.download_model", dl_fail)
    with pytest.raises(WhisperModelError):
        whisper.get_model("medium", tmp_path)


# ================================================================ schema 0.6

def test_schema_user_audio_defaults() -> None:
    project = Project(project_id="p", scenes=[Scene(scene_id="s1")])
    assert project.scenes[0].user_audio_asset_id is None
    assert project.config.whisper_model == "small"
    assert project.config.whisper_language == "zh"


def test_schema_rejects_dangling_user_audio_ref() -> None:
    with pytest.raises(ValidationError, match="自带音频引用"):
        Project(project_id="p", scenes=[Scene(scene_id="s1", user_audio_asset_id="ua_ghost")])


def test_schema_rejects_non_audio_user_audio_ref() -> None:
    with pytest.raises(ValidationError, match="非 audio 资产"):
        Project(
            project_id="p",
            scenes=[Scene(scene_id="s1", user_audio_asset_id="img_x")],
            assets={"img_x": Asset(type="image", path="assets/img_x.png", status="done")},
        )


def test_schema_rejects_bad_whisper_model() -> None:
    with pytest.raises(ValidationError):
        Project(project_id="p", config={"whisper_model": "huge"})


# ================================================================ edits

def _edit_project(store: ProjectStore) -> Project:
    """全节点 done 的项目（2 场景，无资产引用）。"""
    project = Project(
        project_id="proj_edit",
        scenes=[Scene(scene_id="s1", narration="场景一。"), Scene(scene_id="s2", narration="场景二。")],
    )
    project.pipeline = {node: "done" for node in PIPELINE_NODES}
    store.create(project)
    return project


def test_add_user_audio_registers_and_invalidates_transcribe(store, tmp_path) -> None:
    project = _edit_project(store)
    src = tmp_path / "rec.mp3"
    src.write_bytes(b"mp3-body")

    edits.add_user_audio(store, project, "s1", src)

    dest = store.project_dir("proj_edit") / "assets" / "user_audio" / "s1.mp3"
    assert dest.read_bytes() == b"mp3-body"
    loaded = store.load("proj_edit")
    assert loaded.scenes[0].user_audio_asset_id == "user_audio_s1"
    assert loaded.assets["user_audio_s1"].type == "audio"
    assert loaded.pipeline["transcribe"] == "pending"
    assert loaded.pipeline["gen_assets"] == "done"          # TTS 素材不动
    assert loaded.pipeline["timeline"] == "pending"


def test_add_user_audio_rejects_bad_input(store, tmp_path) -> None:
    project = _edit_project(store)
    with pytest.raises(ValueError, match="不存在"):
        edits.add_user_audio(store, project, "s1", tmp_path / "nope.mp3")
    txt = tmp_path / "rec.txt"
    txt.write_bytes(b"text")
    with pytest.raises(ValueError, match="mp3/wav/m4a"):
        edits.add_user_audio(store, project, "s1", txt)


def test_remove_user_audio_restores_tts_path(store, tmp_path) -> None:
    project = _edit_project(store)
    src = tmp_path / "rec.mp3"
    src.write_bytes(b"mp3-body")
    edits.add_user_audio(store, project, "s1", src)
    # 模拟时间线已组装（voiceover 引用自带音频资产）
    loaded = store.load("proj_edit")
    loaded.timeline.voiceover = [AudioClip(asset_id="user_audio_s1", offset_ms=0, duration_ms=900)]
    store.save(loaded, message="补时间线")
    # 转写缓存
    _seed_whisper_cache(store.project_dir("proj_edit"), loaded, "s1")

    edits.remove_user_audio(store, loaded, "s1")

    loaded = store.load("proj_edit")
    assert loaded.scenes[0].user_audio_asset_id is None
    assert "user_audio_s1" not in loaded.assets
    assert loaded.timeline.voiceover == []                 # 旧 clip 清理 → load 校验不炸
    assert not (store.project_dir("proj_edit") / "assets" / "user_audio" / "s1.mp3").is_file()
    assert not (store.project_dir("proj_edit") / "assets" / "whisper_s1.json").is_file()
    assert loaded.pipeline["gen_assets"] == "pending"      # TTS 配音/字幕重建
    assert loaded.pipeline["transcribe"] == "pending"


def test_remove_user_audio_rejects_without_audio(store) -> None:
    project = _edit_project(store)
    with pytest.raises(ValueError, match="没有自带音频"):
        edits.remove_user_audio(store, project, "s1")


def test_set_whisper_config_validates_and_invalidates(store) -> None:
    project = _edit_project(store)

    with pytest.raises(ValueError, match="非法 whisper 模型"):
        edits.set_whisper_config(store, project, "huge", "zh")

    edits.set_whisper_config(store, project, "base", "")
    loaded = store.load("proj_edit")
    assert loaded.config.whisper_model == "base"
    assert loaded.config.whisper_language == ""            # 空 = 自动检测
    assert loaded.pipeline["transcribe"] == "pending"
    assert loaded.pipeline["gen_assets"] == "done"


# ================================================================ pipeline transcribe 节点

def _transcribe_project(store: ProjectStore, user_audio: bool = True) -> Project:
    """2 场景项目；s1 可选自带音频（资产 + 文件）。transcribe 待运行（其余 done）。"""
    project = Project(
        project_id="proj_tsc",
        scenes=[Scene(scene_id="s1", narration="一。"), Scene(scene_id="s2", narration="二。")],
    )
    project.pipeline = {node: "done" for node in PIPELINE_NODES}
    project.pipeline["transcribe"] = "pending"            # 观察节点执行与下游失效
    if user_audio:
        project.scenes[0].user_audio_asset_id = "user_audio_s1"
        project.assets["user_audio_s1"] = Asset(
            type="audio", path="assets/user_audio/s1.mp3", status="done",
        )
    store.create(project)
    root = store.project_dir("proj_tsc")
    (root / "assets" / "user_audio").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "user_audio" / "s1.mp3").write_bytes(_MP3_FRAME * 20)
    return store.load("proj_tsc")


def _fake_transcribe(path, model, language, download_root):
    return [WhisperSegment(0, 900, "第一句。"), WhisperSegment(900, 1500, "第二句。")]


def test_run_transcribe_skips_when_no_user_audio(store, monkeypatch) -> None:
    project = _transcribe_project(store, user_audio=False)
    events: list = []
    monkeypatch.setattr(whisper, "transcribe_audio", _fake_transcribe)

    assert run_transcribe(store, project, progress=events.append) is True

    loaded = store.load("proj_tsc")
    assert loaded.pipeline["transcribe"] == "done"
    assert loaded.pipeline["timeline"] == "pending"       # 下游失效（无场景也一致）
    assert loaded.subtitles == []
    assert any("跳过" in e.message for e in events)


def test_run_transcribe_writes_subs_and_cache(store, monkeypatch) -> None:
    project = _transcribe_project(store)
    events: list = []
    monkeypatch.setattr(whisper, "transcribe_audio", _fake_transcribe)

    assert run_transcribe(store, project, progress=events.append) is True

    loaded = store.load("proj_tsc")
    assert [(s.scene_id, s.start_ms, s.end_ms, s.text) for s in loaded.subtitles] == [
        ("s1", 0, 900, "第一句。"),
        ("s1", 900, 1500, "第二句。"),
    ]
    cache = load_whisper_cache(
        store.project_dir("proj_tsc") / "assets", "s1",
        audio_sha256(store.project_dir("proj_tsc") / "assets" / "user_audio" / "s1.mp3"),
        "small", "zh",
    )
    assert cache == _fake_transcribe(None, None, None, None)
    assert loaded.pipeline["transcribe"] == "done"
    assert loaded.pipeline["timeline"] == "pending"
    assert loaded.pipeline["gen_assets"] == "done"        # 上游不动
    assert any("转写完成" in e.message for e in events)


def test_run_transcribe_cache_hit_skips_inference(store, monkeypatch) -> None:
    project = _transcribe_project(store)
    _seed_whisper_cache(store.project_dir("proj_tsc"), project, "s1")
    calls: list = []
    monkeypatch.setattr(whisper, "transcribe_audio", lambda *a: calls.append(a) or _fake_transcribe(*a))

    assert run_transcribe(store, project) is True

    assert calls == []                                     # 缓存命中 0 次推理
    loaded = store.load("proj_tsc")
    assert len(loaded.subtitles) == 1                      # 缓存里的 1 段


def test_run_transcribe_empty_result_fails(store, monkeypatch) -> None:
    project = _transcribe_project(store)
    monkeypatch.setattr(whisper, "transcribe_audio", lambda *a: [])

    assert run_transcribe(store, project) is False

    loaded = store.load("proj_tsc")
    assert loaded.pipeline["transcribe"] == "failed"
    assert any("未检出语音" in e.error for e in loaded.errors)
    assert not (store.project_dir("proj_tsc") / "assets" / "whisper_s1.json").is_file()


def test_run_transcribe_model_download_fails_transient(store, monkeypatch) -> None:
    project = _transcribe_project(store)
    monkeypatch.setattr(time, "sleep", lambda s: None)   # 跳过重试退避，加快测试

    def dl_fail(path, model, language, download_root):
        raise WhisperModelError("whisper 模型 small 下载失败: ConnectionError")

    monkeypatch.setattr(whisper, "transcribe_audio", dl_fail)

    assert run_transcribe(store, project) is False

    loaded = store.load("proj_tsc")
    assert loaded.pipeline["transcribe"] == "failed"
    assert any("网络" in e.hint for e in loaded.errors)    # 修复动作提示网络/镜像


def test_run_transcribe_missing_audio_file_fails(store) -> None:
    project = _transcribe_project(store)
    (store.project_dir("proj_tsc") / "assets" / "user_audio" / "s1.mp3").unlink()

    assert run_transcribe(store, project) is False

    loaded = store.load("proj_tsc")
    assert loaded.pipeline["transcribe"] == "failed"
    assert any("文件缺失" in e.error for e in loaded.errors)


class _FakeImage:
    """gen_assets 生图段用：仅需可挂 cache 属性（生图函数被 mock）。"""

    cache = None


def test_gen_assets_skips_user_audio_scene_and_preserves_subs(store, monkeypatch) -> None:
    """自带音频场景不跑 TTS、字幕保留；其余场景正常配音。"""
    project = _transcribe_project(store)
    voiced: list[str] = []

    def fake_voice(s, p, scene, tts):
        voiced.append(scene.scene_id)

    monkeypatch.setattr("app.core.pipeline._gen_scene_voice", fake_voice)
    monkeypatch.setattr("app.core.pipeline._gen_scene_candidates", lambda *a, **k: None)
    project.subtitles = [Subtitle(scene_id="s1", start_ms=0, end_ms=900, text="第一句。")]
    project.pipeline["gen_assets"] = "pending"            # 让节点真正执行

    assert run_gen_assets(store, project, object(), _FakeImage()) is True

    assert voiced == ["s2"]                                # s1 自带音频跳过 TTS
    loaded = store.load("proj_tsc")
    assert [s.text for s in loaded.subtitles] == ["第一句。"]   # whisper 字幕保留
    assert loaded.pipeline["gen_assets"] == "done"


# ================================================================ timeline

def _tl_project(user_audio: bool = True) -> Project:
    project = Project(
        project_id="proj_tl",
        scenes=[
            Scene(scene_id="s1", narration="一。", image_asset_id="img_s1", status="done"),
            Scene(scene_id="s2", narration="二。", image_asset_id="img_s2", status="done"),
        ],
        assets={
            "vo_s2": Asset(type="audio", path="assets/vo_s2.mp3", model="edge-tts", status="done"),
            "img_s1": Asset(type="image", path="assets/img_s1.png", status="done"),
            "img_s2": Asset(type="image", path="assets/img_s2.png", status="done"),
        },
    )
    if user_audio:
        project.scenes[0].user_audio_asset_id = "user_audio_s1"
        project.assets["user_audio_s1"] = Asset(
            type="audio", path="assets/user_audio/s1.mp3", status="done",
        )
    else:
        project.assets["vo_s1"] = Asset(type="audio", path="assets/vo_s1.mp3", model="edge-tts", status="done")
    return project


@pytest.fixture()
def tl_dir(tmp_path: Path) -> tuple[Path, Project]:
    proj = tmp_path / "proj"
    assets = proj / "assets"
    (assets / "user_audio").mkdir(parents=True)
    _make_png(assets / "img_s1.png")
    _make_png(assets / "img_s2.png", rgb=(180, 90, 40))
    (assets / "vo_s1.mp3").write_bytes(_MP3_FRAME * 20)
    (assets / "vo_s2.mp3").write_bytes(_MP3_FRAME * 20)
    (assets / "user_audio" / "s1.mp3").write_bytes(_MP3_FRAME * 30)
    return proj, _tl_project()


def _patch_durations(monkeypatch, durations: dict[str, int]) -> None:
    monkeypatch.setattr(builder, "_audio_duration_ms", lambda p: durations[p.name])


def test_build_user_audio_scene_uses_recording(tl_dir, monkeypatch) -> None:
    """自带音频场景：voiceover 用用户录音（时长=实际音频），TTS 场景不受影响。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"s1.mp3": 2400, "vo_s2.mp3": 1800})
    _seed_whisper_cache(proj, project, "s1")

    builder.build_timeline(project, proj)

    assert [(c.asset_id, c.offset_ms, c.duration_ms) for c in project.timeline.voiceover] == [
        ("user_audio_s1", 0, 2400),
        ("vo_s2", 2400, 1800),
    ]
    assert [s.start_ms for s in project.scenes] == [0, 2400]
    assert project.voiceover.duration_ms == 4200


def test_build_user_audio_requires_transcribe(tl_dir, monkeypatch) -> None:
    """未转写（无缓存）：FatalError 提示先跑 transcribe —— 字幕与录音不匹配宁可停下。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"s1.mp3": 2400, "vo_s2.mp3": 1800})

    with pytest.raises(FatalError, match="尚未转写"):
        builder.build_timeline(project, proj)


def test_build_user_audio_cache_mismatch_fails(tl_dir, monkeypatch) -> None:
    """音频更换/配置变了（缓存键不符）→ 同样 FatalError 提示转写。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"s1.mp3": 2400, "vo_s2.mp3": 1800})
    save_whisper_cache(                      # 换模型写缓存 → 与当前配置不符
        proj / "assets", "s1", audio_sha256(proj / "assets" / "user_audio" / "s1.mp3"),
        "base", "zh", [WhisperSegment(0, 900, "一。")],
    )

    with pytest.raises(FatalError, match="尚未转写"):
        builder.build_timeline(project, proj)


def test_build_user_audio_idempotent(tl_dir, monkeypatch) -> None:
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"s1.mp3": 2400, "vo_s2.mp3": 1800})
    _seed_whisper_cache(proj, project, "s1")

    builder.build_timeline(project, proj)
    first = project.model_dump(mode="json")
    builder.build_timeline(project, proj)
    assert project.model_dump(mode="json") == first


# ================================================================ export

def test_export_user_audio_voiceover_and_whisper_subs(tl_dir, monkeypatch) -> None:
    """导出：a1 轨用用户录音（淡入淡出一致）+ 字幕轨 = whisper 字幕按场景平移。"""
    proj, project = tl_dir
    _patch_durations(monkeypatch, {"s1.mp3": 2400, "vo_s2.mp3": 1800})
    _seed_whisper_cache(proj, project, "s1")
    project.subtitles = [
        Subtitle(scene_id="s1", start_ms=0, end_ms=900, text="第一句。"),
        Subtitle(scene_id="s1", start_ms=900, end_ms=1500, text="第二句。"),
    ]
    builder.build_timeline(project, proj)

    draft_dir = export(project, proj, proj / "exports", zip_archive=False)
    draft = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))

    tracks = {t["name"]: t for t in draft["tracks"]}
    vo_seg = tracks["a1"]["segments"][0]
    audio_names = [a.get("path", "").replace("\\", "/").split("/")[-1]
                   for a in draft["materials"]["audios"]]
    assert "s1.mp3" in audio_names                        # 用户录音进草稿素材
    assert vo_seg["target_timerange"]["start"] == 0
    # 时长语义与 TTS 配音一致：claim 2400ms，超出素材真实时长时截断
    from pyJianYingDraft import AudioMaterial
    real_us = AudioMaterial(str(proj / "assets" / "user_audio" / "s1.mp3")).duration
    assert vo_seg["target_timerange"]["duration"] == min(2_400_000, real_us)
    # 淡入淡出 300ms 与 TTS 配音一致（audio_fade 在 extra_material_refs 中）
    fade_ids = {
        m["id"] for mats in draft["materials"].values() for m in mats
        if m.get("type") == "audio_fade"
    }
    assert fade_ids & set(vo_seg["extra_material_refs"])

    sub_segs = tracks["sub"]["segments"]
    texts = [
        json.loads(m["content"]).get("text")
        for mats in draft["materials"].values() for m in mats
        if m.get("type") == "text"
    ]
    assert texts == ["第一句。", "第二句。"]                   # 字幕轨 = whisper 字幕
    assert sub_segs[0]["target_timerange"]["start"] == 0  # s1 起点 0（场景内相对时间）
    assert sub_segs[1]["target_timerange"]["start"] == 900_000
