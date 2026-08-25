"""生图提供方协议（IMPLEMENTATION_PLAN 2.4 扩展：多渠道）。

FluxImage（SiliconFlow FLUX.1-schnell）与 QwenImage（DashScope 通义万相）
共用同一 generate() 外壳：
- 先查缓存（app.vision.cache）—— 命中 0 次 API 调用、成本 0；
- 失败重抽 ≤3 次（指数退避 1s/2s；seed 显式时确定性重试）；
- PNG 魔数校验；产物写 out_png 并回写缓存。

M6-7.4 参考图注入：渠道能力 = supports_reference_image / max_reference_images
类属性（providers.image_capabilities 无 key 内省，UI 按渠道自适应）。支持参考图
的渠道实现 _request_png_ref；不支持的渠道 generate(reference_png=...) 抛
ValueError（管线先行检查能力，双保险绝不崩链 —— 见 pipeline _gen_scene_candidates）。

子类实现 _request_png（渠道差异：请求方式/尺寸表/单价）。
"""

import hashlib
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
    # M6-7.4 能力标志：是否支持参考图注入（图生图/垫图）与一次最多几张
    supports_reference_image: bool = False
    max_reference_images: int = 0

    def __init__(self, cache: ImageCache | None = None):
        self.cache = cache

    def generate(
        self,
        prompt: str,
        out_png: Path,
        model: str,
        size: str = "16:9",
        *,
        seed: int | None = None,
        reference_png: Path | None = None,
    ) -> GeneratedImage:
        """生成一张图写入 out_png，返回 seed/成本；命中缓存则不调 API。

        M6-7.4 扩展：
        - seed 显式时所有重试用同一 seed（确定性重试/断点复用）；None 时每次重抽；
        - reference_png 给定时走图生图（渠道不支持则 ValueError —— 管线先查
          能力标志，此处为第二道防线）；
        - 缓存键含 seed 与参考图 hash（同 prompt 不同候选互不串缓存）。
        """
        out_png = Path(out_png)
        if reference_png is not None and not self.supports_reference_image:
            raise ValueError("当前渠道不支持参考图注入（supports_reference_image=False）")
        ref_hash = self._reference_hash(reference_png) if reference_png is not None else None

        hit: CacheHit | None = (
            self.cache.get(prompt, model, size, seed=seed, reference_hash=ref_hash)
            if self.cache else None
        )
        if hit:
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(hit.png)
            return GeneratedImage(path=out_png, seed=hit.seed, cost=0.0, from_cache=True)

        pixel_size = self.pixel_size(size)
        last_err: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            # 调用方 seed 决定缓存键；实际 seed 用于 API：显式时确定性重试，
            # None 时每次重抽（原行为），写缓存仍用 seedless 键（同 prompt 复用）
            if seed is None:
                actual_seed = random.randint(0, 10**9)
            else:
                actual_seed = seed
            try:
                if reference_png is not None:
                    png = self._request_png_ref(prompt, model, pixel_size, actual_seed, reference_png)
                else:
                    png = self._request_png(prompt, model, pixel_size, actual_seed)
            except Exception as exc:  # noqa: BLE001 —— 网络/服务错误统一重抽
                last_err = exc
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(2**attempt)
                continue
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(png)
            if self.cache:
                # 键用调用方 seed（None → seedless 键，seedless 调用方可复用）；
                # sidecar 记录实际 seed（重试后换种子也恢复最终成功那次）
                self.cache.put(
                    prompt, model, size, png, seed,
                    reference_hash=ref_hash, record_seed=actual_seed,
                )
            return GeneratedImage(path=out_png, seed=actual_seed, cost=self.cost_per_image, from_cache=False)

        raise RuntimeError(f"生图 {MAX_ATTEMPTS} 次均失败，最后错误: {last_err}")

    def pixel_size(self, size: str) -> str:
        """16:9 等语义尺寸 → 渠道像素尺寸；已是像素尺寸则原样返回。"""
        return self.size_map.get(size, size)

    @staticmethod
    def _reference_hash(path: Path) -> str:
        """参考图内容 hash —— 缓存键隔离（参考图变了缓存自然失效）。"""
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]

    @abstractmethod
    def _request_png(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        """渠道特定：prompt → PNG 字节。失败抛异常由外壳重试。"""
        ...

    def _request_png_ref(
        self, prompt: str, model: str, pixel_size: str, seed: int, reference_png: Path
    ) -> bytes:
        """渠道特定：参考图 + prompt → PNG 字节（图生图）。"""
        raise NotImplementedError(f"{type(self).__name__} 不支持参考图注入")
