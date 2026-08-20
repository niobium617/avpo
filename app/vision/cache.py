"""生图缓存（IMPLEMENTATION_PLAN 2.5）。

prompt_hash = SHA256(model|size|prompt) 前 16 位。
命中即复用：不调 API，成本 0。缓存存项目目录 .cache/<hash>.png + <hash>.json
（sidecar 记录 prompt/model/size/seed，供复用时恢复元数据）。
data/ 是独立 git 仓库（版本历史），缓存随项目一起归档。
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

CACHE_DIRNAME = ".cache"


def prompt_hash(prompt: str, model: str, size: str) -> str:
    raw = f"{model}|{size}|{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class CacheHit:
    png: bytes
    seed: int | None


class ImageCache:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir) / CACHE_DIRNAME

    def get(self, prompt: str, model: str, size: str) -> CacheHit | None:
        h = prompt_hash(prompt, model, size)
        png_path = self.dir / f"{h}.png"
        meta_path = self.dir / f"{h}.json"
        if not (png_path.is_file() and meta_path.is_file()):
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return CacheHit(png=png_path.read_bytes(), seed=meta.get("seed"))

    def put(self, prompt: str, model: str, size: str, png: bytes, seed: int) -> str:
        """写入缓存，返回 prompt_hash。"""
        h = prompt_hash(prompt, model, size)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{h}.png").write_bytes(png)
        (self.dir / f"{h}.json").write_text(
            json.dumps(
                {"prompt": prompt, "model": model, "size": size, "seed": seed},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return h
