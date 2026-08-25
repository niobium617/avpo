"""M4-5.1 验收：update_scenes 分镜修改落盘 + run_direct/run_gen_assets 进度回调。

M6-7.6：update_scenes 收敛到 app/core/edits.py（文案守卫放宽为可改）。
"""

import pytest

from app.core.edits import update_scenes
from app.core.pipeline import run_direct, run_gen_assets
from app.core.progress import ProgressEvent
from app.core.schema import PIPELINE_NODES, Project, Scene
from app.tts.base import TTSResult, TTSWord
from app.vision.base import PNG_MAGIC, ImageProvider


class _FakeDirector:
    """假导演：把文案原样做成 1 个场景（校验 narration 拼接=原文可通过）。"""

    def storyboard(self, text, motion_hint="", *, brief=None, shot_size_hint=""):
        return [
            Scene(scene_id="s1", narration=text, visual="旧画面", image_prompt="old prompt", motion="none")
        ], 0.001


class _FakeTTS:
    """假 TTS：逐字 100ms（与 test_m1_assets.FakeTTS 同款，不跨文件 import 保持独立）。"""

    async def synth(self, text, out_mp3):
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        out_mp3.write_bytes(b"fake-mp3")
        words = [TTSWord(text=ch, start_ms=i * 100, end_ms=i * 100 + 80) for i, ch in enumerate(text)]
        return TTSResult(mp3=out_mp3, words=words, duration_ms=len(text) * 100)


class _FakeImage(ImageProvider):
    """假生图：直接返回 PNG 魔数 + 占位字节。"""

    cost_per_image = 0.02

    def _request_png(self, prompt, model, pixel_size, seed):
        return PNG_MAGIC + b"fake"


# ---------------------------------------------------------------- update_scenes

def test_update_scenes_persists_edits(store, sample_project):
    store.create(sample_project)
    scenes = [s.model_copy(deep=True) for s in sample_project.scenes]
    scenes[0].visual = "新画面"
    scenes[0].image_prompt = "new prompt"
    scenes[0].motion = "pan_right"

    update_scenes(store, sample_project, scenes)

    loaded = store.load("proj_001")
    assert loaded.scenes[0].visual == "新画面"
    assert loaded.scenes[0].image_prompt == "new prompt"
    assert loaded.scenes[0].motion == "pan_right"
    assert loaded.scenes[1] == scenes[1]            # 未编辑场景原样保留


def test_update_scenes_invalidates_downstream(store, sample_project):
    store.create(sample_project)
    sample_project.pipeline = {node: "done" for node in PIPELINE_NODES}
    store.save(sample_project)
    scenes = [s.model_copy(deep=True) for s in sample_project.scenes]

    update_scenes(store, sample_project, scenes)

    loaded = store.load("proj_001")
    assert loaded.pipeline["direct"] == "done"      # 上游不变
    for node in ("confirm", "gen_assets", "timeline", "export"):
        assert loaded.pipeline[node] == "pending"   # 下游全部重跑


def test_update_scenes_allows_narration_change(store, sample_project):
    """M6-7.6 文案守卫放宽：文案可改（TTS sidecar 按 narration_hash 自动失效重合成）。"""
    store.create(sample_project)
    scenes = [s.model_copy(deep=True) for s in sample_project.scenes]
    scenes[0].narration = "改了文案。"

    update_scenes(store, sample_project, scenes)

    assert store.load("proj_001").scenes[0].narration == "改了文案。"


def test_update_scenes_rejects_duplicate_scene_id(store, sample_project):
    store.create(sample_project)
    scenes = [s.model_copy(deep=True) for s in sample_project.scenes]
    scenes[1].scene_id = "s1"                       # 触发 Project 引用完整性校验
    with pytest.raises(ValueError, match="scene_id"):
        update_scenes(store, sample_project, scenes)


# ---------------------------------------------------------------- 进度回调（M4 引入，M5 结构化）

def test_run_direct_progress_callback(store):
    project = Project(project_id="proj_p", title="进度回调")
    store.create(project)
    events: list[ProgressEvent] = []

    assert run_direct(store, project, _FakeDirector(), "你好。", progress=events.append) is True

    assert [(e.node, e.message, e.percent) for e in events] == [
        ("direct", "调用 LLM 生成分镜…", 0.0),
        ("direct", "分镜生成完成（1 场景）", 1.0),
    ]


def test_run_gen_assets_progress_callback(store):
    project = Project(project_id="proj_p", title="进度回调", scenes=[
        Scene(scene_id="s1", narration="你好。", visual="v1", image_prompt="p1"),
        Scene(scene_id="s2", narration="世界。", visual="v2", image_prompt="p2"),
    ])
    store.create(project)
    events: list[ProgressEvent] = []

    assert run_gen_assets(store, project, _FakeTTS(), _FakeImage(), progress=events.append) is True

    assert [(e.node, e.message, e.percent) for e in events[:2]] == [
        ("gen_assets", "配音+字幕 1/2（s1）", pytest.approx(0.225)),
        ("gen_assets", "配音+字幕 2/2（s2）", pytest.approx(0.45)),
    ]
    # 生图段候选级粒度：2 场景 × 3 候选 = 6 张，并发完成顺序不定：按消息排序后逐项断 percent
    image_events = sorted(events[2:], key=lambda e: e.message)
    assert [e.message for e in image_events] == [f"生图 {k}/6" for k in range(1, 7)]
    assert image_events[0].percent == pytest.approx(0.45 + 0.55 / 6)
    assert image_events[-1].percent == pytest.approx(1.0)


def test_run_gen_assets_percent_monotonic_and_bounded(store):
    """percent 分段契约：配音段 0~0.45 递增，生图段 0.45~1.0 递增，全部有界。"""
    project = Project(project_id="proj_p", title="percent 契约", scenes=[
        Scene(scene_id=f"s{i}", narration=f"第{i}句。", visual="v", image_prompt="p")
        for i in range(1, 4)
    ])
    store.create(project)
    events: list[ProgressEvent] = []

    assert run_gen_assets(store, project, _FakeTTS(), _FakeImage(), progress=events.append) is True

    assert all(e.percent is not None and 0.0 <= e.percent <= 1.0 for e in events)
    voice = [e.percent for e in events if e.node == "gen_assets" and e.message.startswith("配音")]
    image = [e.percent for e in events if e.node == "gen_assets" and e.message.startswith("生图")]
    assert voice == sorted(voice) and len(set(voice)) == 3     # 严格递增
    assert image == sorted(image) and len(set(image)) == 9     # 3 场景 × 3 候选 = 9 张
    assert max(voice) < min(image)                             # 两阶段不重叠
    assert voice[-1] == pytest.approx(0.45)
    assert image[-1] == pytest.approx(1.0)


def test_run_gen_assets_no_progress_still_works(store):
    """默认 progress=None 向后兼容：不传回调跑通全链。"""
    project = Project(project_id="proj_p", title="无回调", scenes=[
        Scene(scene_id="s1", narration="你好。", visual="v", image_prompt="p"),
    ])
    store.create(project)

    assert run_gen_assets(store, project, _FakeTTS(), _FakeImage()) is True
    assert project.pipeline["gen_assets"] == "done"
