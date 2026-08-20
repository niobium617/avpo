"""外部渠道解析（IMPLEMENTATION_PLAN 2.4 扩展：多渠道）。

渠道配置：环境变量（key/base_url）与默认模型，见 CHANNELS。
make_director / make_image 按 project.config 的 provider 组装实现类；
渠道与模型不匹配（如 dashscope + FLUX）当场报错，不给上游甩 404。
"""

import os

from app.core.schema import ImageConfig, LLMConfig, ProjectConfig
from app.director.director import Director
from app.vision.base import ImageProvider
from app.vision.flux import FluxImage
from app.vision.qwen import QwenImage

CHANNELS: dict[str, dict] = {
    "siliconflow": {
        "key_env": "SILICONFLOW_API_KEY",
        "llm_base": "https://api.siliconflow.cn/v1",
        "image_base": "https://api.siliconflow.cn/v1",
        "defaults": {"llm": "deepseek-ai/DeepSeek-V3", "image": "black-forest-labs/FLUX.1-schnell"},
        "llm_prefix": "",      # 不额外校验前缀
        "image_prefix": "",
    },
    "dashscope": {
        "key_env": "DASHSCOPE_API_KEY",
        # 兼容模式（chat）与原生 API（生图）是两个根路径
        "llm_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "image_base": "https://dashscope.aliyuncs.com/api/v1",
        "defaults": {"llm": "qwen-plus", "image": "wanx2.1-t2i-turbo"},
        "llm_prefix": "qwen",
        "image_prefix": "wanx",
    },
}


def resolve_key(provider: str, env: dict | None = None) -> str:
    """取渠道 API key；缺失时给出明确的修复动作。"""
    env = os.environ if env is None else env
    channel = CHANNELS.get(provider)
    if channel is None:
        raise ValueError(f"未知渠道: {provider}")
    key = env.get(channel["key_env"], "").strip()
    if not key:
        raise KeyError(
            f"缺少 {channel['key_env']}：请在项目根 .env 配置（模板见 .env.example）"
        )
    return key


def _check_model(provider: str, model: str, kind: str) -> None:
    prefix = CHANNELS[provider][f"{kind}_prefix"]
    if prefix and not model.startswith(prefix):
        raise ValueError(
            f"{provider} 渠道不支持 {kind} 模型 {model!r}（应使用 {prefix}* 系列，"
            f"默认 {CHANNELS[provider]['defaults'][kind]}）"
        )


def make_director(config: LLMConfig, env: dict | None = None) -> Director:
    key = resolve_key(config.provider, env)
    _check_model(config.provider, config.model, "llm")
    return Director(api_key=key, model=config.model, base_url=CHANNELS[config.provider]["llm_base"])


def make_image(config: ImageConfig, env: dict | None = None) -> ImageProvider:
    key = resolve_key(config.provider, env)
    _check_model(config.provider, config.model, "image")
    base = CHANNELS[config.provider]["image_base"]
    if config.provider == "siliconflow":
        return FluxImage(api_key=key, base_url=base)
    return QwenImage(api_key=key, base_url=base)


def config_for_provider(provider: str) -> ProjectConfig:
    """new 命令用：按渠道生成默认配置（provider + 渠道默认模型）。"""
    if provider not in CHANNELS:
        raise ValueError(f"未知渠道: {provider}")
    defaults = CHANNELS[provider]["defaults"]
    return ProjectConfig(
        llm=LLMConfig(provider=provider, model=defaults["llm"]),
        image=ImageConfig(provider=provider, model=defaults["image"]),
    )
