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


def prompt_hash(prompt: str, model: str, size: str, seed: int | None = None) -> str:
    """M6-7.4：seed 进缓存键（候选图同 prompt 不同种子 → 不同键）。

    seed=None 时保持旧格式 model|size|prompt —— 旧缓存条目仍可读（升级后首次
    重生成的一次性成本详见 README 迁移说明）。seed 显式时插入 model|size|seed|prompt。
    """
    raw = f"{model}|{size}|{prompt}" if seed is None else f"{model}|{size}|{seed}|{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class CacheHit:
    png: bytes
    seed: int | None


class ImageCache:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir) / CACHE_DIRNAME

    def get(
        self,
        prompt: str,
        model: str,
        size: str,
        seed: int | None = None,
        reference_hash: str | None = None,
    ) -> CacheHit | None:
        """查缓存；seed/reference_hash 参与键（M6-7.4 候选图与图生图精修隔离）。"""
        h = self._key(prompt, model, size, seed, reference_hash)
        png_path = self.dir / f"{h}.png"
        meta_path = self.dir / f"{h}.json"
        if not (png_path.is_file() and meta_path.is_file()):
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return CacheHit(png=png_path.read_bytes(), seed=meta.get("seed"))

    def put(
        self,
        prompt: str,
        model: str,
        size: str,
        png: bytes,
        seed: int | None = None,
        reference_hash: str | None = None,
        *,
        record_seed: int | None = None,
    ) -> str:
        """写入缓存，返回实际缓存键 hash。

        M6-7.4：seed 决定键（None = 旧 seedless 键，seedless 调用方互读复用）；
        record_seed 仅进 sidecar（seedless 调用方随机出的实际 seed 也要恢复，
        供断点续跑 —— 键不含它，下次 seedless 调用可命中）。
        """
        meta_seed = seed if record_seed is None else record_seed
        h = self._key(prompt, model, size, seed, reference_hash)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{h}.png").write_bytes(png)
        (self.dir / f"{h}.json").write_text(
            json.dumps(
                {"prompt": prompt, "model": model, "size": size, "seed": meta_seed},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return h

    @staticmethod
    def _key(prompt: str, model: str, size: str, seed: int | None, reference_hash: str | None) -> str:
        raw = prompt_hash(prompt, model, size, seed=seed)
        return f"{raw}_{reference_hash}" if reference_hash else raw
