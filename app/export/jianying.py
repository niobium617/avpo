"""剪映草稿导出适配器 —— project.json → 剪映草稿目录（三轨）+ zip 打包。

实现依据：docs/jyd_notes.md（pyJianYingDraft 0.3.0 实测）。
- 时间单位换算：project.json 毫秒 → 剪映草稿微秒（×1000）。
- 素材先拷入草稿目录再建素材引用 → 草稿自包含，zip 可迁移。
- M7-8.6 运镜动画：全部用关键帧实现（KeyframeProperty.uniform_scale / position_x
  线性插值），覆盖 zoom_in_slow / zoom_out / pan_left / pan_right —— M2 曾把
  pan_* 降级为无动画（无入场动画枚举），M7 解除该限制。参数取自
  scene.motion_plan（animate 节点解析落盘）；旧项目没跑 animate 时按
  scene.motion 兜底解析（app/core/motion.py 与 animate 同源）。首尾帧尾拍
  （motion=none 的 clip）按通用视频轨导出，不加任何动画。
- M9 止损转场：clip.transition（timeline 组装解析 scene.transition）映射
  TransitionType（闪白/震动），固定 0.3s（_TRANSITION_MS）；转场加在前一段上、
  轨道时长不变；草稿 JSON 落地为 materials.transitions + 段级 extra_material_refs。
- M2-3.2 扩展：字幕样式（字号/居中/描边/低位）、配音段淡入淡出、草稿元信息
  （draft_name/tm_duration）。封面由剪映取首帧自动生成 —— 首段从 0 起即首图。
- M8 音频：sfx 音效轨（timeline.sfx，切点音效短促不加淡入淡出）+ BGM 来源解耦
  （项目上传 assets/bgm.mp3 优先，模板 style.bgm 兜底 —— 无模板 BGM 的项目
  上传后即铺满全片）。
"""

import json
import shutil
from pathlib import Path

from pyJianYingDraft import (  # noqa: N999 —— 包名本身大写
    AudioMaterial,
    AudioSegment,
    ClipSettings,
    DraftFolder,
    KeyframeProperty,
    TextBorder,
    TextSegment,
    TextStyle,
    TrackSpec,
    TrackType,
    TransitionType,
    VideoSegment,
    trange,
)

from app.core.motion import resolve_motion_plan
from app.core.schema import MotionPlan, Project
from app.core.styles import load_style
from app.timeline.builder import BGM_FILENAME

# 草稿内的固定轨道名（MVP 固定三轨，见 EXECUTION_PLAN §1-决策5；BGM 为 M3-4.4 可选第 4 轨，
# sfx 为 M8 第 5 轨）
_TRACK_VIDEO = "v1"
_TRACK_AUDIO = "a1"
_TRACK_TEXT = "sub"
_TRACK_BGM = "bgm"
_TRACK_SFX = "sfx"
_MATERIALS_DIR = "materials"

# 配音段淡入淡出：每段 300ms，衔接处不突兀、首段渐入末段渐出
_FADE_MS = 300
# BGM 尾部淡出 1s（音乐结尾不突兀）
_BGM_FADE_OUT_MS = 1000
# M9 止损转场：固定 0.3s（路线图定值，单点可调）；闪白 = 非叠加（切点处播放），
# 震动 = 叠加（剪映原生元数据，编辑器自处理相邻 clip 的叠化窗口）
_TRANSITION_MS = 300
_TRANSITION_TYPES = {
    "flash_white": TransitionType.闪白,
    "shake": TransitionType.震动,
}


