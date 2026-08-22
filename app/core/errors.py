"""错误分类与可读化（IMPLEMENTATION_PLAN 4.3）。

三类错误各配修复动作：
- API 类（余额 / 限流 / 网络）—— 上游问题，可重试或充钱/换渠道；
- 格式类（JSON / schema 校验）—— 改输入重跑；
- 剪映类（草稿/素材/导出）—— 本地环境问题。

classify(exc) 按异常类型名 + 消息关键词归入 ErrorKind（消息匹配大小写不敏感）。
state.run_task 失败落盘时把分类写入 PipelineError.kind/hint（schema 扩展字段，
老项目 JSON 兼容）；CLI 失败时 describe_list 打印"分类 + 修复动作"。
重试语义仍归状态机（TransientError 重试 / FatalError 不重试），本模块只管可读化。
"""

from enum import Enum

from app.core.schema import PipelineError


class ErrorKind(str, Enum):
    API_BALANCE = "API 余额/密钥"
    API_RATE_LIMIT = "接口限流"
    API_NETWORK = "网络/服务不可达"
    FORMAT = "格式校验"
    JIANYING = "剪映草稿"
    UNKNOWN = "未分类"


HINTS: dict[ErrorKind, str] = {
    ErrorKind.API_BALANCE: "检查 API key 是否正确/未过期，或给渠道充值，或 --provider 换渠道",
    ErrorKind.API_RATE_LIMIT: "稍后重试（avpo run 已自动重试 x3）；连续失败请减少场景数",
    ErrorKind.API_NETWORK: "检查网络/代理（avpo run 已自动重试 x3）",
    ErrorKind.FORMAT: "检查输入文案与模型输出；改输入后重跑",
    ErrorKind.JIANYING: "检查剪映安装与草稿目录；重新导出或升级剪映",
    ErrorKind.UNKNOWN: "查看完整错误日志后重试；仍失败请带日志反馈",
}

# 匹配顺序重要：rate limit 先于 balance（"rate limit exceeded" 不是欠费）
_RATE_MARKERS = ("429", "rate limit", "too many requests", "throttl")
_BALANCE_MARKERS = (
    "insufficient", "balance", "quota", "no credit", "payment", "充值", "余额",
    "401", "403", "invalid api key", "authentication", "unauthorized",
)
_NETWORK_MARKERS = (
    "timeout", "timed out", "connection", "socket", "refused", "unreachable",
    "resolve", "network", "noaudioreceived",
)
_JIANYING_MARKERS = ("jianying", "剪映", "draft", "草稿", "pyjianyingdraft")
_FORMAT_MARKERS = (
    "json", "校验", "schema", "解析", "narration", "scene_id", "validate",
    "decode", "storyboard",
)

_ORDERED = (
    (ErrorKind.API_RATE_LIMIT, _RATE_MARKERS),
    (ErrorKind.API_BALANCE, _BALANCE_MARKERS),
    (ErrorKind.API_NETWORK, _NETWORK_MARKERS),
    (ErrorKind.JIANYING, _JIANYING_MARKERS),
    (ErrorKind.FORMAT, _FORMAT_MARKERS),
)


def classify(exc: Exception) -> tuple[ErrorKind, str]:
    """(错误分类, 修复动作)。抛错方显式 hint 优先于关键词猜测。"""
    hint = getattr(exc, "hint", None)
    if hint:
        return ErrorKind.UNKNOWN, str(hint)
    text = f"{type(exc).__name__}: {exc}".lower()
    for kind, markers in _ORDERED:
        if any(m in text for m in markers):
            return kind, HINTS[kind]
    return ErrorKind.UNKNOWN, HINTS[ErrorKind.UNKNOWN]


def describe_list(errors: list[PipelineError]) -> str:
    """CLI 展示用：每个错误 = [分类] 详情（修复动作），分号连接。"""
    parts = []
    for e in errors:
        seg = f"[{e.kind or ErrorKind.UNKNOWN.value}] {e.error}"
        if e.hint:
            seg += f"（修复: {e.hint}）"
        parts.append(seg)
    return "；".join(parts)
