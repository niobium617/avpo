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
    assert list(project.pipeline) == ["direct", "confirm", "gen_assets", "timeline", "export"]
    assert all(v == "pending" for v in project.pipeline.values())


def test_status_assignment_validated(sample_project: Project) -> None:
    """validate_assignment：赋值时也走校验，非法状态当场报错。"""
    with pytest.raises(ValidationError):
        sample_project.scenes[0].status = "finished"  # type: ignore[assignment]
