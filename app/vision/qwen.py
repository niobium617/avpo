"""通义万相生图 —— DashScope 渠道（千问）。

DashScope 原生 API（兼容模式无 images 路由，已实测 404）：
    1. POST https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis
       头：Authorization: Bearer <key>、X-DashScope-Async: enable
       体：{"model": "wanx2.1-t2i-turbo", "input": {"prompt": ...},
            "parameters": {"size": "1280*720", "n": 1, "seed": ...}}
    2. 轮询 GET /api/v1/tasks/<task_id> 至 SUCCEEDED → output.results[0].url
    3. 下载字节（PNG）

size 用「宽*高」星号分隔；16:9 → 1280*720（实测可用）。
缓存/重试/落盘外壳见 app.vision.base.ImageProvider。
"""

import json
import time
import urllib.request

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

    def __init__(self, api_key: str, cache=None, base_url: str | None = None):
        super().__init__(cache)
        self.api_key = api_key
        self.base_url = base_url or BASE_URL

    def _request_png(self, prompt: str, model: str, pixel_size: str, seed: int) -> bytes:
        task_id = self._submit(prompt, model, pixel_size, seed)
        url = self._wait_task(task_id)
        data = download(url)
        if not data.startswith(PNG_MAGIC):
            raise RuntimeError(f"返回的不是 PNG（前 8 字节: {data[:8]!r}）")
        return data

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
