"""时间线组装（IMPLEMENTATION_PLAN 3.1）。

把 M1 产出的逐场景素材（vo_<scene_id>.mp3 + 候选图中人审选中的一张）组装成全局时间轴：
- scene 时长 = 对应配音段的实际时长（mutagen 读 mp3），场景依次首尾相接、无缝隙；
- 每个场景：视频轨 img clip（asset_id = scene.image_asset_id —— M6-7.5 起为
  候选图 img_<scene_id>_vN 中选中的那张，start=累计起点，duration=配音时长，
  运镜取 director 已分配的 scene.motion，clip 挂 scene_id 供导出取运镜计划）
  + 音频轨 vo clip（offset=累计起点，duration_ms=配音时长）；
- M7-8.5 首尾帧尾拍：animate 节点解析出 scene.motion_plan 且 end_frame_ms > 0 时，
  在配音后追加一段静态尾拍 clip（asset_id = scene.end_image_asset_id，motion=none，
  时长 end_frame_ms，硬切 —— 转场属 M9）；下一场景 start_ms 顺延尾拍时长，
  尾拍处无配音只有 BGM，画面展示镜头结束帧；
- M8 音效轨：scene.sfx_asset_id 绑定的音效放场景全局起点（切点音效），组装进
  timeline.sfx（AudioClip.duration_ms 留 None —— 导出取素材自身时长不截断）；
- M8 BGM 卡点：config.beat_sync 且 assets/bgm.mp3 存在时，场景切换点（≥第 2 场景）
  向前 snap 到最近节拍（≤ SNAP_MAX_MS，只前移不后移 —— 配音不重叠），切点停顿处
  只有 BGM；节拍解码失败降级跳过（BGM 属装饰，不阻塞主线）；
- scene.start_ms 记录全局起点 —— 字幕仍保持场景内相对时间戳（M1 产物），导出时按
  scene.start_ms 平移（app/export/jianying.py），单一真相源不破坏幂等；
- voiceover 顶层字段填汇总：status=done，duration_ms = 整片时长（配音 + 首尾帧
  尾拍 + 卡点停顿，导出 BGM 铺满全片依赖它）。

可重入：每次从头重建 timeline 与 start_ms（覆盖写），重复执行结果一致
（节拍检测确定性 —— 同文件同输出）。
"""

from pathlib import Path

from mutagen.mp3 import MP3

from app.audio.beats import detect_beats
from app.core.schema import AudioClip, Project, VideoClip
from app.core.state import FatalError

# M8 项目素材约定：BGM 与音效目录（edits/export 同源引用，改这里三处生效）
BGM_FILENAME = "bgm.mp3"
SFX_DIR = "assets/sfx"
SNAP_MAX_MS = 400           # 卡点对齐：切点向前 snap 的最大提前量（切点停顿 ≤ 0.4s）


def build_timeline(project: Project, project_root: Path) -> None:
    """组装 project.timeline / scene.start_ms / voiceover 汇总。就地修改 project。

    Raises:
        FatalError: 场景为空、配音/图/音效资产或文件缺失（带修复提示）。
    """
    if not project.scenes:
        raise FatalError("没有分镜可组装", hint="先跑 avpo direct 生成分镜")

    project_root = Path(project_root)
    video: list[VideoClip] = []
    voiceover: list[AudioClip] = []
    sfx: list[AudioClip] = []
    total_ms = 0

    # M8 卡点对齐：开关开启且 BGM 已上传时检测节拍（解码失败降级跳过，不阻塞）
    beats = _load_beats(project, project_root)

    for scene in project.scenes:
        vo_id = f"vo_{scene.scene_id}"
        img_id = scene.image_asset_id
        if img_id is None:
            raise FatalError(
                f"scene {scene.scene_id} 未选择候选图",
                hint="先跑 avpo gen-assets 生成候选素材",
            )

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

        # M8 卡点：切点向前 snap 到最近节拍（首场景从 0 起不动；只前移不后移）
        if beats and total_ms > 0:
            snapped = _snap_to_beat(total_ms, beats)
            if snapped is not None:
                total_ms = snapped

        scene.start_ms = total_ms
        video.append(VideoClip(
            asset_id=img_id, start_ms=total_ms, duration_ms=duration_ms,
            motion=scene.motion, scene_id=scene.scene_id,
        ))
        voiceover.append(AudioClip(asset_id=vo_id, offset_ms=total_ms, duration_ms=duration_ms))

        # M8 音效轨：绑定音效放场景起点（切点音效，时长导出取素材自身）
        if scene.sfx_asset_id:
            sfx_asset = project.assets.get(scene.sfx_asset_id)
            if sfx_asset is None:
                raise FatalError(
                    f"scene {scene.scene_id} 音效资产 {scene.sfx_asset_id} 不存在",
                    hint="到策划页重新上传音效",
                )
            sfx_path = project_root / sfx_asset.path
            if not sfx_path.is_file():
                raise FatalError(
                    f"音效文件缺失: {sfx_path}",
                    hint="到策划页重新上传音效",
                )
            sfx.append(AudioClip(
                asset_id=scene.sfx_asset_id, offset_ms=total_ms, duration_ms=None,
            ))
        total_ms += duration_ms

        # M7-8.5 首尾帧尾拍：配音后追加静态尾拍（运镜计划由 animate 节点解析）
        plan = scene.motion_plan
        if plan and plan.end_frame_ms > 0:
            end_id = scene.end_image_asset_id
            if end_id is None:
                raise FatalError(
                    f"scene {scene.scene_id} 运镜计划有尾拍但未设结束帧",
                    hint="先跑 avpo animate（或检查 project.json 的 end_image_asset_id）",
                )
            end_asset = project.assets.get(end_id)
            if end_asset is None:
                raise FatalError(
                    f"scene {scene.scene_id} 缺少结束帧资产 {end_id}",
                    hint="先跑 avpo gen-assets 生成素材",
                )
            end_path = project_root / end_asset.path
            if not end_path.is_file():
                raise FatalError(f"结束帧文件缺失: {end_path}", hint="先跑 avpo gen-assets 重新生成")
            video.append(VideoClip(
                asset_id=end_id, start_ms=total_ms, duration_ms=plan.end_frame_ms,
                motion="none", scene_id=scene.scene_id,
            ))
            total_ms += plan.end_frame_ms

    project.timeline.video = video
    project.timeline.voiceover = voiceover
    project.timeline.sfx = sfx
    project.voiceover.duration_ms = total_ms          # 整片时长：配音 + 首尾帧尾拍 + 卡点停顿
    project.voiceover.status = "done"


def _load_beats(project: Project, project_root: Path) -> list[int]:
    """M8 卡点节拍：开关开启且 BGM 已上传时检测；否则空列表（不卡点）。

    解码失败降级为空（BGM 属装饰，不阻塞主线 —— 与模板 BGM 缺失降级同语义）。
    """
    if not project.config.beat_sync:
        return []
    bgm_path = project_root / "assets" / BGM_FILENAME
    if not bgm_path.is_file():
        return []
    try:
        return detect_beats(bgm_path)
    except ValueError:
        return []


def _snap_to_beat(ms: int, beats: list[int]) -> int | None:
    """[ms, ms+SNAP_MAX_MS] 内最近的节拍；无则 None（切点保持自然位置）。"""
    for b in beats:
        if ms <= b <= ms + SNAP_MAX_MS:
            return b
        if b > ms + SNAP_MAX_MS:
            break
    return None


def _audio_duration_ms(path: Path) -> int:
    """mutagen 读 mp3 实际时长（秒 → 毫秒，四舍五入）。"""
    return round(MP3(path).info.length * 1000)
