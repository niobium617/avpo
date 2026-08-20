"""M1-2.8 素材链路测试。

- mock 回归：假 Director / 假 TTS / 假生图 API 跑通 direct → gen_assets 全链，
  断言 project.json 完整、产物落盘、缓存生效、状态树正确。
- 1 条真实全链路（@live）：真实 DeepSeek + edge-tts + FLUX，需 .env 密钥与网络，
  默认跳过，用 `pytest -m live` 显式运行。
"""

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest
from dotenv import load_dotenv

from app.core.pipeline import run_direct, run_gen_assets
from app.core.project import ProjectStore
from app.core.schema import Project
from app.director.director import Director
from app.tts.base import TTSResult, TTSWord
from app.tts.tts_edge import EdgeTTS
from app.vision.flux import COST_PER_IMAGE, PNG_MAGIC, FluxImage

TEXT = "AI 正在改变内容创作的方式。现在，一个人也能做视频。"
VALID_SCENES = json.dumps(
    {"scenes": [
        {
            "scene_id": "s1",
            "narration": "AI 正在改变内容创作的方式。",
            "visual": "都市夜景，数字光效",
            "image_prompt": "cinematic city night, digital light",
            "motion": "zoom_in_slow",
        },
        {
            "scene_id": "s2",
            "narration": "现在，一个人也能做视频。",
            "visual": "一人对着电脑，屏幕发光",
            "image_prompt": "one person at desk, glowing screen",
            "motion": "pan_left",
        },
    ]},
    ensure_ascii=False,
)
FAKE_PNG = PNG_MAGIC + b"fake"


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


# ---------------------------------------------------------------- 外部 API 假件

class FakeTTS:
    """假 TTS：逐字 100ms，词级时间戳连续。"""

    def __init__(self):
        self.calls: list[str] = []

    async def synth(self, text: str, out_mp3: Path) -> TTSResult:
        self.calls.append(text)
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        out_mp3.write_bytes(b"fake-mp3")
        words = [
            TTSWord(text=ch, start_ms=i * 100, end_ms=i * 100 + 80)
            for i, ch in enumerate(text)
        ]
        return TTSResult(mp3=out_mp3, words=words, duration_ms=len(text) * 100)


class _Resp:
    def __init__(self, url: str):
        self.data = [_Url(url)]


class _Url:
    def __init__(self, url: str):
        self.url = url


class _FakeImages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("生图 API 调用次数超过预期")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _ChatResp:
    def __init__(self, content: str):
        self.choices = [type("_C", (), {"message": type("_M", (), {"content": content})()})()]
        self.usage = type("_U", (), {"prompt_tokens": 100, "completion_tokens": 50})()


def _fake_urlopen(png: bytes):
    class _R:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._data

    return lambda url, timeout=None: _R(png)


def _make_fakes(monkeypatch, n_images: int):
    """组装假 Director + FluxImage（共享 urlopen 假件）。"""
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(FAKE_PNG))

    director = Director(api_key="test-key")
    chat = _FakeCompletions([_ChatResp(VALID_SCENES)])
    director.client = type("_C", (), {"chat": type("_Ch", (), {"completions": chat})()})()

    image = FluxImage(api_key="test-key")
    images = _FakeImages([_Resp("https://example.com/x.png")] * n_images)
    image.client = type("_C", (), {"images": images})()
    return director, chat, image, images


@pytest.fixture()
def chain_project(store) -> Project:
    project = Project(project_id="proj_chain", title="链路测试")
    store.create(project)
    return project


def test_full_chain_mocked(store, chain_project, monkeypatch):
    director, chat, image, images = _make_fakes(monkeypatch, n_images=2)
    tts = FakeTTS()

    # direct：文案 → 分镜
    assert run_direct(store, chain_project, director, TEXT) is True
    assert chain_project.pipeline["direct"] == "done"
    assert len(chain_project.scenes) == 2
    assert len(chat.calls) == 1

    # gen_assets：配音 + 字幕 + 3 图 → 归档
    assert run_gen_assets(store, chain_project, tts, image) is True
    assert chain_project.pipeline["gen_assets"] == "done"

    # ---- 落盘后重新加载，断言 JSON 完整（退出条件）----
    loaded = store.load("proj_chain")
    assert set(loaded.assets) == {"vo_s1", "vo_s2", "img_s1", "img_s2"}
    assert all(a.status == "done" for a in loaded.assets.values())
    for a in loaded.assets.values():                    # 产物真实存在
        assert (store.project_dir("proj_chain") / a.path).is_file()

    # 场景完成、图资产关联、成本记录
    for s in loaded.scenes:
        assert s.status == "done"
        assert s.image_asset_id == f"img_{s.scene_id}"
        assert s.cost["image"] == COST_PER_IMAGE
        assert s.cost["llm"] > 0
    assert loaded.assets["img_s1"].seed is not None
    assert loaded.assets["img_s1"].prompt_hash

    # 字幕覆盖全文（与配音同源）
    assert norm("".join(sub.text for sub in loaded.subtitles)) == norm(TEXT)
    assert all(sub.scene_id in {"s1", "s2"} for sub in loaded.subtitles)

    # 配音 mp3 每场景一条、TTS 被调两次
    assert tts.calls == ["AI 正在改变内容创作的方式。", "现在，一个人也能做视频。"]
    assert len(images.calls) == 2


