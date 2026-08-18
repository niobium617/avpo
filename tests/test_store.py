"""M0-1.2 验收：ProjectStore —— 原子写 + 保存后 git log 有提交。"""

import subprocess
from pathlib import Path

import pytest

from app.core.project import ProjectStore
from app.core.schema import Project


def git_log(data_dir: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(data_dir), "log", "--oneline"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def test_create_writes_valid_json_and_commits(store: ProjectStore, sample_project: Project) -> None:
    store.create(sample_project)

    # 文件存在且可重新校验加载
    assert store.exists("proj_001")
    assert store.load("proj_001") == sample_project

    # 保存后 git log 有提交
    log = git_log(store.data_dir)
    assert "创建项目" in log


def test_save_commits_each_change(store: ProjectStore, sample_project: Project) -> None:
    store.create(sample_project)
    n_before = len(git_log(store.data_dir).splitlines())

    sample_project.title = "新标题"
    sample_project.pipeline["direct"] = "done"
    store.save(sample_project, message="direct done")

    n_after = len(git_log(store.data_dir).splitlines())
    assert n_after == n_before + 1
    assert store.load("proj_001").title == "新标题"


def test_atomic_write_leaves_no_tmp(store: ProjectStore, sample_project: Project) -> None:
    store.create(sample_project)
    store.save(sample_project)
    assert not (store.json_path("proj_001").with_name("project.json.tmp")).exists()


def test_load_missing_project_raises(store: ProjectStore) -> None:
    with pytest.raises(FileNotFoundError):
        store.load("nope")


def test_create_duplicate_raises(store: ProjectStore, sample_project: Project) -> None:
    store.create(sample_project)
    with pytest.raises(FileExistsError):
        store.create(sample_project)


def test_load_corrupt_json_raises(store: ProjectStore, sample_project: Project) -> None:
    store.create(sample_project)
    store.json_path("proj_001").write_text("{ 这不是合法 JSON", encoding="utf-8")
    with pytest.raises(Exception):
        store.load("proj_001")
