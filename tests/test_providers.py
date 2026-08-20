"""渠道解析测试（app/core/providers.py）：key 解析 / 工厂组装 / 模型-渠道匹配校验。"""

import pytest

from app.core.providers import config_for_provider, make_director, make_image, resolve_key
from app.core.schema import ImageConfig, LLMConfig
from app.vision.flux import FluxImage
from app.vision.qwen import QwenImage


def test_resolve_key_missing_has_env_name():
    with pytest.raises(KeyError, match="DASHSCOPE_API_KEY"):
        resolve_key("dashscope", env={})
    with pytest.raises(KeyError, match="SILICONFLOW_API_KEY"):
        resolve_key("siliconflow", env={})


def test_resolve_key_reads_env():
    assert resolve_key("dashscope", env={"DASHSCOPE_API_KEY": " sk-x "}) == "sk-x"


def test_make_director_dashscope():
    director = make_director(
        LLMConfig(provider="dashscope", model="qwen-plus"),
        env={"DASHSCOPE_API_KEY": "k"},
    )
    assert director.model == "qwen-plus"
    assert "dashscope.aliyuncs.com/compatible-mode" in str(director.client.base_url)


def test_make_image_chooses_channel_class():
    flux = make_image(ImageConfig(), env={"SILICONFLOW_API_KEY": "k"})
    qwen = make_image(
        ImageConfig(provider="dashscope", model="wanx2.1-t2i-turbo"),
        env={"DASHSCOPE_API_KEY": "k"},
    )
    assert isinstance(flux, FluxImage)
    assert isinstance(qwen, QwenImage)


def test_model_channel_mismatch_rejected():
    with pytest.raises(ValueError, match="wanx"):
        make_image(ImageConfig(provider="dashscope"), env={"DASHSCOPE_API_KEY": "k"})
    with pytest.raises(ValueError, match="qwen"):
        make_director(LLMConfig(provider="dashscope", model="deepseek-ai/DeepSeek-V3"),
                      env={"DASHSCOPE_API_KEY": "k"})


def test_config_for_provider_defaults():
    sf = config_for_provider("siliconflow")
    assert sf.llm.model == "deepseek-ai/DeepSeek-V3"
    assert sf.image.model == "black-forest-labs/FLUX.1-schnell"
    ds = config_for_provider("dashscope")
    assert ds.llm.model == "qwen-plus"
    assert ds.image.model == "wanx2.1-t2i-turbo"
    with pytest.raises(ValueError, match="未知渠道"):
        config_for_provider("openai")
