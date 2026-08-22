"""剪映草稿导出适配器 —— project.json → 剪映草稿目录（三轨）+ zip 打包。

实现依据：docs/jyd_notes.md（pyJianYingDraft 0.3.0 实测）。
- 时间单位换算：project.json 毫秒 → 剪映草稿微秒（×1000）。
- 素材先拷入草稿目录再建素材引用 → 草稿自包含，zip 可迁移。
- 运镜动画：zoom_in_slow/zoom_out 映射剪映入场动画（枚举中文名）；pan_* 暂无对应
  动画枚举，先降级为 none 不加动画（M2 扩展），不阻塞主线（IMPLEMENTATION_PLAN §7）。
- M2-3.2 扩展：字幕样式（字号/居中/描边/低位）、配音段淡入淡出、草稿元信息
  （draft_name/tm_duration）。封面由剪映取首帧自动生成 —— 首段从 0 起即首图。
"""

import json
import shutil
from pathlib import Path

from pyJianYingDraft import (  # noqa: N999 —— 包名本身大写
    AudioMaterial,
    AudioSegment,
    ClipSettings,
    DraftFolder,
    IntroType,
    TextBorder,
    TextSegment,
    TextStyle,
    TrackSpec,
    TrackType,
    VideoSegment,
    trange,
)

from app.core.schema import Project
from app.core.styles import load_style

# 运镜 → 剪映入场动画（中文枚举名，见 jyd_notes §2）
_MOTION_ANIMATION = {
    "zoom_in_slow": "放大",
    "zoom_out": "缩小",
}
# 动画时长上限：长片段不让入场动画拖太久（demo 实测 0.8~1s 观感 OK）
_ANIM_MAX_MS = 2000

# 草稿内的固定轨道名（MVP 固定三轨，见 EXECUTION_PLAN §1-决策5；BGM 为 M3-4.4 可选第 4 轨）
_TRACK_VIDEO = "v1"
_TRACK_AUDIO = "a1"
_TRACK_TEXT = "sub"
_TRACK_BGM = "bgm"
_MATERIALS_DIR = "materials"

# 配音段淡入淡出：每段 300ms，衔接处不突兀、首段渐入末段渐出
_FADE_MS = 300
# BGM 尾部淡出 1s（音乐结尾不突兀）
_BGM_FADE_OUT_MS = 1000


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

    # BGM：模板配置了路径且文件存在才加轨（缺失降级跳过，不阻塞导出）
    bgm_draft_path: Path | None = None
    if style.bgm:
        bgm_src = project_root / style.bgm
        if bgm_src.is_file():
            bgm_draft_path = _copy_to_materials(bgm_src, materials_dir)

    draft.append_track(TrackSpec(TrackType.video, _TRACK_VIDEO))
    draft.append_track(TrackSpec(TrackType.audio, _TRACK_AUDIO))
    if bgm_draft_path:
        draft.append_track(TrackSpec(TrackType.audio, _TRACK_BGM))

    # 视频轨：图片/视频素材 + 运镜入场动画
    for clip in project.timeline.video:
        asset = project.assets[clip.asset_id]
        if asset.type not in ("image", "video"):
            raise ValueError(f"视频轨素材类型不符: {clip.asset_id} 是 {asset.type}")
        segment = VideoSegment(
            str(draft_paths[clip.asset_id]),
            trange(clip.start_ms * 1000, clip.duration_ms * 1000),
        )
        _apply_motion(segment, clip.motion, clip.duration_ms)
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


def _apply_motion(segment: VideoSegment, motion: str, duration_ms: int) -> None:
    """运镜 → 入场动画；未知运镜降级为 none（不加动画，不阻塞导出）。"""
    anim_name = _MOTION_ANIMATION.get(motion)
    if anim_name is None:
        return
    segment.add_animation(IntroType[anim_name], f"{min(duration_ms, _ANIM_MAX_MS) / 1000:.2f}s")


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
