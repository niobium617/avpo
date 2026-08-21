"""时间线组装（IMPLEMENTATION_PLAN 3.1）。

把 M1 产出的逐场景素材（vo_<scene_id>.mp3 + img_<scene_id>.png）组装成全局时间轴：
- scene 时长 = 对应配音段的实际时长（mutagen 读 mp3），场景依次首尾相接、无缝隙；
- 每个场景：视频轨 img clip（start=累计起点，duration=配音时长，运镜取 director 已分配的
  scene.motion）+ 音频轨 vo clip（offset=累计起点，duration_ms=配音时长）；
- scene.start_ms 记录全局起点 —— 字幕仍保持场景内相对时间戳（M1 产物），导出时按
  scene.start_ms 平移（app/export/jianying.py），单一真相源不破坏幂等；
- voiceover 顶层字段填汇总：duration_ms=总时长、status=done（整片 = 各段拼接）。

可重入：每次从头重建 timeline 与 start_ms（覆盖写），重复执行结果一致。
"""

from pathlib import Path

from mutagen.mp3 import MP3

from app.core.schema import AudioClip, Project, VideoClip
from app.core.state import FatalError


def build_timeline(project: Project, project_root: Path) -> None:
    """组装 project.timeline / scene.start_ms / voiceover 汇总。就地修改 project。

    Raises:
        FatalError: 场景为空、配音/图资产或文件缺失（带修复提示）。
    """
    if not project.scenes:
        raise FatalError("没有分镜可组装", hint="先跑 avpo direct 生成分镜")

    project_root = Path(project_root)
    video: list[VideoClip] = []
    voiceover: list[AudioClip] = []
    total_ms = 0

    for scene in project.scenes:
        vo_id = f"vo_{scene.scene_id}"
        img_id = f"img_{scene.scene_id}"

        vo_asset = project.assets.get(vo_id)
        if vo_asset is None:
            raise FatalError(
                f"scene {scene.scene_id} 缺少配音资产 {vo_id}",
                hint="先跑 avpo gen-assets 生成素材",
            )
        vo_path = project_root / vo_asset.path
        if not vo_path.is_file():
            raise FatalError(f"配音文件缺失: {vo_path}", hint="先跑 avpo gen-assets 重新生成")

        img_asset = project.assets.get(img_id)
        if img_asset is None:
            raise FatalError(
                f"scene {scene.scene_id} 缺少图资产 {img_id}",
                hint="先跑 avpo gen-assets 生成素材",
            )
        img_path = project_root / img_asset.path
        if not img_path.is_file():
            raise FatalError(f"图文件缺失: {img_path}", hint="先跑 avpo gen-assets 重新生成")

        duration_ms = _audio_duration_ms(vo_path)
        if duration_ms <= 0:
            raise FatalError(
                f"配音时长异常: {vo_path} = {duration_ms}ms",
                hint="先跑 avpo gen-assets 重新生成配音",
            )

        scene.start_ms = total_ms
        video.append(VideoClip(
            asset_id=img_id, start_ms=total_ms, duration_ms=duration_ms, motion=scene.motion,
        ))
        voiceover.append(AudioClip(asset_id=vo_id, offset_ms=total_ms, duration_ms=duration_ms))
        total_ms += duration_ms

    project.timeline.video = video
    project.timeline.voiceover = voiceover
    project.voiceover.duration_ms = total_ms
    project.voiceover.status = "done"


def _audio_duration_ms(path: Path) -> int:
    """mutagen 读 mp3 实际时长（秒 → 毫秒，四舍五入）。"""
    return round(MP3(path).info.length * 1000)
