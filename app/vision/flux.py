"""FLUX 生图 —— SiliconFlow 渠道（IMPLEMENTATION_PLAN 2.4 原方案）。

SiliconFlow images API（OpenAI 兼容）：
    POST https://api.siliconflow.cn/v1/images/generations
参数 image_size（"宽x高"）、seed 经 extra_body 透传；返回 url 再下载字节。
缓存/重试/落盘外壳见 app.vision.base.ImageProvider。
"""

import openai

from app.vision.base import ImageProvider, PNG_MAGIC, download

BASE_URL = "https://api.siliconflow.cn/v1"
SIZE_MAP = {"16:9": "1024x576", "9:16": "576x1024", "1:1": "1024x1024"}
# 估账用（官网 $0.0014/张 ≈ ¥0.01；留裕量 ¥0.02，M3 成本账本再精确化）
COST_PER_IMAGE = 0.02


class FluxImage(ImageProvider):
    """SiliconFlow FLUX.1-schnell 生图器。"""

    cost_per_image = COST_PER_IMAGE
    size_map = SIZE_MAP

    def __init__(self, api_key: str, cache=None, base_url: str | None = None):
        super().__init__(cache)
        self.client = openai.OpenAI(base_url=base_url or BASE_URL, api_key=api_key)

    def _request_png(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        resp = self.client.images.generate(
            model=model,
            prompt=prompt,
            extra_body={"image_size": pixel_size, "seed": seed},
        )
        data = download(resp.data[0].url)
        if not data.startswith(PNG_MAGIC):
            raise RuntimeError(f"返回的不是 PNG（前 8 字节: {data[:8]!r}）")
        return data
