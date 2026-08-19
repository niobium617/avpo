"""剪映草稿导出适配器 —— project.json → 剪映草稿目录（三轨）+ zip 打包。

实现依据：docs/jyd_notes.md（pyJianYingDraft 0.3.0 实测）。
- 时间单位换算：project.json 毫秒 → 剪映草稿微秒（×1000）。
- 素材先拷入草稿目录再建素材引用 → 草稿自包含，zip 可迁移。
- 运镜动画：zoom_in_slow/zoom_out 映射剪映入场动画（枚举中文名）；pan_* 暂无对应
  动画枚举，先降级为 none 不加动画（M2 扩展），不阻塞主线（IMPLEMENTATION_PLAN §7）。
"""

import shutil
from pathlib import Path

from pyJianYingDraft import (  # noqa: N999 —— 包名本身大写
    AudioMaterial,
    AudioSegment,
    DraftFolder,
    IntroType,
    TextSegment,
    TrackSpec,
    TrackType,
    VideoSegment,
    trange,
)

from app.core.schema import Project

# 运镜 → 剪映入场动画（中文枚举名，见 jyd_notes §2）
_MOTION_ANIMATION = {
    "zoom_in_slow": "放大",
    "zoom_out": "缩小",
}
# 动画时长上限：长片段不让入场动画拖太久（demo 实测 0.8~1s 观感 OK）
_ANIM_MAX_MS = 2000

# 草稿内的固定三轨名（MVP 固定三轨，见 EXECUTION_PLAN §1-决策5）
_TRACK_VIDEO = "v1"
_TRACK_AUDIO = "a1"
_TRACK_TEXT = "sub"
_MATERIALS_DIR = "materials"


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

    # 素材先拷入草稿 → 草稿自包含（相对引用 + zip 可迁移）
    materials_dir = Path(draft.save_path).parent / _MATERIALS_DIR
    materials_dir.mkdir(parents=True, exist_ok=True)
    draft_paths: dict[str, Path] = {}
    for asset_id, asset in project.assets.items():
        src = project_root / asset.path
        if not src.is_file():
            raise FileNotFoundError(f"素材缺失: {asset_id} -> {src}")
        draft_paths[asset_id] = _copy_to_materials(src, materials_dir)

    draft.append_track(TrackSpec(TrackType.video, _TRACK_VIDEO))
    draft.append_track(TrackSpec(TrackType.audio, _TRACK_AUDIO))

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

    # 音频轨：整条配音。时长以实际音频为准（EXECUTION_PLAN §3）：project.json
    # 里的 duration_ms 只是记录值，超出素材真实时长时截断，不抛错。
    for clip in project.timeline.voiceover:
        asset = project.assets[clip.asset_id]
        if asset.type != "audio":
            raise ValueError(f"音频轨素材类型不符: {clip.asset_id} 是 {asset.type}")
        material = AudioMaterial(str(draft_paths[clip.asset_id]))
        if project.voiceover.duration_ms:
            duration_us = min(project.voiceover.duration_ms * 1000, material.duration)
        else:
            duration_us = material.duration
        draft.add_segment(
            AudioSegment(str(draft_paths[clip.asset_id]), trange(clip.offset_ms * 1000, duration_us)),
            _TRACK_AUDIO,
        )

    # 字幕轨：逐条文本段（样式定制留到 M2-3.2）
    draft.append_track(TrackSpec(TrackType.text, _TRACK_TEXT))
    for sub in project.subtitles:
        draft.add_segment(
            TextSegment(sub.text, trange(sub.start_ms * 1000, (sub.end_ms - sub.start_ms) * 1000)),
            _TRACK_TEXT,
        )

    draft.save()

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