def export(
    project: Project,
    project_root: Path,
    out_root: Path | None = None,
    *,
    zip_archive: bool = True,
) -> Path:
    """把 project 导出为剪映草稿目录，返回草稿目录路径。

    Args:
        project: 校验通过的 Project（assets.path 相对 project_root）。
        project_root: 项目数据目录，如 data/projects/<pid>。
        out_root: 导出根目录，默认 project_root/exports。
        zip_archive: 是否同时打 zip（可迁移/备份）。

    Raises:
        FileNotFoundError: 时间线引用的素材文件缺失。
        ValueError: 素材类型与轨道不符（如音频轨引用图片）。
    """
    project_root = Path(project_root)
    out_root = Path(out_root) if out_root is not None else project_root / "exports"
    out_root.mkdir(parents=True, exist_ok=True)  # DraftFolder 要求根目录已存在

    draft_name = f"{project.project_id}_draft"
    folder = DraftFolder(str(out_root))
    draft = folder.create_draft(draft_name, 1920, 1080, fps=30, allow_replace=True)

    # M3-4.4 风格模板：字幕样式参数化 + 可选 BGM 轨
    style = load_style(project.config.style)

    # 素材先拷入草稿 → 草稿自包含（相对引用 + zip 可迁移）
    materials_dir = Path(draft.save_path).parent / _MATERIALS_DIR
    materials_dir.mkdir(parents=True, exist_ok=True)
    draft_paths: dict[str, Path] = {}
    for asset_id, asset in project.assets.items():
        src = project_root / asset.path
        if not src.is_file():
            raise FileNotFoundError(f"素材缺失: {asset_id} -> {src}")
        draft_paths[asset_id] = _copy_to_materials(src, materials_dir)

    # BGM（M8 来源解耦）：项目上传的 assets/bgm.mp3 优先（策划页上传），模板
    # style.bgm 兜底（老约定）；文件缺失降级跳过，不阻塞导出。
    bgm_draft_path: Path | None = None
    bgm_src = project_root / "assets" / BGM_FILENAME
    if not bgm_src.is_file() and style.bgm:
        bgm_src = project_root / style.bgm
    if bgm_src.is_file():
        bgm_draft_path = _copy_to_materials(bgm_src, materials_dir)

    draft.append_track(TrackSpec(TrackType.video, _TRACK_VIDEO))
    draft.append_track(TrackSpec(TrackType.audio, _TRACK_AUDIO))
    if bgm_draft_path:
        draft.append_track(TrackSpec(TrackType.audio, _TRACK_BGM))
    if project.timeline.sfx:
        draft.append_track(TrackSpec(TrackType.audio, _TRACK_SFX))

    # 视频轨：图片/视频素材 + M7 关键帧运镜（参数取自场景运镜计划）
    scene_map = {s.scene_id: s for s in project.scenes}
    for clip in project.timeline.video:
        asset = project.assets[clip.asset_id]
        if asset.type not in ("image", "video"):
            raise ValueError(f"视频轨素材类型不符: {clip.asset_id} 是 {asset.type}")
        segment = VideoSegment(
            str(draft_paths[clip.asset_id]),
            trange(clip.start_ms * 1000, clip.duration_ms * 1000),
        )
        scene = scene_map.get(clip.scene_id)
        if scene is None and not clip.scene_id:
            # 旧项目兜底（0.2 及以前：clip 无 scene_id）：按图资产 id 前缀找回场景
            scene = next(
                (s for s in project.scenes
                 if clip.asset_id == f"img_{s.scene_id}"
                 or clip.asset_id.startswith(f"img_{s.scene_id}_")),
                None,
            )
        # 运镜只属于主镜头 clip：motion=none 的段（首尾帧尾拍/静态段）不套场景计划
        plan = scene.motion_plan if (scene and clip.motion != "none") else None
        if plan is None and scene is not None and clip.motion != "none":
            plan = resolve_motion_plan(scene)   # 旧项目兜底：没跑 animate 也按 motion 出运镜
        _apply_motion_plan(segment, plan, clip.duration_ms)
        # M9 止损转场：clip.transition（timeline 组装解析）映射剪映转场，固定 0.3s；
        # 转场加在前一段上（库语义），轨道时长不变
        if clip.transition in _TRANSITION_TYPES:
            segment.add_transition(
                _TRANSITION_TYPES[clip.transition], duration=_TRANSITION_MS * 1000,
            )
        draft.add_segment(segment, _TRACK_VIDEO)

    # 音频轨：时长优先级 clip.duration_ms（时间线组装写入）> voiceover.duration_ms
    # （单条整片配音）> 素材自身时长；超出素材真实时长时截断，不抛错。
    for clip in project.timeline.voiceover:
        asset = project.assets[clip.asset_id]
        if asset.type != "audio":
            raise ValueError(f"音频轨素材类型不符: {clip.asset_id} 是 {asset.type}")
        material = AudioMaterial(str(draft_paths[clip.asset_id]))
        claimed_ms = clip.duration_ms if clip.duration_ms is not None else project.voiceover.duration_ms
        if claimed_ms:
            duration_us = min(claimed_ms * 1000, material.duration)
        else:
            duration_us = material.duration
        segment = AudioSegment(str(draft_paths[clip.asset_id]), trange(clip.offset_ms * 1000, duration_us))
        segment.add_fade(_FADE_MS * 1000, _FADE_MS * 1000)     # 淡入淡出 300ms
        draft.add_segment(segment, _TRACK_AUDIO)

    # BGM 轨：从 0 铺到整片结束（截断到素材自身时长），尾部 1s 淡出
    if bgm_draft_path:
        bgm_material = AudioMaterial(str(bgm_draft_path))
        end_ms = _total_end_ms(project)
        duration_us = min(end_ms * 1000, bgm_material.duration)
        bgm_segment = AudioSegment(str(bgm_draft_path), trange(0, duration_us))
        bgm_segment.add_fade(0, _BGM_FADE_OUT_MS * 1000)
        draft.add_segment(bgm_segment, _TRACK_BGM)

    # 音效轨（M8）：切点音效按素材自身时长整段播放（AudioClip.duration_ms 组装时
    # 留 None），短促音效不淡入淡出（淡入会削弱起音打击感）。
    for clip in project.timeline.sfx:
        asset = project.assets[clip.asset_id]
        if asset.type != "audio":
            raise ValueError(f"音效轨素材类型不符: {clip.asset_id} 是 {asset.type}")
        material = AudioMaterial(str(draft_paths[clip.asset_id]))
        segment = AudioSegment(
            str(draft_paths[clip.asset_id]),
            trange(clip.offset_ms * 1000, material.duration),
        )
        draft.add_segment(segment, _TRACK_SFX)

    # 字幕轨：逐条文本段，样式按风格模板（M2-3.2 固定样式 = default 模板）。字幕存的是
    # 场景内相对时间戳（M1 产物，各场景从 0 开始），导出时按 scene.start_ms 平移到全局时间轴。
    scene_start = {s.scene_id: s.start_ms for s in project.scenes}
    sub_style = TextStyle(size=style.subtitle_style.size, align=1)
    sub_border = TextBorder(
        alpha=1.0, color=(0.0, 0.0, 0.0), width=style.subtitle_style.border_width
    )
    sub_clip = ClipSettings(transform_y=style.subtitle_style.y)
    draft.append_track(TrackSpec(TrackType.text, _TRACK_TEXT))
    for sub in project.subtitles:
        offset = scene_start.get(sub.scene_id, 0)
        draft.add_segment(
            TextSegment(
                sub.text,
                trange((sub.start_ms + offset) * 1000, (sub.end_ms - sub.start_ms) * 1000),
                style=sub_style,
                border=sub_border,
                clip_settings=sub_clip,
            ),
            _TRACK_TEXT,
        )

    draft.save()
    _write_meta_info(project, Path(draft.save_path).parent)

    if zip_archive:
        shutil.make_archive(str(out_root / draft_name), "zip", root_dir=out_root, base_dir=draft_name)

    return Path(draft.save_path).parent


