"""M2-3.4 e2e 测试：固定输入 + 固定 seed → 固定输出（IMPLEMENTATION_PLAN 3.4）。

完全离线：director/生图/配音全部假实现（确定性输出），走真实 CLI run 全链路
（状态机 + 时间线组装 + 导出）。两个项目跑同一条文案，project.json 必须逐字段一致
—— 这是整条管线无隐式随机源的回归护栏。真实链路的耗时记录见 tests/e2e_time_log.md。
"""

import hashlib
import json
import struct
import time
import zlib
from pathlib import Path

from typer.testing import CliRunner

from app.cli import create_app
from app.core.project import ProjectStore
from app.core.schema import Scene
from app.tts.base import TTSResult, TTSWord
from app.vision.base import GeneratedImage

# 极简 MP3 帧（同 test_tts）
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413
_FRAME_MS = 417 / 16.0     # 每帧 ≈26.06ms（417 字节 ÷ 16000 字节/秒）


class _FakeDirector:
    """固定分镜：与文案无关的 2 场景（e2e 文案固定，允许硬编码）。"""

    def storyboard(
        self, text: str, motion_hint: str = "", *, brief=None, shot_size_hint=""
    ) -> tuple[list[Scene], float]:
        scenes = [
            Scene(
                scene_id="s1",
                narration="第一条文案。",
                visual="画面一",
                image_prompt="A minimal first frame, flat color, studio light",
                motion="zoom_in_slow",
            ),
            Scene(
                scene_id="s2",
                narration="第二条文案。",
                visual="画面二",
                image_prompt="A minimal second frame, flat color, warm tone",
                motion="pan_right",
            ),
        ]
        return scenes, 0.0


class _FakeImage:
    """确定性生图：PNG 内容由 prompt 的 sha256 决定，seed 固定 42（候选种子不影响输出）。"""

    def __init__(self, cache=None):
        self.cache = cache

    def generate(
        self, prompt: str, out_png: Path, model: str, size: str = "16:9", seed: int | None = None
    ) -> GeneratedImage:
        h = hashlib.sha256(prompt.encode()).digest()
        png = _png_from_bytes(h[:3])
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(png)
        return GeneratedImage(path=out_png, seed=42, cost=0.0, from_cache=False)


class _FakeTTS:
    """确定性配音：逐字词流（100ms/字），mp3 帧数按同语速折算（时长 ≈ 字数×100ms）。"""

    def __init__(self, config=None):
        pass

    async def synth(self, text: str, out_mp3: Path) -> TTSResult:
        out_mp3 = Path(out_mp3)
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        # 帧数按词流语速折算：mp3 实际时长必须 ≥ 词流时间轴，否则字幕超出场景时长
        n_frames = max(1, round(len(text) * 100 / _FRAME_MS))
        out_mp3.write_bytes(_MP3_FRAME * n_frames)
        words = [TTSWord(text=ch, start_ms=i * 100, end_ms=i * 100 + 80) for i, ch in enumerate(text)]
        return TTSResult(mp3=out_mp3, words=words, duration_ms=n_frames * _FRAME_MS)


def _png_from_bytes(rgb: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    w = h = 8
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _invoke_run(app, runner, project_id: str, text: str) -> float:
    """跑一次 run（--yes 跳过确认），返回耗时秒。"""
    t0 = time.time()
    result = runner.invoke(app, ["run", project_id, "--text", text, "--yes"])
    assert result.exit_code == 0, result.output
    return time.time() - t0


def test_e2e_full_chain_deterministic(tmp_path: Path, monkeypatch) -> None:
    """同一条文案跑两个项目 → project.json 除项目标识外逐字段一致。"""
    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_director", lambda config: _FakeDirector())
    monkeypatch.setattr("app.cli.make_image", lambda config: _FakeImage())
    monkeypatch.setattr("app.cli.EdgeTTS", _FakeTTS)

    app = create_app(tmp_path / "data")
    runner = CliRunner()
    text = "第一条文案。第二条文案。"
    store = ProjectStore(tmp_path / "data")

    runner.invoke(app, ["new", "proj_e2e_a", "--title", "e2e"])
    elapsed_a = _invoke_run(app, runner, "proj_e2e_a", text)
    proj_a = store.load("proj_e2e_a")
    dump_a = proj_a.model_dump(mode="json")

    runner.invoke(app, ["new", "proj_e2e_b", "--title", "e2e"])
    _invoke_run(app, runner, "proj_e2e_b", text)
    dump_b = store.load("proj_e2e_b").model_dump(mode="json")

    # 项目标识与导出目录名含 project_id → 归一后其余必须一致
    assert dump_b["project_id"] != dump_a["project_id"]
    dump_b["project_id"] = dump_a["project_id"]
    dump_b["export"]["path"] = dump_a["export"]["path"]
    assert dump_b == dump_a

    # 全链路节点 done + 产物齐备
    assert all(v == "done" for v in dump_a["pipeline"].values())
    assert len(dump_a["scenes"]) == 2
    assert len(dump_a["assets"]) == 8            # 2 场景 × 3 候选图 + 2 配音（M6-7.5）
    assert len(dump_a["subtitles"]) == 2
    assert len(dump_a["timeline"]["video"]) == 2

    # 离线 e2e 应在 60s 内（真实链路耗时见 e2e_time_log.md）
    assert elapsed_a < 60, f"离线 e2e 过慢: {elapsed_a:.1f}s"


def test_e2e_rerun_skips_done(tmp_path: Path, monkeypatch) -> None:
    """全 done 重跑：节点全部跳过，秒级返回，project.json 不变。"""
    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_director", lambda config: _FakeDirector())
    monkeypatch.setattr("app.cli.make_image", lambda config: _FakeImage())
    monkeypatch.setattr("app.cli.EdgeTTS", _FakeTTS)

    app = create_app(tmp_path / "data")
    runner = CliRunner()
    store = ProjectStore(tmp_path / "data")
    text = "第一条文案。第二条文案。"

    runner.invoke(app, ["new", "proj_e2e_c", "--title", "e2e"])
    _invoke_run(app, runner, "proj_e2e_c", text)
    before = store.json_path("proj_e2e_c").read_text(encoding="utf-8")

    elapsed = _invoke_run(app, runner, "proj_e2e_c", text)
    after = store.json_path("proj_e2e_c").read_text(encoding="utf-8")

    assert before == after
    assert elapsed < 5, f"done 跳过重跑应秒级返回: {elapsed:.1f}s"


def test_e2e_draft_opens_as_valid_json(tmp_path: Path, monkeypatch) -> None:
    """导出的草稿 JSON 结构合法（三轨齐全）—— 打开验证的离线替身。"""
    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_director", lambda config: _FakeDirector())
    monkeypatch.setattr("app.cli.make_image", lambda config: _FakeImage())
    monkeypatch.setattr("app.cli.EdgeTTS", _FakeTTS)

    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_e2e_d", "--title", "e2e"])
    _invoke_run(app, runner, "proj_e2e_d", "第一条文案。第二条文案。")

    project = ProjectStore(tmp_path / "data").load("proj_e2e_d")
    draft_dir = tmp_path / "data" / "projects" / "proj_e2e_d" / project.export.path
    draft = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    assert {t["type"] for t in draft["tracks"]} == {"video", "audio", "text"}
