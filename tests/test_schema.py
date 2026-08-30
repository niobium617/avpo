"""M0-1.1 验收：pydantic schema —— JSON roundtrip 一致 + 引用完整性校验。"""

import pytest
from pydantic import ValidationError

from app.core.schema import Project


def test_json_roundtrip_consistent(sample_project: Project) -> None:
    """读 → 写 → 读 一致（fixture JSON 逐字段无损）。"""
    dumped = sample_project.model_dump(mode="json")
    reloaded = Project.model_validate(dumped)
    assert reloaded.model_dump(mode="json") == dumped


def test_unknown_field_rejected(sample_project: Project) -> None:
    """改错字段当场报错（extra=forbid）。"""
    data = sample_project.model_dump(mode="json")
    data["scenes"][0]["naration"] = "typo"  # 拼错的字段
    with pytest.raises(ValidationError):
        Project.model_validate(data)


def test_duplicate_scene_id_rejected(sample_project: Project) -> None:
    data = sample_project.model_dump(mode="json")
    data["scenes"][1]["scene_id"] = "s1"
    with pytest.raises(ValidationError, match="scene_id 重复"):
        Project.model_validate(data)


def test_subtitle_missing_scene_rejected(sample_project: Project) -> None:
    data = sample_project.model_dump(mode="json")
    data["subtitles"][0]["scene_id"] = "s_ghost"
    with pytest.raises(ValidationError, match="不存在的 scene_id"):
        Project.model_validate(data)


def test_timeline_missing_asset_rejected(sample_project: Project) -> None:
    data = sample_project.model_dump(mode="json")
    data["timeline"]["video"][0]["asset_id"] = "a_ghost"
    with pytest.raises(ValidationError, match="不存在的 asset_id"):
        Project.model_validate(data)


def test_unknown_pipeline_node_rejected(sample_project: Project) -> None:
    data = sample_project.model_dump(mode="json")
    data["pipeline"]["render_video"] = "pending"
    with pytest.raises(ValidationError, match="未知节点"):
        Project.model_validate(data)


def test_new_project_has_all_pipeline_nodes() -> None:
    project = Project(project_id="proj_x")
    assert list(project.pipeline) == [
        "direct", "confirm", "gen_assets", "transcribe", "animate", "timeline", "export",
    ]
    assert all(v == "pending" for v in project.pipeline.values())


def test_status_assignment_validated(sample_project: Project) -> None:
    """validate_assignment：赋值时也走校验，非法状态当场报错。"""
    with pytest.raises(ValidationError):
        sample_project.scenes[0].status = "finished"  # type: ignore[assignment]


# ---------------------------------------------------------------- M6-7.1 schema 0.2 / M7-8.1 0.3 / M8 0.4 / M9 0.5 / M10 0.6

def test_new_project_schema_version_060() -> None:
    """M10：新项目 schema_version = 0.6，pipeline 含 transcribe + animate 节点。"""
    project = Project(project_id="proj_m10")
    assert project.schema_version == "0.6"
    assert list(project.pipeline) == [
        "direct", "confirm", "gen_assets", "transcribe", "animate", "timeline", "export",
    ]
    assert project.config.whisper_model == "small"
    assert project.config.whisper_language == "zh"
    assert project.scenes == []


def test_legacy_json_loads_with_new_defaults() -> None:
    """旧 JSON（schema 0.1，无 M6/M7/M8 键）加载：新字段全部落到默认值。"""
    data = {
        "project_id": "legacy",
        "schema_version": "0.1",
        "pipeline": {n: "pending" for n in ("direct", "confirm", "gen_assets", "timeline", "export")},
        "scenes": [{"scene_id": "s1", "narration": "hi", "motion": "none"}],
    }
    project = Project.model_validate(data)
    assert project.brief is None
    assert project.reference_images == []
    s = project.scenes[0]
    assert s.shot_size == ""
    assert s.sfx == ""
    assert s.planned_duration_ms is None
    assert s.image_candidates == []
    assert s.end_image_asset_id is None
    assert s.image_asset_id is None
    assert s.motion_plan is None                   # M7-8.1：未跑 animate 无运镜计划
    assert s.sfx_asset_id is None                  # M8：音效未绑定
    assert project.timeline.sfx == []              # M8：音效轨为空
    assert project.config.beat_sync is False       # M8：卡点对齐默认关
    assert s.transition == "auto"                  # M9：止损转场默认 auto
    assert project.timeline.video == []            # M9：未组装时间线（VideoClip.transition 默认 none 见 m9 测试）


def test_motion_plan_roundtrip() -> None:
    """MotionPlan 全字段 dump/validate 无损（M7 运镜计划）。"""
    from app.core.schema import MotionPlan

    plan = MotionPlan(scale_from=1.0, scale_to=1.15, pan_from=-0.12, pan_to=0.12, end_frame_ms=400)
    reloaded = MotionPlan.model_validate(plan.model_dump(mode="json"))
    assert reloaded.model_dump(mode="json") == plan.model_dump(mode="json")


def test_clip_scene_id_ghost_rejected(sample_project: Project) -> None:
    """视频轨 clip 的 scene_id 引用不存在的场景 → 拒绝（M7-8.1）。"""
    data = sample_project.model_dump(mode="json")
    data["timeline"]["video"][0]["scene_id"] = "s_ghost"
    with pytest.raises(ValidationError, match="不存在的 scene_id"):
        Project.model_validate(data)


def test_brief_roundtrip() -> None:
    """Brief 全字段 dump/validate 无损（含风格关键词包与 BGM 节奏）。"""
    project = Project(
        project_id="p",
        brief={
            "theme": "末日求生", "art_style": "实写末日", "platform": "抖音",
            "palette": "冷灰 + 橙色火光", "lighting": "volumetric lighting from upper left",
            "character": "络腮胡中年男", "bgm_hint": "重拍在第 3、8 秒",
        },
    )
    dumped = project.model_dump(mode="json")
    reloaded = Project.model_validate(dumped)
    assert reloaded.brief == project.brief  # type: ignore[union-attr]


def test_candidate_asset_missing_rejected(sample_project: Project) -> None:
    """候选图引用不存在的 asset → 拒绝。"""
    data = sample_project.model_dump(mode="json")
    data["scenes"][0]["image_candidates"] = ["img_ghost"]
    with pytest.raises(ValidationError, match="候选图引用了不存在的 asset_id"):
        Project.model_validate(data)


def test_end_image_asset_ghost_rejected(sample_project: Project) -> None:
    """首尾帧引用不存在的 asset → 拒绝（M7 预埋字段的校验先行）。"""
    data = sample_project.model_dump(mode="json")
    data["scenes"][0]["end_image_asset_id"] = "img_ghost"
    with pytest.raises(ValidationError, match="首尾帧引用了不存在的 asset_id"):
        Project.model_validate(data)


def test_reference_images_duplicate_id_rejected(sample_project: Project) -> None:
    data = sample_project.model_dump(mode="json")
    data["reference_images"] = [
        {"id": "ref1", "path": "assets/ref_1.png"},
        {"id": "ref1", "path": "assets/ref_2.png"},
    ]
    with pytest.raises(ValidationError, match="reference_images id 重复"):
        Project.model_validate(data)


def test_candidates_bounds_enforced() -> None:
    """ImageConfig.candidates 范围 1~6。"""
    with pytest.raises(ValidationError):
        from app.core.schema import ProjectConfig
        ProjectConfig(image={"candidates": 0})
    ProjectConfig(image={"candidates": 6})  # 边界合法
    with pytest.raises(ValidationError):
        ProjectConfig(image={"candidates": 7})
