"""FLUX 生图（IMPLEMENTATION_PLAN 2.4）。

SiliconFlow images API（OpenAI 兼容）：
    POST https://api.siliconflow.cn/v1/images/generations
参数 image_size（"宽x高"）、seed 经 extra_body 透传；返回 url 再下载字节。
16:9 → 1024x576（FLUX.1-schnell 支持尺寸）。

失败重抽 ≤3 次（每次换 seed，指数退避）；PNG 魔数校验；
先查缓存（app.vision.cache）—— 命中则 0 次 API 调用、成本 0。
"""

import random
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import openai

from app.vision.cache import CacheHit, ImageCache

BASE_URL = "https://api.siliconflow.cn/v1"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SIZE_MAP = {"16:9": "1024x576", "9:16": "576x1024", "1:1": "1024x1024"}
MAX_ATTEMPTS = 3
# 估账用（官网 $0.0014/张 ≈ ¥0.01；留裕量 ¥0.02，M3 成本账本再精确化）
COST_PER_IMAGE = 0.02


@dataclass
class GeneratedImage:
    path: Path
    seed: int | None
    cost: float
    from_cache: bool


class FluxImage:
    """SiliconFlow FLUX.1-schnell 生图器。"""

    def __init__(self, api_key: str, cache: ImageCache | None = None, base_url: str = BASE_URL):
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key)
        self.cache = cache

    def generate(
        self,
        prompt: str,
        out_png: Path,
        model: str = "black-forest-labs/FLUX.1-schnell",
        size: str = "16:9",
    ) -> GeneratedImage:
        """生成一张图写入 out_png，返回 seed/成本；命中缓存则不调 API。"""
        out_png = Path(out_png)

        hit: CacheHit | None = self.cache.get(prompt, model, size) if self.cache else None
        if hit:
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(hit.png)
            return GeneratedImage(path=out_png, seed=hit.seed, cost=0.0, from_cache=True)

        pixel_size = SIZE_MAP.get(size, size)
        last_err: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            seed = random.randint(0, 10**9)
            try:
                png = self._request(prompt, model, pixel_size, seed)
            except Exception as exc:  # noqa: BLE001 —— 网络/服务错误统一重抽
                last_err = exc
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(2**attempt)
                continue
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(png)
            if self.cache:
                self.cache.put(prompt, model, size, png, seed)
            return GeneratedImage(path=out_png, seed=seed, cost=COST_PER_IMAGE, from_cache=False)

        raise RuntimeError(f"FLUX 生图 {MAX_ATTEMPTS} 次均失败，最后错误: {last_err}")

    def _request(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        resp = self.client.images.generate(
            model=model,
            prompt=prompt,
            extra_body={"image_size": pixel_size, "seed": seed},
        )
        url = resp.data[0].url
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read()
        if not data.startswith(PNG_MAGIC):
            raise RuntimeError(f"返回的不是 PNG（前 8 字节: {data[:8]!r}）")
        return data
