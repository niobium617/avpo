"""M1-2.4/2.5 单元测试：FLUX 生图（mock API）+ 生图缓存。完全离线。"""

import urllib.request

import pytest

from app.vision.base import MAX_ATTEMPTS, PNG_MAGIC
from app.vision.cache import ImageCache, prompt_hash
from app.vision.flux import COST_PER_IMAGE, FluxImage

FAKE_PNG = PNG_MAGIC + b"fake-image-bytes"
FLUX_MODEL = "black-forest-labs/FLUX.1-schnell"


class _Resp:
    def __init__(self, url: str):
        self.data = [_Url(url)]


class _Url:
    def __init__(self, url: str):
        self.url = url


class _FakeImages:
    """outcomes: 按调用顺序排队的结果；Exception = 抛错，其余为 _Resp。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("API 调用次数超过预期")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes):
        self.images = _FakeImages(outcomes)


def _fake_urlopen(png: bytes):
    class _Resp:
        def __init__(self, data: bytes):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return self._data

    def urlopen(url, timeout=None):
        return _Resp(png)

    return urlopen


@pytest.fixture()
def flux_with_png(monkeypatch, tmp_path):
    """返回 (flux, fake_images)：API 成功、下载固定 PNG 字节。"""
    flux = FluxImage(api_key="test-key", cache=ImageCache(tmp_path / "proj"))
    fake = _FakeImages([_Resp("https://example.com/img.png")])
    flux.client = _FakeClient([])  # 占位，下面替换
    flux.client.images = fake
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(FAKE_PNG))
    return flux, fake


# ---------------------------------------------------------------- 2.5 缓存

def test_prompt_hash_deterministic_and_sensitive():
    h1 = prompt_hash("cat", "m", "16:9")
    assert h1 == prompt_hash("cat", "m", "16:9")
    assert h1 != prompt_hash("dog", "m", "16:9")
    assert h1 != prompt_hash("cat", "m2", "16:9")
    assert h1 != prompt_hash("cat", "m", "1:1")
    assert len(h1) == 16


def test_cache_roundtrip(tmp_path):
    cache = ImageCache(tmp_path / "proj")
    assert cache.get("cat", "m", "16:9") is None
    cache.put("cat", "m", "16:9", b"png-bytes", seed=42)
    hit = cache.get("cat", "m", "16:9", seed=42)
    assert hit is not None and hit.png == b"png-bytes" and hit.seed == 42
    # 同 hash 的 sidecar 落盘（键含 seed）
    assert (tmp_path / "proj" / ".cache" / f"{prompt_hash('cat', 'm', '16:9', seed=42)}.json").is_file()
    # seed 隔离语义：seedless 读不到带 seed 的条目
    assert cache.get("cat", "m", "16:9") is None


def test_cache_seedless_roundtrip(tmp_path):
    """seedless 写读同键（M6 前旧行为）：sidecar 记录实际 seed，命中可恢复。"""
    cache = ImageCache(tmp_path / "proj")
    cache.put("cat", "m", "16:9", b"png-bytes", seed=None, record_seed=7)
    hit = cache.get("cat", "m", "16:9")
    assert hit is not None and hit.png == b"png-bytes" and hit.seed == 7


def test_generate_second_time_hits_cache_zero_api_calls(flux_with_png, tmp_path):
    flux, fake = flux_with_png
    out1 = tmp_path / "a1.png"
    out2 = tmp_path / "a2.png"

    r1 = flux.generate("cat", out1, model=FLUX_MODEL)
    r2 = flux.generate("cat", out2, model=FLUX_MODEL)

    assert len(fake.calls) == 1                    # 二次生成 0 次 API 调用
    assert not r1.from_cache and r1.cost == COST_PER_IMAGE
    assert r2.from_cache and r2.cost == 0.0
    assert r2.seed == r1.seed                      # seed 从缓存 sidecar 恢复
    assert out1.read_bytes() == out2.read_bytes() == FAKE_PNG


def test_generate_sends_expected_params(flux_with_png, tmp_path):
    flux, fake = flux_with_png
    flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL, size="16:9")

    call = fake.calls[0]
    assert call["model"] == "black-forest-labs/FLUX.1-schnell"
    assert call["prompt"] == "cat"
    assert call["extra_body"]["image_size"] == "1024x576"      # 16:9 映射
    assert 0 <= call["extra_body"]["seed"] <= 10**9


def test_generate_retries_on_transient_error(flux_with_png, tmp_path):
    flux, fake = flux_with_png
    # 前 2 次失败，第 3 次成功（重抽换 seed）
    fake.outcomes = [RuntimeError("boom"), RuntimeError("boom"), _Resp("https://example.com/x.png")]
    fake.calls.clear()

    result = flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL)

    assert result.from_cache is False and result.seed is not None
    assert len(fake.calls) == 3
    seeds = {c["extra_body"]["seed"] for c in fake.calls}
    assert len(seeds) == 3                            # 每次换 seed


def test_generate_raises_after_all_attempts_fail(monkeypatch, tmp_path):
    flux = FluxImage(api_key="test-key", cache=ImageCache(tmp_path / "proj"))
    fake = _FakeImages([RuntimeError("boom")] * MAX_ATTEMPTS)
    flux.client = _FakeClient([])
    flux.client.images = fake
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(FAKE_PNG))

    with pytest.raises(RuntimeError, match="3 次均失败"):
        flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL)
    assert len(fake.calls) == MAX_ATTEMPTS


def test_generate_rejects_non_png(monkeypatch, tmp_path):
    flux = FluxImage(api_key="test-key", cache=ImageCache(tmp_path / "proj"))
    fake = _FakeImages([_Resp("https://example.com/x.png")] * MAX_ATTEMPTS)
    flux.client = _FakeClient([])
    flux.client.images = fake
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(b"GIF89a-not-png"))

    with pytest.raises(RuntimeError, match="不是 PNG"):
        flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL)


def test_generate_without_cache_never_hits(tmp_path, monkeypatch):
    flux = FluxImage(api_key="test-key")             # 无缓存
    fake = _FakeImages([_Resp("https://example.com/x.png")] * 2)
    flux.client = _FakeClient([])
    flux.client.images = fake
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(FAKE_PNG))

    flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL)
    flux.generate("cat", tmp_path / "b.png", model=FLUX_MODEL)
    assert len(fake.calls) == 2


# ---------------------------------------------------------------- M6-7.4 seed 键/参考图/能力

def test_prompt_hash_seed_scoped():
    """seed 进键：同 prompt 不同 seed → 不同键；seed=None 保持旧格式。"""
    h = prompt_hash("cat", "m", "16:9")
    assert h == prompt_hash("cat", "m", "16:9", seed=None)     # 旧格式兼容
    h1 = prompt_hash("cat", "m", "16:9", seed=1)
    h2 = prompt_hash("cat", "m", "16:9", seed=2)
    assert h1 != h2 and h1 != h


def test_cache_seed_isolated(tmp_path):
    """带 seed 的条目与 seedless 互不串键；旧 seedless 缓存仍可读。"""
    cache = ImageCache(tmp_path / "proj")
    cache.put("cat", "m", "16:9", b"seed-1", seed=1)
    cache.put("cat", "m", "16:9", b"seed-11", seed=11)

    assert cache.get("cat", "m", "16:9", seed=1).png == b"seed-1"
    assert cache.get("cat", "m", "16:9", seed=11).png == b"seed-11"
    assert cache.get("cat", "m", "16:9", seed=2) is None              # 不同 seed 无命中
    # 旧代码写的 seedless 条目（无 seed 键）：仍按旧格式读取（M6 迁移兼容）
    h = prompt_hash("cat", "m", "16:9")
    (cache.dir / f"{h}.png").write_bytes(b"legacy")
    (cache.dir / f"{h}.json").write_text('{"prompt": "cat", "model": "m", "size": "16:9", "seed": 7}')
    assert cache.get("cat", "m", "16:9").png == b"legacy"
    assert cache.get("cat", "m", "16:9").seed == 7


def test_cache_reference_hash_isolated(tmp_path):
    cache = ImageCache(tmp_path / "proj")
    cache.put("cat", "m", "16:9", b"plain", seed=5)
    cache.put("cat", "m", "16:9", b"ref-a", seed=5, reference_hash="aaaa")
    assert cache.get("cat", "m", "16:9", seed=5, reference_hash="bbbb") is None
    assert cache.get("cat", "m", "16:9", seed=5, reference_hash="aaaa").png == b"ref-a"
    assert cache.get("cat", "m", "16:9", seed=5).png == b"plain"       # 无参考图读纯文生


def test_generate_explicit_seed_retries_with_same_seed(flux_with_png, tmp_path):
    """显式 seed：3 次重试用同一 seed（确定性重试，断点复用的基础）。"""
    flux, fake = flux_with_png
    fake.outcomes = [RuntimeError("boom"), RuntimeError("boom"), _Resp("https://example.com/x.png")]
    fake.calls.clear()

    result = flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL, seed=777)

    assert result.seed == 777
    assert len(fake.calls) == 3
    seeds = {c["extra_body"]["seed"] for c in fake.calls}
    assert seeds == {777}


def test_generate_seed_cache_hit_zero_calls(flux_with_png, tmp_path):
    """同 seed 二次生成走缓存（候选图断点续跑 0 重复调用）。"""
    flux, fake = flux_with_png
    flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL, seed=9)
    fake.calls.clear()

    r2 = flux.generate("cat", tmp_path / "b.png", model=FLUX_MODEL, seed=9)
    assert len(fake.calls) == 0
    assert r2.from_cache and r2.seed == 9


def test_flux_rejects_reference_image(tmp_path):
    """FLUX（siliconflow）无图生图：generate(reference_png=...) 当场 ValueError。"""
    flux = FluxImage(api_key="test-key", cache=ImageCache(tmp_path / "proj"))
    ref = tmp_path / "ref.png"
    ref.write_bytes(FAKE_PNG)
    with pytest.raises(ValueError, match="不支持参考图注入"):
        flux.generate("cat", tmp_path / "a.png", model=FLUX_MODEL, reference_png=ref)


def test_flux_capability_flags_default_false():
    assert FluxImage.supports_reference_image is False
    assert FluxImage.max_reference_images == 0