def _apply_motion_plan(segment: VideoSegment, plan: MotionPlan | None, duration_ms: int) -> None:
    """关键帧运镜：按计划在片段首尾写缩放/横移关键帧（线性插值，微秒单位）。

    只写有变化的属性（默认值不写关键帧）：缩放计划（zoom/pan 恒 1.15 防露边）
    写 uniform_scale，横移计划写 position_x；none/未知运镜 = 空计划，不加任何动画。
    """
    if plan is None:
        return
    end_us = duration_ms * 1000
    if plan.scale_from != 1.0 or plan.scale_to != 1.0:
        segment.add_keyframe(KeyframeProperty.uniform_scale, 0, plan.scale_from)
        segment.add_keyframe(KeyframeProperty.uniform_scale, end_us, plan.scale_to)
    if plan.pan_from != 0.0 or plan.pan_to != 0.0:
        segment.add_keyframe(KeyframeProperty.position_x, 0, plan.pan_from)
        segment.add_keyframe(KeyframeProperty.position_x, end_us, plan.pan_to)


def _copy_to_materials(src: Path, materials_dir: Path) -> Path:
    """拷贝素材进草稿目录，重名时加序号，返回草稿内路径。"""
    dst = materials_dir / src.name
    if dst.exists():
        stem, suffix = src.stem, src.suffix
        i = 2
        while (candidate := materials_dir / f"{stem}_{i}{suffix}").exists():
            i += 1
        dst = candidate
    shutil.copy2(src, dst)
    return dst


def _total_end_ms(project: Project) -> int:
    """整片结束毫秒：优先配音汇总值，否则取视频轨最晚终点。"""
    return max(
        (project.voiceover.duration_ms or 0),
        *((c.start_ms + c.duration_ms) for c in project.timeline.video),
    )


def _write_meta_info(project: Project, draft_dir: Path) -> None:
    """补写草稿元信息（M2-3.2）：draft_name + tm_duration（微秒）。

    draft_cover 留空 —— 剪映打开时取首帧自动生成封面缩略图，首段从 0 起即首图。
    """
    meta_path = draft_dir / "draft_meta_info.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["draft_name"] = project.title or project.project_id
    meta["tm_duration"] = _total_end_ms(project) * 1000
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
