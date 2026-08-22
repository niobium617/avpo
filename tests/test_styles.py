"""M3-4.4 风格模板单元测试：加载/列表/字幕样式差异/未知模板报错。"""

import pytest

from app.core.styles import DEFAULT_STYLE, list_styles, load_style

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
