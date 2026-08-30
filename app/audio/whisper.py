"""本地语音转写（M10）—— 用户自带音频 → 字幕的唯一来源。

faster-whisper（CTranslate2 CPU int8 推理）：模型文件下载到 F 盘缓存目录
（<数据目录>/whisper_models，可用 AVPO_WHISPER_CACHE 环境变量覆盖 —— 绝不写
C 盘用户目录；国内网络可设 HF_ENDPOINT=https://hf-mirror.com 走镜像）。

缓存 sidecar assets/whisper_<scene_id>.json：音频文件 sha256 + 模型 + 语言
三键校验，命中即跳过推理 —— 断点续跑语义与 TTS sidecar（app/tts/cache.py）
一致：音频换了缓存自动失效，模型/语言配置变了同样重新转写。

模型懒加载单例：同一（模型, 缓存目录）进程内只加载一次 —— 一个项目内逐场景
转写共享同一份模型内存，重跑 transcribe 不重复加载。
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.core.schema import WhisperModelKind

WHISPER_MODELS = tuple(WhisperModelKind.__args__)   # tiny/base/small/medium/large-v3
DEFAULT_MODEL = "small"      # 中文口播精度/速度平衡点（base 更快、medium 更准，均可换）
DEFAULT_LANGUAGE = "zh"      # 中文项目默认；"" = whisper 自动检测
# 简繁归一（OpenCC t2s）：whisper 训练语料简繁混杂，小模型输出会简繁混用
# （实测 small 把「变/内/创」识别成「變/內/創」）—— 转写后统一转简体；
# t2s 对非中文文本是空操作。台/港繁体项目可改为 False（单点可调）。
SIMPLIFY_CHINESE = True

# (model, download_root) → WhisperModel 懒加载单例（进程内共享）
_MODELS: dict[tuple[str, str], object] = {}

# OpenCC 惰性加载（首次转写才初始化，CLI 冷启动不背字典文件）
_t2s = None


def _to_simplified(text: str) -> str:
    """繁体 → 简体（OpenCC t2s）；开关关闭时原样返回。"""
    global _t2s
    if not SIMPLIFY_CHINESE:
        return text
    if _t2s is None:
        from opencc import OpenCC
        _t2s = OpenCC("t2s")
    return _t2s.convert(text)


class WhisperModelError(ValueError):
    """模型缺失/下载失败 —— 网络类问题（pipeline 映射为 TransientError 重试）。"""


class WhisperDecodeError(ValueError):
    """音频不可解码/推理失败 —— 格式类问题（pipeline 映射为 FatalError 不重试）。"""


@dataclass
class WhisperSegment:
    """一段转写结果：毫秒级起止 + 文本（导出字幕的输入格式）。"""

    start_ms: int
    end_ms: int
    text: str


def model_cache_dir(data_dir: Path | str) -> Path:
    """模型缓存目录：AVPO_WHISPER_CACHE 优先（跨项目共享/自定义位置），
    否则 <数据目录>/whisper_models（随数据目录走，F 盘）。"""
    env = os.environ.get("AVPO_WHISPER_CACHE")
    if env:
        return Path(env)
    return Path(data_dir) / "whisper_models"


def _redirect_hub_cache(download_root: Path) -> None:
    """huggingface_hub 缓存重定向：不写 C 盘用户目录（~/.cache/huggingface）。

    setdefault —— 用户显式设置的 HF_HOME/HF_ENDPOINT（如镜像）不被覆盖。
    """
    os.environ.setdefault("HF_HOME", str(download_root))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(download_root / "hub"))


def get_model(model: str, download_root: Path) -> object:
    """加载 whisper 模型（懒加载单例，进程内同键只加载一次）。

    Raises:
        WhisperModelError: 模型下载失败（网络/未缓存且离线）。
    """
    key = (model, str(download_root))
    if key in _MODELS:
        return _MODELS[key]
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model

    _redirect_hub_cache(download_root)
    try:
        model_dir = download_model(model, cache_dir=download_root)
    except Exception:  # noqa: BLE001 —— 网络/离线/仓库缺失都归为模型下载失败
        # 兜底：缓存已完整时离线加载（断网/代理抖动也能用已下载的模型）
        try:
            model_dir = download_model(model, cache_dir=download_root, local_files_only=True)
        except Exception as exc:  # noqa: BLE001
            raise WhisperModelError(
                f"whisper 模型 {model} 下载失败: {exc}",
            ) from exc
    _MODELS[key] = WhisperModel(model_dir, device="cpu", compute_type="int8")
    return _MODELS[key]


def transcribe_audio(
    path: Path,
    model: str,
    language: str,
    download_root: Path,
) -> list[WhisperSegment]:
    """转写音频为字幕段（language="" = whisper 自动检测语言）。

    Raises:
        WhisperModelError: 模型下载失败（原样上抛，调用方映射重试语义）。
        WhisperDecodeError: 文件缺失/不可解码/推理失败。
    """
    path = Path(path)
    if not path.is_file():
        raise WhisperDecodeError(f"音频文件缺失: {path}")
    try:
        m = get_model(model, download_root)
        segments, _info = m.transcribe(str(path), language=language or None)
        return [
            WhisperSegment(
                start_ms=round(seg.start * 1000),
                end_ms=round(seg.end * 1000),
                text=_to_simplified(seg.text.strip()),
            )
            for seg in segments
            if seg.text.strip()
        ]
    except WhisperModelError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 解码/推理错误统一为格式类
        raise WhisperDecodeError(f"音频转写失败: {path}（{exc}）") from exc


# ---------------------------------------------------------------- sidecar 缓存

def audio_sha256(path: Path) -> str:
    """音频文件内容哈希（16 位 hex）—— 缓存键：文件换了缓存自动失效。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def cache_path(assets_dir: Path, scene_id: str) -> Path:
    return Path(assets_dir) / f"whisper_{scene_id}.json"


def load_whisper_cache(
    assets_dir: Path,
    scene_id: str,
    audio_hash: str,
    model: str,
    language: str,
) -> list[WhisperSegment] | None:
    """读转写缓存；文件缺失或音频/模型/语言任一不符返回 None（视为无缓存）。"""
    path = cache_path(assets_dir, scene_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if (
        data.get("audio_hash") != audio_hash
        or data.get("model") != model
        or data.get("language") != language
    ):
        return None
    segments = data.get("segments", [])
    if not isinstance(segments, list) or not segments:
        return None
    return [
        WhisperSegment(start_ms=s["start_ms"], end_ms=s["end_ms"], text=s["text"])
        for s in segments
        if isinstance(s, dict) and "text" in s
    ]


def save_whisper_cache(
    assets_dir: Path,
    scene_id: str,
    audio_hash: str,
    model: str,
    language: str,
    segments: list[WhisperSegment],
) -> Path:
    """写 sidecar 缓存（音频/模型/语言三键校验，任一变化自动失效）。"""
    path = cache_path(assets_dir, scene_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "audio_hash": audio_hash,
                "model": model,
                "language": language,
                "segments": [
                    {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text} for s in segments
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
