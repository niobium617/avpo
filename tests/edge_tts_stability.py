"""edge-tts 稳定性实测（IMPLEMENTATION_PLAN 2.1）。

连续合成 20 条文案（不同长度/标点/中英混排），记录：
- 失败率（异常 / 空音频 / 无 WordBoundary 事件）
- 时间戳质量（首词偏移、尾词与音频时长的缝隙、重叠、单调性）

用法：
    python tests/edge_tts_stability.py

产物：
- mp3 写入临时目录（同 tests/conftest.py 约定：优先 F 盘，退回系统 temp）
- 汇总报告写入 tests/edge_tts_log.md（提交仓库，机器无关）

定版判定：失败率 0 且时间戳全部合规 → edge-tts 定版；
否则按计划切豆包 TTS（app/tts/tts_volc.py，同协议）。
"""

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts
from mutagen.mp3 import MP3

VOICE = "zh-CN-YunxiNeural"
RATE = "+0%"

# 覆盖维度：超短 / 短 / 中 / 长 / 超长；。！？，；、……引号 数字 英文 混排
SENTENCES = [
    "你好呀。",
    "开始工作。",
    "太好了！",
    "真的吗？",
    "今天天气很好，我们出去走走吧。",
    "先准备素材；再生成配音；最后合成视频。",
    "北京、上海、广州和深圳都是超一线城市。",
    "2026年8月20日，AI行业发布了三款新产品。",
    "这个功能叫 AI Copilot，速度提升了百分之五十。",
    "他说：\"坚持就是胜利。\"",
    "人工智能正在改变内容创作的方式，一个人也能完成从文案到视频的全流程制作。",
    "首先，你要确定选题；然后，写出口播文案；接着，生成配音和配图；最后，一键导出剪映草稿。",
    "在过去的几年里，人工智能技术经历了飞速的发展，从简单的图像识别到复杂的自然语言处理，再到今天的视频生成，每一项突破都在重新定义我们与机器的协作方式。",
    "太神奇了！难道机器真的能理解人类的语言吗？",
    "其实……这个方案还有很大的改进空间……",
    "嗯，让我想想，这个问题应该怎么回答呢。",
    "OpenAI 的 GPT 模型、Google 的 Gemini 以及 DeepSeek，都在 2026 年推出了新版本。",
    "你好。欢迎来到 AVPO。这是一个 AI 视频工作流系统。希望你喜欢。",
    "准备好了吗？好！那我们开始吧：第一步，打开电脑；第二步，运行命令；第三步，等待结果。",
    "想象一下，在未来的某一天，你只需要告诉 AI 你想要一个什么样的视频，它就能在几分钟之内自动完成文案撰写、语音合成、图片生成、字幕制作和视频剪辑的全部工作，而你要做的，仅仅是检查一下最终结果是否符合预期。",
]

# 时间戳质量阈值（毫秒）
MAX_FIRST_OFFSET = 3000    # 首词出现过晚 → 片头空白过长
MAX_TAIL_GAP = 1500        # 尾词结束到音频结尾的缝隙上限
OVERLAP_TOLERANCE = 20     # 允许的浮点误差


@dataclass
class Entry:
    index: int
    text: str
    chars: int
    ok: bool = True
    attempts: int = 1            # 实际尝试次数（含重试）
    error: str = ""
    duration_ms: float = 0.0
    words: int = 0
    first_ms: float | None = None
    last_end_ms: float | None = None
    anomalies: list[str] = field(default_factory=list)


def _pick_tmp_root() -> Path:
    """同 tests/conftest.py：优先 F 盘，退回系统临时目录。"""
    for candidate in (Path("F:/tmp/avpo-pytest"),):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    fallback = Path(tempfile.gettempdir()) / "avpo-pytest"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


