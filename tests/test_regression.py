"""M3-4.4 风格模板回归：3 模板 × 3 条全链路（direct→gen_assets→timeline→export）。

mock 渠道（FakeDirector/FakeTTS/FakeImage）保证确定性 —— 9 条项目跑完整流水线，
断言导出草稿产物完整 + 模板接线（motion_hint 传入 director、字幕样式进草稿、BGM 轨）。
真实（live）链路按约定另跑 `pytest -m live` 验证；剪映打开成功率见 tests/opened_log.md。
"""

import base64
import json
import shutil

import pytest
from mutagen.mp3 import MP3

from app.core.pipeline import run_direct, run_export, run_gen_assets, run_timeline
from app.core.schema import Project, ProjectConfig, Scene
from app.core.styles import load_style
from app.tts.base import TTSResult, TTSWord
from app.vision.base import PNG_MAGIC, ImageProvider

FIXTURE_MP3 = __import__("pathlib").Path(__file__).parent / "fixtures" / "silence.mp3"
# 1x1 合法 PNG（VideoSegment 构造时会解析图片宽高，假 PNG 会报"image width 为空"）
MINI_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
TEXT = "今天聊聊 AI 视频。它能自动配音。还能自动配图。"
STYLE_IDS = ("fast_talk", "emotional", "explainer")


class FakeDirector:
    """固定分镜；记录收到的 motion_hint（验证模板接线）。"""

    def __init__(self):
        self.hints: list[str] = []

    def storyboard(self, text: str, motion_hint: str = "", *, brief=None, shot_size_hint=""):
        self.hints.append(motion_hint)
        return [
            Scene(scene_id="s1", narration="今天聊聊 AI 视频。", visual="v1", image_prompt="p1"),
            Scene(scene_id="s2", narration="它能自动配音。", visual="v2", image_prompt="p2"),
            Scene(scene_id="s3", narration="还能自动配图。", visual="v3", image_prompt="p3"),
        ], 0.003


class FakeTTS:
    """拷贝真实静音 mp3（mutagen/AudioMaterial 需要合法文件）。"""

    def __init__(self):
        self.calls = 0

    async def synth(self, text: str, out_mp3):
        self.calls += 1
        out_mp3 = __import__("pathlib").Path(out_mp3)
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE_MP3, out_mp3)
        words = [
            TTSWord(text=ch, start_ms=i * 100, end_ms=i * 100 + 90)
            for i, ch in enumerate(text)
        ]
        return TTSResult(mp3=out_mp3, words=words, duration_ms=round(MP3(FIXTURE_MP3).info.length * 1000))


class FakeImage(ImageProvider):
    cost_per_image = 0.02

    def __init__(self):
        super().__init__()
        self.calls = 0

    def _request_png(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        self.calls += 1
        return MINI_PNG


def _run_full_chain(store, project, director: FakeDirector) -> tuple[FakeTTS, FakeImage]:
    assert run_direct(store, project, director, TEXT) is True
    tts, image = FakeTTS(), FakeImage()
    assert run_gen_assets(store, project, tts, image) is True
    assert run_timeline(store, project) is True
    assert run_export(store, project, zip_archive=False) is True
    return tts, image


def _draft_dir(store, pid: str):
    return store.project_dir(pid) / "exports" / f"{pid}_draft"


@pytest.mark.parametrize("style", STYLE_IDS)
@pytest.mark.parametrize("i", range(3))
def test_style_full_chain_exports(store, style, i):
    """3 模板 × 3 条：全链路 done，导出草稿产物完整。"""
    pid = f"reg_{style}_{i}"
    project = Project(project_id=pid, config=ProjectConfig(style=style))
    store.create(project)

    director = FakeDirector()
    tts, image = _run_full_chain(store, project, director)

    # 模板接线：motion_hint 注入 director
    assert director.hints[-1] == load_style(style).motion_hint
    # 素材产物：3 配音 + 3 场景 × 3 候选图（M6-7.5）
    assert tts.calls == 3 and image.calls == 9

    draft = _draft_dir(store, pid)
    assert (draft / "draft_content.json").is_file()
    materials = list((draft / "materials").iterdir())
    assert len(materials) >= 6                     # 3 vo + 3 img
    content = json.loads((draft / "draft_content.json").read_text(encoding="utf-8"))
    assert "tracks" in content and "materials" in content
    assert (draft / "draft_meta_info.json").is_file()


def test_subtitle_style_reaches_draft(store):
    """字幕样式按模板参数化进草稿：字号与低位 y 都来自模板。"""
    pid = "reg_sub_style"
    project = Project(project_id=pid, config=ProjectConfig(style="explainer"))
    store.create(project)
    _run_full_chain(store, project, FakeDirector())

    content = json.loads((_draft_dir(store, pid) / "draft_content.json").read_text(encoding="utf-8"))
    text_materials = content["materials"]["texts"]
    assert text_materials, "草稿应有字幕素材"
    styles = json.loads(text_materials[0]["content"])["styles"]
    style = load_style("explainer")
    assert {s["size"] for s in styles} == {style.subtitle_style.size}      # 字号 7.0
    # 低位 y 来自模板（ClipSettings.transform_y）
    text_seg = next(
        t["segments"][0] for t in content["tracks"] if t["type"] == "text" and t["segments"]
    )
    assert text_seg["clip"]["transform"]["y"] == style.subtitle_style.y


def test_bgm_track_added_when_file_present(store):
    """情感向模板：放 assets/bgm.mp3 → 素材库多一条 BGM 音频素材。"""
    pid = "reg_bgm"
    project = Project(project_id=pid, config=ProjectConfig(style="emotional"))
    store.create(project)
    bgm_dir = store.project_dir(pid) / "assets"
    bgm_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_MP3, bgm_dir / "bgm.mp3")

    _run_full_chain(store, project, FakeDirector())

    content = json.loads((_draft_dir(store, pid) / "draft_content.json").read_text(encoding="utf-8"))
    audio_names = [a.get("path", "").replace("\\", "/").split("/")[-1] for a in content["materials"]["audios"]]
    assert len(audio_names) == 4                     # 3 配音 + 1 BGM
    assert any("bgm" in n for n in audio_names), f"素材库应有 BGM 素材: {audio_names}"


def test_bgm_missing_degrades_gracefully(store):
    """BGM 文件缺失：导出成功（降级跳过 BGM），不阻塞流水线。"""
    pid = "reg_no_bgm"
    project = Project(project_id=pid, config=ProjectConfig(style="emotional"))
    store.create(project)

    _run_full_chain(store, project, FakeDirector())

    content = json.loads((_draft_dir(store, pid) / "draft_content.json").read_text(encoding="utf-8"))
    audio_names = [a.get("path", "").replace("\\", "/").split("/")[-1] for a in content["materials"]["audios"]]
    assert len(audio_names) == 3                     # 只有 3 段配音，无 BGM
    assert not any("bgm" in n for n in audio_names), f"不应有 BGM 素材: {audio_names}"
