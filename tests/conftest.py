"""测试公共设施。

临时目录约定：优先 F 盘（本机免 C 盘污染），不可用则退回系统临时目录。
必须在任何临时目录被使用之前设置 —— tempfile 优先读 TMP/TEMP 环境变量。
"""

import os
import tempfile
from pathlib import Path


def _pick_tmp_root() -> Path:
    for candidate in (Path("F:/tmp/avpo-pytest"),):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    fallback = Path(tempfile.gettempdir()) / "avpo-pytest"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


_TMP_ROOT = _pick_tmp_root()
os.environ["TMP"] = str(_TMP_ROOT)
os.environ["TEMP"] = str(_TMP_ROOT)

import pytest  # noqa: E402

from app.core.project import ProjectStore  # noqa: E402
from app.core.schema import (  # noqa: E402
    Asset,
    AudioClip,
    Project,
    Scene,
    Subtitle,
    Timeline,
    VideoClip,
    Voiceover,
)


@pytest.fixture()
def store(tmp_path: Path) -> ProjectStore:
    """带 git 仓库的临时 ProjectStore。"""
    s = ProjectStore(tmp_path / "data")
    s.init_repo()
    return s


@pytest.fixture()
def sample_project() -> Project:
    """M0 golden 样例：2 场景、配音、字幕、2 素材、时间线齐全。"""
    return Project(
        project_id="proj_001",
        title="AI 产品口播",
        scenes=[
            Scene(
                scene_id="s1",
                narration="AI 正在改变内容创作的方式。",
                visual="都市夜景, 数字光效, 电影感",
                image_prompt="cinematic city night, digital light",
                image_asset_id="a_img_1",
                motion="zoom_in_slow",
                status="done",
                cost={"image": 0.02, "llm": 0.001},
            ),
            Scene(
                scene_id="s2",
                narration="现在,用 AVPO 一个人也能做视频。",
                visual="一人对着电脑, 屏幕发光",
                image_prompt="one person at desk, glowing screen",
                image_asset_id="a_img_2",
                motion="pan_left",
                status="done",
            ),
        ],
        voiceover=Voiceover(asset_id="a_vo", status="done", duration_ms=8320),
        subtitles=[
            Subtitle(scene_id="s1", start_ms=0, end_ms=1450, text="AI 正在改变"),
            Subtitle(scene_id="s1", start_ms=1450, end_ms=2900, text="内容创作的方式"),
            Subtitle(scene_id="s2", start_ms=2900, end_ms=5800, text="现在,用 AVPO"),
            Subtitle(scene_id="s2", start_ms=5800, end_ms=8320, text="一个人也能做视频"),
        ],
        timeline=Timeline(
            video=[
                VideoClip(asset_id="a_img_1", start_ms=0, duration_ms=2900, motion="zoom_in_slow"),
                VideoClip(asset_id="a_img_2", start_ms=2900, duration_ms=5420, motion="pan_left"),
            ],
            voiceover=[AudioClip(asset_id="a_vo", offset_ms=0)],
        ),
        assets={
            "a_img_1": Asset(
                type="image", path="assets/s1_v1.png",
                model="FLUX.1-schnell", prompt_hash="e4a2b9", seed=1234,
                cost=0.02, status="done",
            ),
            "a_img_2": Asset(
                type="image", path="assets/s2_v1.png",
                model="FLUX.1-schnell", prompt_hash="f7c1d2", seed=5678,
                cost=0.02, status="done",
            ),
            "a_vo": Asset(type="audio", path="assets/vo_v1.mp3", model="edge-tts", status="done"),
        },
    )
