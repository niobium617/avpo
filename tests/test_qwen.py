"""千问渠道生图测试（DashScope 原生 API，mock HTTP 与轮询）。完全离线。"""

import pytest

from app.vision.base import PNG_MAGIC
from app.vision.cache import ImageCache
from app.vision.qwen import COST_PER_IMAGE, QwenImage

FAKE_PNG = PNG_MAGIC + b"qwen-fake"
WANX_MODEL = "wanx2.1-t2i-turbo"


class FakeQwen(QwenImage):
    """_post/_get 换成假件；download/sleep 由 monkeypatch 接管。"""

    def __init__(self, cache=None, submit_results=None, poll_states=None):
        super().__init__(api_key="test-key", cache=cache)
        self.submit_results = list(submit_results or [])
        self.poll_states = list(poll_states or [])
        self.submits: list[dict] = []

    def _post(self, url, body, extra_headers=None):
        self.submits.append({"url": url, "body": body, "headers": extra_headers})
        result = self.submit_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def _get(self, url):
        state = self.poll_states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state


@pytest.fixture()
def qwen_env(monkeypatch, tmp_path):
    """返回 (qwen, download 记录)：任务一次成功，下载固定 PNG。"""
    downloads: list[str] = []
    monkeypatch.setattr("app.vision.qwen.download", lambda url: downloads.append(url) or FAKE_PNG)
    monkeypatch.setattr("app.vision.qwen.time.sleep", lambda s: None)
    return downloads


def _succeeded(url="https://example.com/img.png"):
    return {"output": {"task_status": "SUCCEEDED", "results": [{"url": url}]}}


def test_qwen_happy_path(tmp_path, qwen_env):
    downloads = qwen_env
    qwen = FakeQwen(
        cache=ImageCache(tmp_path / "proj"),
        submit_results=[{"output": {"task_id": "t1"}}],
        poll_states=[_succeeded()],
    )
    result = qwen.generate("cat", tmp_path / "a.png", model=WANX_MODEL, size="16:9")

    assert result.from_cache is False and result.seed is not None
    assert result.cost == COST_PER_IMAGE
    assert (tmp_path / "a.png").read_bytes() == FAKE_PNG
    # 提交体：模型 + 16:9 → 1280*720（星号分隔）+ seed 透传
    body = qwen.submits[0]["body"]
    assert body["model"] == WANX_MODEL
    assert body["input"]["prompt"] == "cat"
    assert body["parameters"]["size"] == "1280*720"
    assert body["parameters"]["seed"] == result.seed
    assert qwen.submits[0]["headers"] == {"X-DashScope-Async": "enable"}
    # 轮询 url + 下载
    assert qwen.submits[0]["url"].endswith("/services/aigc/text2image/image-synthesis")
    assert downloads == ["https://example.com/img.png"]


def test_qwen_cache_hit_zero_calls(tmp_path, qwen_env):
    qwen = FakeQwen(
        cache=ImageCache(tmp_path / "proj"),
        submit_results=[{"output": {"task_id": "t1"}}],
        poll_states=[_succeeded()],
    )
    qwen.generate("cat", tmp_path / "a.png", model=WANX_MODEL)
    r2 = qwen.generate("cat", tmp_path / "b.png", model=WANX_MODEL)
    assert r2.from_cache and r2.cost == 0.0
    assert len(qwen.submits) == 1


def test_qwen_task_failed_retries_all(tmp_path, qwen_env, monkeypatch):
    monkeypatch.setattr("app.vision.qwen.time.sleep", lambda s: None)
    qwen = FakeQwen(
        submit_results=[{"output": {"task_id": "t"}}] * 3,
        poll_states=[{"output": {"task_status": "FAILED", "message": "boom"}}] * 3,
    )
    with pytest.raises(RuntimeError, match="3 次均失败"):
        qwen.generate("cat", tmp_path / "a.png", model=WANX_MODEL)
    assert len(qwen.submits) == 3


def test_qwen_task_timeout(tmp_path, qwen_env, monkeypatch):
    monkeypatch.setattr("app.vision.qwen.TASK_TIMEOUT_S", 0.05)
    monkeypatch.setattr("app.vision.qwen.POLL_INTERVAL_S", 0.01)
    qwen = FakeQwen(
        submit_results=[{"output": {"task_id": "t"}}] * 3,
        poll_states=[{"output": {"task_status": "PENDING"}}] * 30,
    )
    with pytest.raises(RuntimeError, match="3 次均失败"):
        qwen.generate("cat", tmp_path / "a.png", model=WANX_MODEL)


def test_qwen_rejects_non_png(tmp_path, qwen_env, monkeypatch):
    monkeypatch.setattr("app.vision.qwen.time.sleep", lambda s: None)
    monkeypatch.setattr("app.vision.qwen.download", lambda url: b"GIF89a-not-png")
    qwen = FakeQwen(
        submit_results=[{"output": {"task_id": "t"}}] * 3,
        poll_states=[_succeeded()] * 3,
    )
    with pytest.raises(RuntimeError, match="不是 PNG"):
        qwen.generate("cat", tmp_path / "a.png", model=WANX_MODEL)


def test_qwen_missing_task_id_raises(tmp_path, qwen_env, monkeypatch):
    monkeypatch.setattr("app.vision.qwen.time.sleep", lambda s: None)
    qwen = FakeQwen(submit_results=[{"output": {}}] * 3, poll_states=[])
    with pytest.raises(RuntimeError, match="task_id"):
        qwen.generate("cat", tmp_path / "a.png", model=WANX_MODEL)
