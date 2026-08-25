"""通义万相生图 —— DashScope 渠道（千问）。

DashScope 原生 API（兼容模式无 images 路由，已实测 404）：
    1. POST https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis
       头：Authorization: Bearer <key>、X-DashScope-Async: enable
       体：{"model": "wanx2.1-t2i-turbo", "input": {"prompt": ...},
            "parameters": {"size": "1280*720", "n": 1, "seed": ...}}
    2. 轮询 GET /api/v1/tasks/<task_id> 至 SUCCEEDED → output.results[0].url
    3. 下载字节（PNG）

M6-7.4 参考图注入（图生图）：wanx2.1-imageedit，走 image2image 端点
    POST /services/aigc/image2image/image-synthesis（同为异步任务 + 轮询）
    体：{"model": "wanx2.1-imageedit", "input": {"function": "stylization_all",
        "prompt": ..., "base_image_url": "data:image/png;base64,..."},
        "parameters": {"n": 1}}
    base_image_url 用 base64 data URI（本地参考图，无需公网 URL）；
    注意 imageedit 要求参考图宽高各 512~4096 像素（见 live 验证测试）。

size 用「宽*高」星号分隔；16:9 → 1280*720（实测可用）。
缓存/重试/落盘外壳见 app.vision.base.ImageProvider。
"""

import base64
import json
import time
import urllib.request
from pathlib import Path

from app.vision.base import ImageProvider, PNG_MAGIC, download

BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
SIZE_MAP = {"16:9": "1280*720", "9:16": "720*1280", "1:1": "1024*1024"}
# 估账用（官网 wanx2.1-t2i-turbo ¥0.16/张，M3 成本账本再精确化）
COST_PER_IMAGE = 0.16

POLL_INTERVAL_S = 2
TASK_TIMEOUT_S = 180


class QwenImage(ImageProvider):
    """DashScope 通义万相生图器（异步任务 + 轮询）。"""

    cost_per_image = COST_PER_IMAGE
    size_map = SIZE_MAP
    # M6-7.4：wanx2.1-imageedit 支持图生图（live 验证门 tests/test_qwen.py::test_live_imageedit）
    supports_reference_image = True
    max_reference_images = 1

    def __init__(self, api_key: str, cache=None, base_url: str | None = None):
        super().__init__(cache)
        self.api_key = api_key
        self.base_url = base_url or BASE_URL

    def _request_png(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        task_id = self._submit(prompt, model, pixel_size, seed)
        return self._download_result(task_id)

    def _request_png_ref(
        self, prompt: str, model: str, pixel_size: str, seed: int, reference_png: Path
    ) -> bytes:
        """图生图：参考图（base64 data URI）→ 编辑后的 PNG。"""
        task_id = self._submit_edit(prompt, model, reference_png)
        return self._download_result(task_id)

    def _submit(self, prompt: str, model: str, pixel_size: str, seed: int) -> str:
        body = {
            "model": model,
            "input": {"prompt": prompt},
            "parameters": {"size": pixel_size, "n": 1, "seed": seed},
        }
        resp = self._post(
            f"{self.base_url}/services/aigc/text2image/image-synthesis",
            body,
            extra_headers={"X-DashScope-Async": "enable"},
        )
        task_id = resp.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"DashScope 未返回 task_id: {resp}")
        return task_id

    def _submit_edit(self, prompt: str, model: str, reference_png: Path) -> str:
        """wanx2.1-imageedit：参考图 → 编辑任务。

        function=stylization_all 整图风格化（最接近「保持主体 + 按提示词重绘」）；
        base_image_url 用 base64 data URI（本地参考图免公网 URL）。
        """
        b64 = base64.b64encode(reference_png.read_bytes()).decode("ascii")
        body = {
            "model": model,
            "input": {
                "function": "stylization_all",
                "prompt": prompt,
                "base_image_url": f"data:image/png;base64,{b64}",
            },
            "parameters": {"n": 1},
        }
        resp = self._post(
            f"{self.base_url}/services/aigc/image2image/image-synthesis",
            body,
            extra_headers={"X-DashScope-Async": "enable"},
        )
        task_id = resp.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"DashScope 未返回 task_id: {resp}")
        return task_id

    def _download_result(self, task_id: str) -> bytes:
        url = self._wait_task(task_id)
        data = download(url)
        if not data.startswith(PNG_MAGIC):
            raise RuntimeError(f"返回的不是 PNG（前 8 字节: {data[:8]!r}）")
        return data

    def _wait_task(self, task_id: str) -> str:
        deadline = time.monotonic() + TASK_TIMEOUT_S
        while time.monotonic() < deadline:
            status = self._get(f"{self.base_url}/tasks/{task_id}")
            output = status.get("output", {})
            state = output.get("task_status")
            if state == "SUCCEEDED":
                results = output.get("results") or []
                if not results:
                    raise RuntimeError(f"任务成功但无结果: {status}")
                return results[0]["url"]
            if state in ("FAILED", "CANCELED", "UNKNOWN"):
                raise RuntimeError(f"生图任务失败: {output}")
            time.sleep(POLL_INTERVAL_S)
        raise RuntimeError(f"生图任务超时（>{TASK_TIMEOUT_S}s）: {task_id}")

    def _post(self, url: str, body: dict, extra_headers: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
