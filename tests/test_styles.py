"""M3-4.4 风格模板单元测试：加载/列表/字幕样式差异/未知模板报错。"""

import pytest
from pydantic import ValidationError

from app.core.schema import Brief, ProjectConfig, Scene
from app.core.styles import (
    DEFAULT_STYLE,
    compose_image_prompt,
    list_styles,
    load_style,
)

STYLE_IDS = ("fast_talk", "emotional", "explainer")


def test_default_builtin():
    s = load_style("default")
    assert s is DEFAULT_STYLE
    assert s.bgm is None
    assert s.subtitle_style.size == 6.0


def test_load_all_templates():
    for sid in STYLE_IDS:
        s = load_style(sid)
        assert s.id == sid
        assert s.name and s.motion_hint
        assert 0 < s.subtitle_style.size <= 10
        assert -1 <= s.subtitle_style.y <= 0


def test_list_styles_contains_default_and_templates():
    styles = list_styles()
    assert styles[0] == "default"
    assert set(STYLE_IDS) <= set(styles)


def test_unknown_style_raises():
    with pytest.raises(ValueError, match="未知风格模板"):
        load_style("no_such_style")


def test_templates_subtitle_styles_differ():
    """字幕样式确实按模板参数化（不是所有模板都等于 default）。"""
    styles = {sid: load_style(sid).subtitle_style for sid in STYLE_IDS}
    assert styles["explainer"].size != styles["emotional"].size
    assert styles["explainer"].y != DEFAULT_STYLE.subtitle_style.y


def test_emotional_bgm_configured():
    assert load_style("emotional").bgm == "assets/bgm.mp3"
    # 其余模板默认无 BGM
    assert load_style("fast_talk").bgm is None
    assert load_style("explainer").bgm is None


# ---------------------------------------------------------------- M6-7.2 提示词模块

def test_templates_carry_prompt_modules():
    """三个模板都有画风/光影/画质/景别模块。"""
    for sid in STYLE_IDS:
        s = load_style(sid)
        assert s.prompt_prefix and s.prompt_lighting and s.quality_suffix
        assert s.shot_size_hint


def test_template_unknown_key_rejected():
    """extra=forbid：模板 JSON 出现未定义键当场报错（与模型同 commit 防呆）。"""
    import json

    from app.core.styles import StyleTemplate, TEMPLATES_DIR

    path = TEMPLATES_DIR / "fast_talk.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["not_a_field"] = "oops"
    with pytest.raises(ValidationError):
        StyleTemplate(**data)


def test_compose_full_order():
    """拼装顺序：prefix → lighting → palette → character → 场景变量 → quality。"""
    style = load_style("fast_talk")
    brief = Brief(palette="冷灰 + 橙色火光", lighting="hard top light", character="络腮胡中年男")
    scene = Scene(scene_id="s1", image_prompt="a man standing in ruins")
    prompt = compose_image_prompt(style, brief, scene)
    assert prompt.startswith(style.prompt_prefix)
    assert "hard top light" in prompt            # brief.lighting 生效
    assert "冷灰 + 橙色火光" in prompt
    assert "络腮胡中年男" in prompt
    assert "a man standing in ruins" in prompt
    assert prompt.endswith(style.quality_suffix)


def test_compose_brief_lighting_overrides_template():
    """brief.lighting 覆盖模板光影；brief 缺光影时回退模板光影。"""
    style = load_style("emotional")
    scene = Scene(scene_id="s1", image_prompt="x")
    assert "soft golden hour lighting" in compose_image_prompt(style, Brief(), scene)
    assert "hard top light" in compose_image_prompt(
        style, Brief(lighting="hard top light"), scene
    )


def test_compose_legacy_no_brief():
    """旧项目（brief=None）退化为纯模板 + 场景变量，不炸。"""
    style = load_style("fast_talk")
    scene = Scene(scene_id="s1", image_prompt="x")
    prompt = compose_image_prompt(style, None, scene)
    assert prompt == f"{style.prompt_prefix}, {style.prompt_lighting}, x, {style.quality_suffix}"


def test_compose_default_style_degrades():
    """default 模板无模块时退化为仅场景变量（老项目兼容）。"""
    scene = Scene(scene_id="s1", image_prompt="x")
    assert compose_image_prompt(DEFAULT_STYLE, None, scene) == "x"
