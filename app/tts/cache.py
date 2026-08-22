"""TTS 结果缓存（M3-4.1 断点续跑）。

生图有 prompt_hash 缓存（app/vision/cache.py），配音没有 —— kill -9 后重跑
gen_assets 会把所有配音重新合成一遍（重复 API 调用）。本模块补上：

- sidecar 文件 assets/vo_<scene_id>.json 存词级时间戳（word 时间戳是字幕重建的唯一来源）；
- 缓存带 narration_hash 校验：用户改了口播文案 → 缓存自动失效，重新合成，
  不会拿旧配音配新文案（正确性问题，生图侧 prompt_hash 天然同语义）；
- mp3 文件本身也是缓存一部分：mp3 丢失则缓存视为无效（配音资产不完整）。
"""

import hashlib
import json
from pathlib import Path

from app.tts.base import TTSWord

CACHE_SUFFIX = ".json"


def _narration_hash(narration: str) -> str:
    return hashlib.sha256(narration.encode("utf-8")).hexdigest()[:16]


def cache_path(assets_dir: Path, scene_id: str) -> Path:
    return Path(assets_dir) / f"vo_{scene_id}{CACHE_SUFFIX}"


def load_words(assets_dir: Path, scene_id: str, narration: str) -> list[TTSWord] | None:
    """读缓存词级时间戳；文件缺失或 narration 与缓存不符返回 None（视为无缓存）。"""
    path = cache_path(assets_dir, scene_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("narration_hash") != _narration_hash(narration):
        return None
    words = data.get("words", [])
    if not isinstance(words, list) or not words:
        return None
    return [
        TTSWord(text=w["text"], start_ms=w["start_ms"], end_ms=w["end_ms"])
        for w in words
        if isinstance(w, dict) and "text" in w
    ]


def save_words(assets_dir: Path, scene_id: str, narration: str, words: list[TTSWord]) -> Path:
    """写 sidecar 缓存（narration 参与 hash：文案变了缓存自动失效）。"""
    path = cache_path(assets_dir, scene_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "narration_hash": _narration_hash(narration),
                "words": [
                    {"text": w.text, "start_ms": w.start_ms, "end_ms": w.end_ms} for w in words
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
