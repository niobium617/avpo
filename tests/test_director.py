"""M1-2.7 分镜生成测试：mock DeepSeek chat client。完全离线。"""

import json

import pytest

from app.core.state import FatalError
from app.director.director import COST_PER_M_IN, COST_PER_M_OUT, Director

TEXT = "AI 正在改变内容创作的方式。现在，一个人也能做视频。"


def scenes_content(scenes: list[dict]) -> str:
    return json.dumps({"scenes": scenes}, ensure_ascii=False)


VALID = scenes_content([
    {
        "scene_id": "s1",
        "narration": "AI 正在改变内容创作的方式。",
        "visual": "都市夜景，数字光效",
        "image_prompt": "cinematic city night, digital light",
        "motion": "zoom_in_slow",
    },
    {
        "scene_id": "s2",
        "narration": "现在，一个人也能做视频。",
        "visual": "一人对着电脑，屏幕发光",
        "image_prompt": "one person at desk, glowing screen",
        "motion": "pan_left",
    },
])


class _Usage:
    prompt_tokens = 100
    completion_tokens = 50


class _Message:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Message(content)


class _Resp:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class _FakeCompletions:
    """outcomes: 按调用顺序排队；Exception = 抛错，其余为 _Resp。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("API 调用次数超过预期")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeChat:
    def __init__(self, outcomes):
        self.completions = _FakeCompletions(outcomes)


class _FakeClient:
    def __init__(self, outcomes):
        self.chat = _FakeChat(outcomes)


def make_director(outcomes) -> tuple[Director, _FakeCompletions]:
    director = Director(api_key="test-key")
    fake = _FakeCompletions(outcomes)
    director.client = _FakeClient([])
    director.client.chat.completions = fake
    return director, fake


def test_storyboard_happy_path():
    director, fake = make_director([_Resp(VALID)])
    scenes, cost = director.storyboard(TEXT)

    assert [s.scene_id for s in scenes] == ["s1", "s2"]
    assert scenes[0].motion == "zoom_in_slow"
    # LLM 不该控制的字段被清空
    assert all(s.status == "pending" and s.image_asset_id is None and s.cost == {} for s in scenes)
    # 强制 JSON 模式
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    # 成本 = (100*2.1 + 50*8.3)/1e6
    assert cost == pytest.approx((100 * COST_PER_M_IN + 50 * COST_PER_M_OUT) / 1e6)


def test_narration_mismatch_retries_with_feedback():
    bad = scenes_content([
        {"scene_id": "s1", "narration": "AI 正在改变内容创作。", "visual": "v", "image_prompt": "i", "motion": "none"},
        {"scene_id": "s2", "narration": "现在，一个人也能做视频。", "visual": "v", "image_prompt": "i", "motion": "none"},
    ])
    director, fake = make_director([_Resp(bad), _Resp(VALID)])
    scenes, _ = director.storyboard(TEXT)

    assert len(scenes) == 2 and len(fake.calls) == 2
    # 第二次调用带上了校验错误反馈
    feedback = fake.calls[1]["messages"][-1]["content"]
    assert "校验失败" in feedback


def test_storyboard_fails_after_retry():
    bad = scenes_content([
        {"scene_id": "s1", "narration": "只写了一部分。", "visual": "v", "image_prompt": "i", "motion": "none"},
    ])
    director, fake = make_director([_Resp(bad), _Resp(bad)])
    with pytest.raises(FatalError, match="校验失败"):
        director.storyboard(TEXT)
    assert len(fake.calls) == 2


def test_invalid_json_retries():
    director, fake = make_director([_Resp("not json at all"), _Resp(VALID)])
    scenes, _ = director.storyboard(TEXT)
    assert len(scenes) == 2 and len(fake.calls) == 2


def test_duplicate_scene_id_rejected():
    dup = scenes_content([
        {"scene_id": "s1", "narration": "AI 正在改变内容创作的方式。", "visual": "v", "image_prompt": "i", "motion": "none"},
        {"scene_id": "s1", "narration": "现在，一个人也能做视频。", "visual": "v", "image_prompt": "i", "motion": "none"},
    ])
    director, fake = make_director([_Resp(dup), _Resp(VALID)])
    scenes, _ = director.storyboard(TEXT)
    assert [s.scene_id for s in scenes] == ["s1", "s2"]


def test_illegal_motion_rejected():
    bad = scenes_content([
        {"scene_id": "s1", "narration": "AI 正在改变内容创作的方式。", "visual": "v", "image_prompt": "i", "motion": "旋转"},
        {"scene_id": "s2", "narration": "现在，一个人也能做视频。", "visual": "v", "image_prompt": "i", "motion": "none"},
    ])
    director, fake = make_director([_Resp(bad), _Resp(VALID)])
    scenes, _ = director.storyboard(TEXT)
    assert scenes[0].motion == "zoom_in_slow"


def test_empty_text_rejected():
    director, _ = make_director([])
    with pytest.raises(ValueError, match="文案为空"):
        director.storyboard("   ")
