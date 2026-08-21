"""M2-3.5 并发生图测试（IMPLEMENTATION_PLAN 3.5）。

4 场景 × 0.25s 慢生图：串行 ≥1.0s，4 线程并行应 <0.75s —— 用墙钟证明并行生效。
"""

import time
from pathlib import Path

from app.core.pipeline import run_gen_assets
from app.core.project import ProjectStore
from app.core.schema import Project, Scene
from app.tts.base import TTSResult, TTSWord
from app.vision.base import GeneratedImage

_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413
_IMAGE_SLEEP_S = 0.3
_N_SCENES = 4


class _FakeTTS:
    async def synth(self, text: str, out_mp3: Path) -> TTSResult:
        out_mp3 = Path(out_mp3)
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        out_mp3.write_bytes(_MP3_FRAME * 50)
        content = [w for w in text if w not in "。，！？"]
        words = [TTSWord(ch, i * 50, i * 50 + 40) for i, ch in enumerate(content)]
        return TTSResult(mp3=out_mp3, words=words, duration_ms=len(content) * 50)


class _SlowImage:
    def __init__(self):
        self.cache = None

    def generate(self, prompt: str, out_png: Path, model: str, size: str = "16:9") -> GeneratedImage:
        time.sleep(_IMAGE_SLEEP_S)
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(b"\x89PNG\r\n\x1a\nslow")
        return GeneratedImage(path=out_png, seed=1, cost=0.0, from_cache=False)


def test_gen_assets_images_run_in_parallel(tmp_path: Path, monkeypatch) -> None:
    """4 张 0.3s 慢生图：串行 ≥1.2s，并行 <1.0s（含 asyncio/git 固定开销）。"""
    monkeypatch.setattr("app.core.pipeline._TTS_GAP_S", 0)     # 排除配音间隔干扰
    store = ProjectStore(tmp_path / "data", git=False)          # 排除 git 提交开销
    store.init_repo()

    project = Project(
        project_id="proj_conc",
        scenes=[
            Scene(
                scene_id=f"s{i}", narration=f"第{i}句。",
                image_prompt=f"prompt {i}", motion="none",
            )
            for i in range(1, _N_SCENES + 1)
        ],
    )
    project.pipeline["direct"] = "done"
    store.create(project)

    t0 = time.time()
    ok = run_gen_assets(store, project, tts=_FakeTTS(), image=_SlowImage())
    elapsed = time.time() - t0

    assert ok is True
    assert all(s.status == "done" for s in project.scenes)
    # 串行生图 = 4 × 0.3 = 1.2s；并行 ~0.3s + 固定开销（4 个 asyncio 循环 ≈0.3s）。
    assert elapsed < 1.0, f"生图未并行: {elapsed:.2f}s"
