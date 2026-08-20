"""M1-2.2/2.3 单元测试：edge-tts 适配器（mock 外部 API）+ 字幕聚合。

完全离线：edge_tts.Communicate 被替换为假实现，mp3 用 M0 同款单帧静音帧拼装。
"""

import asyncio
import re

import edge_tts
import pytest
from mutagen.mp3 import MP3

from app.tts.subs import aggregate, build_subtitles
from app.tts.tts_edge import EdgeTTS
from app.tts.base import TTSWord

# 极简 MP3：MPEG-1 Layer III 帧头 + 静音（128kbps @ 44100Hz）
# 帧长须与头声明一致：144*128000/44100 = 417 字节，否则 mutagen 跳帧失步
_MP3_FRAME = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"\x00" * 413
_N_FRAMES = 20
# MPEG-1 Layer III 每帧 1152 采样 @ 44100Hz → mutagen 按帧数计时长
_EXPECT_MS = round(_N_FRAMES * 1152 / 44100 * 1000)


def word(text: str, start_ms: int, end_ms: int) -> TTSWord:
    return TTSWord(text=text, start_ms=start_ms, end_ms=end_ms)


def char_words(text: str, step_ms: int = 100, dur_ms: int = 80) -> list[TTSWord]:
    """逐字词流，时间连续不重叠。"""
    return [word(ch, i * step_ms, i * step_ms + dur_ms) for i, ch in enumerate(text)]


def norm(text: str) -> str:
    """去全部空白 —— 覆盖全文断言用。"""
    return re.sub(r"\s+", "", text)


# ---------------------------------------------------------------- 2.2 edge 适配器

class _FakeCommunicate:
    """假 edge_tts.Communicate：按构造时给定的 chunks 回放。"""

    def __init__(self, text: str, voice: str, rate: str = "+0%", chunks: list[dict] | None = None):
        self.text, self.voice, self.rate = text, voice, rate
        self.chunks = chunks or []

    async def stream(self):
        for chunk in self.chunks:
            yield chunk


@pytest.fixture()
def fake_edge(monkeypatch):
    """替换 edge_tts.Communicate。测试先写 state["chunks"]，再调 synth 即可注入。"""
    state: dict = {"chunks": []}

    def factory(text, voice, rate="+0%", boundary="WordBoundary"):
        return _FakeCommunicate(text, voice, rate, chunks=state["chunks"])

    monkeypatch.setattr(edge_tts, "Communicate", factory)
    return state


def _audio_chunks() -> list[dict]:
    return [{"type": "audio", "data": _MP3_FRAME * _N_FRAMES}]


def _wb(text: str, offset_100ns: int, dur_100ns: int) -> dict:
    return {
        "type": "WordBoundary",
        "text": text,
        "offset": offset_100ns,
        "duration": dur_100ns,
    }


def test_edge_synth_happy_path(tmp_path, fake_edge):
    mp3 = tmp_path / "vo.mp3"
    tts = EdgeTTS()

    fake_edge["chunks"] = [
        *_audio_chunks(),
        _wb("你", 0, 50_000),
        _wb("好", 50_000, 50_000),
        _wb("世", 100_000, 50_000),
        _wb("界", 150_000, 50_000),
    ]
    result = asyncio.run(tts.synth("你好世界", mp3))

    assert result.mp3 == mp3 and mp3.is_file()
    assert result.duration_ms == pytest.approx(_EXPECT_MS, abs=5)
    # 100ns → ms
    assert result.words == [
        word("你", 0, 5), word("好", 5, 10), word("世", 10, 15), word("界", 15, 20)
    ]


def test_edge_synth_empty_audio_raises(tmp_path, fake_edge):
    fake_edge["chunks"] = []        # 完全无输出
    with pytest.raises(RuntimeError, match="0 字节"):
        asyncio.run(EdgeTTS().synth("你好", tmp_path / "vo.mp3"))


def test_edge_synth_no_word_boundary_raises(tmp_path, fake_edge):
    fake_edge["chunks"] = _audio_chunks()
    with pytest.raises(RuntimeError, match="WordBoundary"):
        asyncio.run(EdgeTTS().synth("你好", tmp_path / "vo.mp3"))


# ---------------------------------------------------------------- 2.3 字幕聚合

def test_split_at_hard_punct():
    words = char_words("今天天气很好。我们出去吧。")
    segs = aggregate(words)
    assert [s.text for s in segs] == ["今天天气很好。", "我们出去吧。"]
    assert segs[1].start_ms >= segs[0].end_ms          # 不重叠


def test_max_chars_cap_without_punct():
    words = char_words("一二三四五六七八九十一二三四五六七八九十" + "一二三四五六七八九十")
    segs = aggregate(words, max_chars=18)
    assert all(norm(s.text) and len(norm(s.text)) <= 18 for s in segs)
    assert len(segs) == 2


def test_prefer_soft_punct_at_cap():
    # 10 字处有逗号，20 字触发 18 上限 → 应在逗号后断
    text = "一二三四五六七八九，一二三四五六七八九十"
    segs = aggregate(char_words(text), max_chars=18)
    assert segs[0].text == "一二三四五六七八九，"
    assert segs[1].text == "一二三四五六七八九十"


def test_tail_too_short_merges():
    words = char_words("很长的一段话。" , step_ms=100, dur_ms=90) + \
        char_words("好。", step_ms=100, dur_ms=10)
    # 尾段仅 ~10ms < 500ms → 并入前段
    segs = aggregate(words)
    assert [s.text for s in segs] == ["很长的一段话。好。"]


def test_invariants_hold_on_mixed_text():
    """不重叠 + 覆盖全文 + 无截断词（随机组合的混合标点文本）。"""
    text = ("首先，我们要准备素材；然后，写出口播文案。接着生成配音和配图！"
            "最后一步：一键导出剪映草稿，完成视频制作……你准备好了吗？")
    words = char_words(text, step_ms=120, dur_ms=100)
    segs = aggregate(words, max_chars=18)

    # 覆盖全文
    assert norm("".join(s.text for s in segs)) == norm(text)
    # 不重叠且时间单调
    for a, b in zip(segs, segs[1:]):
        assert b.start_ms >= a.end_ms
    # 无截断词：每个词恰好属于一段
    word_texts = [w.text for w in words]
    seg_word_texts = [w.text for s in segs for w in s.words]
    assert seg_word_texts == word_texts
    # 18 字上限（除末段合并导致的超限外）
    assert all(len(norm(s.text)) <= 19 for s in segs)


def test_build_subtitles_binds_scene():
    # 尾句 7 词 ≈ 680ms ≥ 500ms，不触发尾段合并
    subtitles = build_subtitles(char_words("你好世界。再见了朋友们。"), scene_id="s1")
    assert len(subtitles) == 2
    assert all(s.scene_id == "s1" for s in subtitles)
    assert subtitles[0].text == "你好世界。"
    assert subtitles[0].start_ms == 0
    assert subtitles[0].end_ms == 4 * 100 + 80     # 第 5 个词「。」的结束
    assert subtitles[1].start_ms == 500
    assert subtitles[1].start_ms >= subtitles[0].end_ms
