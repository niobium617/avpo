"""M1-2.7 分镜生成测试：mock DeepSeek chat client。完全离线。"""

import json

import pytest

from app.core.schema import Brief, Scene
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


# ---------------------------------------------------------------- M6-7.3 brief/节奏/景别 + rewrite_scene

def test_brief_and_bgm_injected_into_system_prompt():
    """创作简报（含风格关键词包与 BGM 节奏）注入系统提示词。"""
    brief = Brief(
        theme="末日求生", art_style="实写末日", lighting="volumetric light from upper left",
        bgm_hint="重拍在第 3 秒，第 8 秒骤停",
    )
    director, fake = make_director([_Resp(VALID)])
    director.storyboard(TEXT, brief=brief)
    system = fake.calls[0]["messages"][0]["content"]
    assert "末日求生" in system and "实写末日" in system
    assert "volumetric light from upper left" in system
    assert "BGM 节奏" in system and "重拍在第 3 秒" in system


def test_shot_size_hint_and_new_fields_parsed():
    """景别指导注入；shot_size/planned_duration_ms/sfx 从合法 JSON 解析。"""
    content = scenes_content([
        {
            "scene_id": "s1",
            "narration": "AI 正在改变内容创作的方式。",
            "visual": "都市夜景，数字光效",
            "image_prompt": "cinematic city night",
            "motion": "zoom_in_slow",
            "shot_size": "特写",
            "planned_duration_ms": 2000,
            "sfx": "环境风声",
        },
        {
            "scene_id": "s2",
            "narration": "现在，一个人也能做视频。",
            "visual": "一人对着电脑",
            "image_prompt": "one person at desk",
            "motion": "pan_left",
            "shot_size": "中景",
            "planned_duration_ms": 1500,
            "sfx": "",
        },
    ])
    director, fake = make_director([_Resp(content)])
    scenes, _ = director.storyboard(TEXT, shot_size_hint="多用近景突出情绪")
    system = fake.calls[0]["messages"][0]["content"]
    assert "多用近景突出情绪" in system
    assert scenes[0].shot_size == "特写"
    assert scenes[0].planned_duration_ms == 2000
    assert scenes[0].sfx == "环境风声"
    assert scenes[1].shot_size == "中景" and scenes[1].sfx == ""


def test_storyboard_scrubs_candidate_and_end_frame():
    """LLM 输出的候选图/首尾帧字段一律清空（资产由管线负责）。"""
    content = scenes_content([
        {
            "scene_id": "s1",
            "narration": "AI 正在改变内容创作的方式。",
            "visual": "v", "image_prompt": "i", "motion": "none",
            "image_candidates": ["img_s1_v1"],
            "end_image_asset_id": "img_s1_end",
        },
        {
            "scene_id": "s2",
            "narration": "现在，一个人也能做视频。",
            "visual": "v", "image_prompt": "i", "motion": "none",
        },
    ])
    director, fake = make_director([_Resp(content)])
    scenes, _ = director.storyboard(TEXT)
    assert scenes[0].image_candidates == []
    assert scenes[0].end_image_asset_id is None
    assert all(s.status == "pending" and s.cost == {} for s in scenes)


def _rewrite_json(**overrides) -> str:
    data = {
        "scene": {
            "scene_id": "s2",
            "narration": "现在，一个人也能做视频。",
            "visual": "新画面：清晨城市",
            "image_prompt": "morning city, wide shot",
            "motion": "pan_left",
            "shot_size": "全景",
            "planned_duration_ms": 3000,
            "sfx": "",
        },
        **overrides,
    }
    return json.dumps(data, ensure_ascii=False)


def test_rewrite_scene_happy_path():
    scene = Scene(scene_id="s2", narration="现在，一个人也能做视频。", visual="旧", image_prompt="old")
    director, fake = make_director([_Resp(_rewrite_json())])
    new_scene, _ = director.rewrite_scene(scene, "改成清晨城市全景")
    assert new_scene.scene_id == "s2"
    assert new_scene.visual == "新画面：清晨城市"
    assert new_scene.narration == scene.narration     # 未要求改文案 → 保持
    assert new_scene.shot_size == "全景"
    assert new_scene.image_candidates == [] and new_scene.end_image_asset_id is None
    # 重写也走强制 JSON 模式
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


def test_rewrite_scene_injects_brief():
    scene = Scene(scene_id="s2", narration="x", visual="v", image_prompt="i")
    director, fake = make_director([_Resp(_rewrite_json())])
    director.rewrite_scene(scene, "改", brief=Brief(theme="末日", character="络腮胡中年男"))
    system = fake.calls[0]["messages"][0]["content"]
    assert "末日" in system and "络腮胡中年男" in system


def test_rewrite_scene_id_change_retries():
    bad = _rewrite_json(scene={"scene_id": "s9", "narration": "x", "visual": "v", "image_prompt": "i", "motion": "none"})
    director, fake = make_director([_Resp(bad), _Resp(_rewrite_json())])
    scene = Scene(scene_id="s2", narration="x", visual="v", image_prompt="i")
    new_scene, _ = director.rewrite_scene(scene, "改")
    assert new_scene.scene_id == "s2"
    feedback = fake.calls[1]["messages"][-1]["content"]
    assert "必须保持 s2" in feedback


def test_rewrite_scene_bad_json_retries():
    director, fake = make_director([_Resp("not json"), _Resp(_rewrite_json())])
    scene = Scene(scene_id="s2", narration="x", visual="v", image_prompt="i")
    new_scene, _ = director.rewrite_scene(scene, "改")
    assert new_scene.scene_id == "s2"
    assert len(fake.calls) == 2


def test_rewrite_scene_empty_instructions_rejected():
    director, _ = make_director([])
    scene = Scene(scene_id="s2", narration="x")
    with pytest.raises(ValueError, match="重写指令为空"):
        director.rewrite_scene(scene, "   ")
