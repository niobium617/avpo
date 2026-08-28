"""BGM 节拍检测（M8）—— 卡点剪辑的唯一节拍来源。

输入任意 miniaudio 可解码的音频（mp3/wav），输出节拍时间点（毫秒，升序）。
算法刻意保持简单、确定性（无随机/无机器学习），同文件同输出 —— 时间线
组装幂等依赖这一点：

1. miniaudio 解码 PCM（内置 dr_mp3/dr_wav，无 ffmpeg 依赖）；
2. 双声道平均为 mono → 帧 RMS 能量包络（FRAME_HOP 步进）；
3. onset = 包络正差分（能量突增 = 鼓点/重拍起音）；
4. 峰值检测：onset 高于自适应阈值（均值 + k·标准差）且为局部最大，
   相邻节拍间距 ≥ MIN_GAP_MS（音乐 BPM 上限约 240 对应 250ms）。

参数集中在本文件一处（与 app/core/motion.py 同约定：可审计可调）。
"""

from pathlib import Path

import miniaudio
import numpy as np

FRAME_HOP = 1024            # 包络帧步进（样本数）：44100Hz 下 ~23ms/帧，onset 分辨率足够
MIN_GAP_MS = 250            # 相邻节拍最小间距（音乐最快约 240 BPM）
THRESHOLD_K = 1.2           # onset 阈值 = mean + k * std（自适应；沉默音频无峰 → 空列表）


class BeatDetectionError(ValueError):
    """音频解码/格式问题 —— 卡点对齐的降级信号（BGM 属装饰，失败不阻塞主线）。"""


def detect_beats(path: Path) -> list[int]:
    """解码音频并检测节拍，返回节拍时间点（毫秒，升序）。

    Raises:
        BeatDetectionError: 文件不可解码或格式不支持。
    """
    try:
        info = miniaudio.decode_file(str(path))
    except (miniaudio.DecodeError, FileNotFoundError) as exc:
        raise BeatDetectionError(f"音频解码失败: {path}（{exc}）") from exc

    if info.sample_width != 2:
        raise BeatDetectionError(f"不支持的采样位宽: {info.sample_width} 字节（需 16-bit PCM）")
    rate = info.sample_rate
    pcm = np.frombuffer(info.samples, dtype=np.int16).astype(np.float64)
    if pcm.size == 0:
        return []
    if info.nchannels > 1:
        pcm = pcm.reshape(-1, info.nchannels).mean(axis=1)     # 双声道平均为 mono

    # 帧 RMS 包络（末帧不足一帧直接截断 —— 尾部残帧不影响节拍定位）
    n_frames = len(pcm) // FRAME_HOP
    if n_frames < 2:
        return []
    frames = pcm[: n_frames * FRAME_HOP].reshape(n_frames, FRAME_HOP)
    env = np.sqrt((frames ** 2).mean(axis=1))

    # onset = 包络正差分（能量突增）；负差分（衰减段）置 0
    onset = np.diff(env)
    onset = np.maximum(onset, 0.0)

    threshold = onset.mean() + THRESHOLD_K * onset.std()
    gap_frames = max(1, round(MIN_GAP_MS * rate / FRAME_HOP / 1000))

    beats: list[int] = []
    i = 0
    while i < onset.size:
        # 局部最大（等值平台取最左）+ 高于自适应阈值（含绝对下限：全静音时阈值≈0）
        if onset[i] > 0 and onset[i] >= threshold and (i == 0 or onset[i] > onset[i - 1]) and (
            i == onset.size - 1 or onset[i] >= onset[i + 1]
        ):
            center_ms = round((i + 0.5) * FRAME_HOP * 1000 / rate)
            beats.append(center_ms)
            i += gap_frames
        else:
            i += 1
    return beats
