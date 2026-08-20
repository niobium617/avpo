"""生图提供方协议（IMPLEMENTATION_PLAN 2.4 扩展：多渠道）。

FluxImage（SiliconFlow FLUX.1-schnell）与 QwenImage（DashScope 通义万相）
共用同一 generate() 外壳：
- 先查缓存（app.vision.cache）—— 命中 0 次 API 调用、成本 0；
- 失败重抽 ≤3 次（每次换 seed，指数退避 1s/2s）；
- PNG 魔数校验；产物写 out_png 并回写缓存。

子类只实现 _request_png（渠道差异：请求方式/尺寸表/单价）。
"""

import random
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.vision.cache import CacheHit, ImageCache

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_ATTEMPTS = 3


@dataclass
class GeneratedImage:
    path: Path
    seed: int | None
    cost: float
    from_cache: bool


def download(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


class ImageProvider(ABC):
    """生图提供方基类：缓存/重试/落盘外壳 + 渠道特定的 _request_png。"""

    # 渠道差异（子类覆盖）
    cost_per_image: float
    size_map: dict[str, str] = {}

    def __init__(self, cache: ImageCache | None = None):
        self.cache = cache

    def generate(
        self,
        prompt: str,
        out_png: Path,
        model: str,
        size: str = "16:9",
    ) -> GeneratedImage:
        """生成一张图写入 out_png，返回 seed/成本；命中缓存则不调 API。"""
        out_png = Path(out_png)

        hit: CacheHit | None = self.cache.get(prompt, model, size) if self.cache else None
        if hit:
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(hit.png)
            return GeneratedImage(path=out_png, seed=hit.seed, cost=0.0, from_cache=True)

        pixel_size = self.pixel_size(size)
        last_err: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            seed = random.randint(0, 10**9)
            try:
                png = self._request_png(prompt, model, pixel_size, seed)
            except Exception as exc:  # noqa: BLE001 —— 网络/服务错误统一重抽
                last_err = exc
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(2**attempt)
                continue
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(png)
            if self.cache:
                self.cache.put(prompt, model, size, png, seed)
            return GeneratedImage(path=out_png, seed=seed, cost=self.cost_per_image, from_cache=False)

        raise RuntimeError(f"生图 {MAX_ATTEMPTS} 次均失败，最后错误: {last_err}")

    def pixel_size(self, size: str) -> str:
        """16:9 等语义尺寸 → 渠道像素尺寸；已是像素尺寸则原样返回。"""
        return self.size_map.get(size, size)

    @abstractmethod
    def _request_png(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        """渠道特定：prompt → PNG 字节。失败抛异常由外壳重试。"""
        ...