def test_gen_assets_rerun_hits_image_cache(store, chain_project, monkeypatch):
    """二次生成 0 次生图 API 调用（2.5 验收在链路层复验）。"""
    director, _, image, images = _make_fakes(monkeypatch, n_images=2)
    tts = FakeTTS()

    run_direct(store, chain_project, director, TEXT)
    assert run_gen_assets(store, chain_project, tts, image) is True
    assert len(images.calls) == 2

    # 状态机 done 跳过 —— 重跑直接返回，不调任何 API
    assert run_gen_assets(store, chain_project, tts, image) is True
    assert len(images.calls) == 2
    assert len(tts.calls) == 2

    # 即使把节点重置为 pending 重跑，生图仍命中缓存（0 新 API 调用）
    chain_project.pipeline["gen_assets"] = "pending"
    store.save(chain_project)
    assert run_gen_assets(store, chain_project, tts, image) is True
    assert len(images.calls) == 2                        # 未新增生图调用
    assert len(tts.calls) == 4                           # TTS 无缓存，重新合成


def test_chain_survives_retry_no_duplicate_subtitles(store, chain_project, monkeypatch):
    """fn 可重入：第一次跑到一半抛 TransientError，重试后字幕不叠加。"""
    director, _, image, images = _make_fakes(monkeypatch, n_images=2)
    tts = FakeTTS()

    calls = {"n": 0}
    real_synth = tts.synth

    async def flaky_synth(text, out_mp3):
        calls["n"] += 1
        if calls["n"] <= 2:                              # s1 配音段失败两次，第 3 次成功
            raise TimeoutError("网络抖动")
        return await real_synth(text, out_mp3)

    tts.synth = flaky_synth
    monkeypatch.setattr("app.core.state.time.sleep", lambda s: None)

    run_direct(store, chain_project, director, TEXT)
    assert run_gen_assets(store, chain_project, tts, image) is True

    loaded = store.load("proj_chain")
    assert loaded.pipeline["gen_assets"] == "done"
    # 字幕整体重建：s1 字幕恰好一份
    s1_subs = [s for s in loaded.subtitles if s.scene_id == "s1"]
    assert norm("".join(s.text for s in s1_subs)) == norm("AI 正在改变内容创作的方式。")
    # 无重复行（各场景时间轴都从 0 开始，按整行判重）
    rows = {(s.scene_id, s.start_ms, s.end_ms, s.text) for s in loaded.subtitles}
    assert len(rows) == len(loaded.subtitles)


# ---------------------------------------------------------------- 1 条真实全链路

@pytest.mark.live
def test_real_full_chain(tmp_path: Path):
    """真实 DeepSeek + edge-tts + FLUX 全链路。需 .env 里 SILICONFLOW_API_KEY。

    运行：pytest -m live
    """
    load_dotenv()
    api_key = os.environ.get("SILICONFLOW_API_KEY")
    if not api_key:
        pytest.skip("未配置 SILICONFLOW_API_KEY（.env）")

    store = ProjectStore(tmp_path / "data")
    store.init_repo()
    project = Project(project_id="proj_live", title="真实链路验证")
    store.create(project)

    ok_direct = run_direct(store, project, Director(api_key=api_key), TEXT)
    assert ok_direct, f"direct 失败: {project.errors}"
    assert len(project.scenes) == 2

    ok_assets = run_gen_assets(
        store, project,
        tts=EdgeTTS(project.config.tts),
        image=FluxImage(api_key=api_key),
    )
    assert ok_assets, f"gen_assets 失败: {project.errors}"

    loaded = store.load("proj_live")
    assert loaded.pipeline["direct"] == "done"
    assert loaded.pipeline["gen_assets"] == "done"
    assert set(loaded.assets) == {"vo_s1", "vo_s2", "img_s1", "img_s2"}
    assert all((store.project_dir("proj_live") / a.path).is_file() for a in loaded.assets.values())
    # 字幕覆盖全文
    assert norm("".join(s.text for s in loaded.subtitles)) == norm(TEXT)
