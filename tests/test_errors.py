"""M3-4.3 错误分类与可读化测试：API(余额/限流/网络) vs 格式 vs 剪映，各带修复动作。"""

import json

from app.core.errors import ErrorKind, HINTS, classify, describe_list
from app.core.schema import PipelineError, Project
from app.core.state import FatalError, run_task


def test_balance_error_classified():
    kind, hint = classify(Exception("InsufficientBalance: 账户余额不足，请充值"))
    assert kind == ErrorKind.API_BALANCE
    assert "充值" in hint


def test_rate_limit_classified_before_balance():
    """'rate limit exceeded' 是限流不是欠费 —— 顺序敏感。"""
    kind, _ = classify(Exception("429 Too Many Requests: rate limit exceeded"))
    assert kind == ErrorKind.API_RATE_LIMIT


def test_network_error_classified():
    kind, _ = classify(TimeoutError("timed out after 60s"))
    assert kind == ErrorKind.API_NETWORK
    kind2, _ = classify(ConnectionError("Connection refused"))
    assert kind2 == ErrorKind.API_NETWORK


def test_format_error_classified():
    kind, _ = classify(json.JSONDecodeError("Expecting value", "", 0))
    assert kind == ErrorKind.FORMAT
    kind2, _ = classify(ValueError("narration 拼接与原文不一致"))
    assert kind2 == ErrorKind.FORMAT


def test_jianying_error_classified():
    kind, _ = classify(Exception("pyJianYingDraft: draft_content.json 无法写入"))
    assert kind == ErrorKind.JIANYING
    assert "剪映" in HINTS[kind]


def test_unknown_error_defaults():
    kind, hint = classify(Exception("怪异的错误"))
    assert kind == ErrorKind.UNKNOWN
    assert hint  # 有默认修复提示


def test_explicit_hint_wins_over_keyword_guess():
    """抛错方显式 hint 优先于关键词猜测。"""
    kind, hint = classify(FatalError("429 限流", hint="换一个时间段再试"))
    assert hint == "换一个时间段再试"
    # 关键词分类时（无显式 hint），429 仍归限流
    kind2, _ = classify(FatalError("429 限流"))
    assert kind2 == ErrorKind.API_RATE_LIMIT


def test_failed_node_persists_kind_and_hint(store):
    """run_task 失败落盘：PipelineError.kind/hint 可被 status/cost 读到。"""
    project = Project(project_id="p_err")
    store.create(project)

    def fn(p: Project) -> None:
        raise TimeoutError("连接超时")

    assert run_task(store, project, "direct", fn) is False
    err = store.load("p_err").errors[0]
    assert err.kind == ErrorKind.API_NETWORK.value
    assert err.hint and "网络" in err.hint


def test_describe_list_human_readable():
    errors = [
        PipelineError(node="direct", error="余额不足", kind="API 余额/密钥", hint="充值"),
        PipelineError(node="export", error="草稿损坏"),
    ]
    text = describe_list(errors)
    assert "[API 余额/密钥]" in text and "（修复: 充值）" in text
    assert "[未分类]" in text
