"""M3-4.1 断点续跑测试：kill -9 后重跑 0 次重复 API 调用。

模拟 kill -9 的手段：把 pipeline 节点状态置回 running（进程死在 fn 中途时的落盘状态，
原子写保证 project.json 合法）、删掉部分已落盘产物（模拟"还没来得及写"），
再重跑 gen_assets，断言：
- 配音 API（tts.synth）只对缓存未命中的场景调用；
- 生图 API（_request_png）只对缓存未命中的场景调用；
- narration 改动 → 对应配音缓存自动失效重新合成。
"""

from pathlib import Path

import pytest

from app.core.pipeline import run_gen_assets
from app.core.schema import Project, Scene
from app.tts.base import TTSResult, TTSWord
from app.vision.base import ImageProvider, PNG_MAGIC
from app.vision.cache import ImageCache, prompt_hash

TEXT_S1 = "AI 正在改变内容创作的方式。"
TEXT_S2 = "现在,用 AVPO 一个人也能做视频。"


class FakeTTS:
    """计数 TTSProvider：synth 调用数即重复 API 调用数。"""

    def __init__(self):
        self.synth_calls = 0

    async def synth(self, text: str, out_mp3: Path) -> TTSResult:
        self.synth_calls += 1
        out_mp3 = Path(out_mp3)
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        out_mp3.write_bytes(b"fake-mp3-bytes")
        words = [
            TTSWord(text=ch, start_ms=i * 100, end_ms=i * 100 + 90)
            for i, ch in enumerate(text)
        ]
        return TTSResult(mp3=out_mp3, words=words, duration_ms=len(text) * 100)


class FakeImage(ImageProvider):
    """计数生图渠道：_request_png 调用数即重复 API 调用数。"""

    cost_per_image = 0.02

    def __init__(self):
        super().__init__()
        self.api_calls = 0

    def _request_png(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        self.api_calls += 1
        return PNG_MAGIC + b"0" * 32


@pytest.fixture()
def project() -> Project:
    return Project(
        project_id="proj_resume",
        scenes=[
            Scene(scene_id="s1", narration=TEXT_S1, image_prompt="p1 cinematic"),
            Scene(scene_id="s2", narration=TEXT_S2, image_prompt="p2 desk"),
        ],
    )


def _rerun(store, project) -> tuple[FakeTTS, FakeImage]:
    """模拟 kill -9 后重开：重新 load，节点置回 running，重跑 gen_assets。"""
    project.pipeline["gen_assets"] = "running"      # kill -9 时落在 running
    store.save(project, message="kill -9 模拟")
    loaded = store.load(project.project_id)
    tts, image = FakeTTS(), FakeImage()
    ok = run_gen_assets(store, loaded, tts=tts, image=image)
    return ok, tts, image


def test_full_run_then_rerun_zero_api_calls(store, project):
    """完整跑一遍后 kill -9 重跑：0 次重复 API 调用，字幕/资产一致。"""
    ok, tts, image = _rerun(store, project)
    assert ok
    assert tts.synth_calls == 2                     # 第一次跑：2 场景都合成
    assert image.api_calls == 2                     # 第一次跑：2 张图都生成

    sub_count = len(store.load("proj_resume").subtitles)
    ok, tts, image = _rerun(store, project)
    assert ok
    assert tts.synth_calls == 0                     # kill -9 重跑：0 次重复配音
    assert image.api_calls == 0                     # kill -9 重跑：0 次重复生图
    assert len(store.load("proj_resume").subtitles) == sub_count


def test_killed_midway_voice_resumes_remaining(store, project):
    """kill -9 在配音中途（s1 完成、s2 未写）：只补 s2，s1 走缓存。"""
    ok, tts, image = _rerun(store, project)          # 完整跑一遍
    assert ok
    loaded = store.load("proj_resume")

    # 删掉 s2 配音产物（mp3 + sidecar）：模拟"进程死在写 s2 之前"
    for f in ("assets/vo_s2.mp3", "assets/vo_s2.json"):
        (store.project_dir("proj_resume") / f).unlink()
    loaded.pipeline["gen_assets"] = "running"
    store.save(loaded, message="kill 在 s2 配音前")

    ok, tts, image = _rerun(store, project)
    assert ok
    assert tts.synth_calls == 1                     # 只补 s2
    assert image.api_calls == 0                     # 两张图缓存都命中
    subs = store.load("proj_resume").subtitles
    assert any(s.scene_id == "s1" for s in subs) and any(s.scene_id == "s2" for s in subs)


def test_killed_midway_image_resumes_remaining(store, project):
    """kill -9 在生图中途（s2 图缓存未写）：只补 s2 图，配音 0 次重复。"""
    ok, tts, image = _rerun(store, project)
    assert ok
    loaded = store.load("proj_resume")
    project_dir = store.project_dir("proj_resume")

    # 删掉 s2 图缓存与资产条目：模拟"进程死在写 s2 图之前"
    h = prompt_hash("p2 desk", loaded.config.image.model, loaded.config.image.size)
    for f in (f".cache/{h}.png", f".cache/{h}.json"):
        (project_dir / f).unlink()
    loaded.assets.pop("img_s2")
    loaded.scenes[1].image_asset_id = None
    loaded.scenes[1].status = "pending"
    loaded.pipeline["gen_assets"] = "running"
    store.save(loaded, message="kill 在 s2 生图前")

    ok, tts, image = _rerun(store, project)
    assert ok
    assert tts.synth_calls == 0                     # 配音全部缓存命中
    assert image.api_calls == 1                     # 只补 s2 图
    loaded = store.load("proj_resume")
    assert loaded.scenes[1].image_asset_id == "img_s2"


def test_narration_change_invalidates_voice_cache(store, project):
    """改了口播文案：对应配音缓存失效重新合成，其他场景仍走缓存。"""
    ok, tts, image = _rerun(store, project)
    assert ok

    project.scenes[0].narration = "AI 正在彻底改变内容创作的方式。"   # 文案变了（_rerun 会连同 running 一起落盘）

    ok, tts, image = _rerun(store, project)
    assert ok
    assert tts.synth_calls == 1                     # 只重合成 s1（s2 缓存命中）
    assert image.api_calls == 0
    # 字幕整体重建，s1 字幕对应新文案（按新 words 聚合）
    subs = store.load("proj_resume").subtitles
    s1_texts = [s.text for s in subs if s.scene_id == "s1"]
    assert "".join(s1_texts).replace(" ", "") == "AI正在彻底改变内容创作的方式。"


def test_cache_survives_project_reload_without_running_state(store, project):
    """正常重跑（节点 failed 而非 running）：同样 0 次重复调用。"""
    ok, tts, image = _rerun(store, project)
    assert ok
    loaded = store.load("proj_resume")
    loaded.pipeline["gen_assets"] = "failed"        # 例如上次 failed，用户手动重试
    store.save(loaded, message="failed 重试模拟")

    ok, tts, image = _rerun(store, project)
    assert ok
    assert tts.synth_calls == 0
    assert image.api_calls == 0