async def synth_one(index: int, text: str, out_dir: Path) -> Entry:
    """合成一条，最多 3 次尝试（1s/2s 退避）—— 与管线状态机重试策略一致。"""
    entry = Entry(index=index, text=text, chars=len(text))
    mp3_path = out_dir / f"edge_tts_stability_{index:02d}.mp3"

    for attempt in range(1, 4):
        entry.attempts = attempt
        try:
            words: list[tuple[float, float, str]] = []  # (start_ms, end_ms, word)
            # boundary 必须显式指定：默认 SentenceBoundary 没有词级时间戳
            communicate = edge_tts.Communicate(
                text, VOICE, rate=RATE, boundary="WordBoundary"
            )
            with open(mp3_path, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / 10000      # 100ns → ms
                        end = (chunk["offset"] + chunk["duration"]) / 10000
                        words.append((start, end, chunk["text"]))
        except Exception as exc:  # noqa: BLE001 —— 任何失败都要记录
            if attempt < 3:
                await asyncio.sleep(2 ** (attempt - 1))     # 1s, 2s
                continue
            entry.ok = False
            entry.error = f"{type(exc).__name__}: {exc}"
            return entry

        if mp3_path.stat().st_size == 0:
            if attempt < 3:
                await asyncio.sleep(2 ** (attempt - 1))
                continue
            entry.ok = False
            entry.error = "mp3 为空"
            return entry
        if not words:
            if attempt < 3:
                await asyncio.sleep(2 ** (attempt - 1))
                continue
            entry.ok = False
            entry.error = "无 WordBoundary 事件"
            return entry
        break

    entry.duration_ms = MP3(mp3_path).info.length * 1000
    entry.words = len(words)
    entry.first_ms = words[0][0]
    entry.last_end_ms = words[-1][1]

    # ---- 时间戳质量检查 ----
    if entry.first_ms > MAX_FIRST_OFFSET:
        entry.anomalies.append(f"首词偏移 {entry.first_ms:.0f}ms > {MAX_FIRST_OFFSET}ms")
    tail_gap = entry.duration_ms - entry.last_end_ms
    if tail_gap > MAX_TAIL_GAP:
        entry.anomalies.append(f"尾词缝隙 {tail_gap:.0f}ms > {MAX_TAIL_GAP}ms")
    if tail_gap < -50:  # 尾词结束晚于音频 50ms 以上 → 截断
        entry.anomalies.append(f"尾词超出音频 {tail_gap:.0f}ms（疑似截断）")
    for i in range(1, len(words)):
        if words[i][0] < words[i - 1][1] - OVERLAP_TOLERANCE:
            entry.anomalies.append(
                f"词重叠 @{i}: \"{words[i-1][2]}\" 止于 {words[i-1][1]:.0f}ms, "
                f"\"{words[i][2]}\" 始于 {words[i][0]:.0f}ms"
            )
        if words[i][0] < words[i - 1][0]:
            entry.anomalies.append(f"时间戳非单调 @{i}")
    if entry.anomalies:
        entry.ok = False
    return entry


def render_markdown(entries: list[Entry], elapsed_s: float) -> str:
    lines = [
        "# edge-tts 稳定性实测报告（M1-2.1）",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 版本：edge-tts 7.2.8（钉版 docs/versions.md）",
        f"- 声音：{VOICE}，rate {RATE}",
        f"- 样本：{len(entries)} 条（长度 {min(e.chars for e in entries)}~{max(e.chars for e in entries)} 字，标点/数字/英文/引号/省略号全覆盖）",
        f"- 总耗时：{elapsed_s:.0f}s（含网络请求）",
        "- mp3 产物：测试临时目录（同 tests/conftest.py 约定），不提交",
        "",
        "| # | 字数 | 状态 | 尝试 | 音频时长(ms) | 词数 | 首词(ms) | 尾词(ms) | 缝隙(ms) | 异常 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in entries:
        tail = f"{e.duration_ms - e.last_end_ms:.0f}" if e.last_end_ms is not None else "-"
        first = f"{e.first_ms:.0f}" if e.first_ms is not None else "-"
        last = f"{e.last_end_ms:.0f}" if e.last_end_ms is not None else "-"
        lines.append(
            f"| {e.index:02d} | {e.chars} | {'✅' if e.ok else '❌'} | {e.attempts} | "
            f"{e.duration_ms:.0f} | {e.words} | "
            f"{first} | {last} | {tail} | "
            f"{'; '.join(e.anomalies) + e.error if e.anomalies or e.error else ''} |"
        )

    failed = [e for e in entries if not e.ok]
    retried = [e for e in entries if e.ok and e.attempts > 1]
    raw_failed = len(failed) + len(retried)
    lines += [
        "",
        f"**最终失败率：{len(failed)}/{len(entries)}**"
        f"（原始失败率 {raw_failed}/{len(entries)}，其中 {len(retried)} 条经重试恢复）",
        "",
    ]
    if failed:
        lines.append("## 失败明细")
        for e in failed:
            lines.append(f"- #{e.index:02d}（{e.chars}字）：{e.error or '; '.join(e.anomalies)}")
    else:
        retry_note = (
            f"原始失败率 {raw_failed}/{len(entries)} 全部由 ≤2 次重试恢复，"
            if retried else
            f"原始失败率 {raw_failed}/{len(entries)}，全部一次通过。"
        )
        lines += [
            "## 结论",
            "",
            f"最终失败率 0。{retry_note}"
            "时间戳全部合规（首词偏移 ≤3000ms、尾词缝隙 ≤1500ms、无重叠/截断/非单调）。",
            "",
            "**edge-tts 定版，但有两个条件写进实现（app/tts/tts_edge.py 已满足）：**",
            "1. 必须传 `boundary=\"WordBoundary\"` —— 7.2.8 默认 SentenceBoundary，无词级时间戳；",
            "2. 调用方必须有 ×3 重试（app/core/state.py 的 run_task 天然满足）—— 服务端存在瞬态 NoAudioReceived。",
            "",
            "M1 进入 2.2 TTS 协议实现。",
        ]
    return "\n".join(lines) + "\n"


async def main() -> int:
    out_dir = _pick_tmp_root() / "edge_tts_stability"
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    entries: list[Entry] = []
    for i, text in enumerate(SENTENCES, 1):
        entry = await synth_one(i, text, out_dir)
        entries.append(entry)
        print(f"[{i:02d}/{len(SENTENCES)}] {'OK ' if entry.ok else 'FAIL'} "
              f"attempt={entry.attempts} {entry.chars}字 {entry.duration_ms:.0f}ms "
              f"{entry.error or ''}", flush=True)
        await asyncio.sleep(0.3)    # 条目间小间隔，避免突发触发服务端限流
    elapsed = time.monotonic() - started

    report = render_markdown(entries, elapsed)
    log_path = Path(__file__).with_name("edge_tts_log.md")
    log_path.write_text(report, encoding="utf-8")

    # 控制台只打 ASCII 摘要（GBK 终端打不出 ✅/❌），完整表格在报告文件里
    failed = [e for e in entries if not e.ok]
    print(f"done: {len(entries) - len(failed)}/{len(entries)} ok, "
          f"{len(failed)} failed, {elapsed:.0f}s")
    print(f"report: {log_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    os.environ.setdefault("TMP", str(_pick_tmp_root()))
    os.environ.setdefault("TEMP", str(_pick_tmp_root()))
    raise SystemExit(asyncio.run(main()))
