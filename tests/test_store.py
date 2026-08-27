"""M0-1.2 验收：ProjectStore —— 原子写 + 保存后 git log 有提交。"""

import subprocess
import threading
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


# ---- M4-5.1 项目枚举（web 工作台项目选择器） ----

def test_list_project_ids_empty(store: ProjectStore) -> None:
    assert store.list_project_ids() == []


def test_list_project_ids_sorted(store: ProjectStore, sample_project: Project) -> None:
    for pid in ("proj_b", "proj_a"):
        p = sample_project.model_copy(deep=True)
        p.project_id = pid
        store.create(p)
    assert store.list_project_ids() == ["proj_a", "proj_b"]


def test_list_project_ids_ignores_dir_without_json(store: ProjectStore, sample_project: Project) -> None:
    store.create(sample_project)
    (store.projects_dir / "stray").mkdir()          # 无 project.json 的目录不算项目
    assert store.list_project_ids() == ["proj_001"]


def test_list_projects_corrupt_json_raises_with_id(store: ProjectStore, sample_project: Project) -> None:
    store.create(sample_project)
    store.json_path("proj_001").write_text("{ 损坏", encoding="utf-8")
    with pytest.raises(ValueError, match="proj_001"):
        store.list_projects()


# ---- M5 6.1 save 线程锁（后台任务 + 多会话并发 save 串行化） ----

def test_save_serialized_across_threads(store: ProjectStore, sample_project: Project) -> None:
    """4 线程 × 3 次 save 并发：git 索引无竞争损坏，提交数与校验都正确。"""
    store.create(sample_project)
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        for i in range(3):
            try:
                p = sample_project.model_copy(deep=True)
                p.title = f"线程 {tid} 第 {i} 次"     # 每次制造差异保证产生提交
                store.save(p, message=f"t{tid}-{i}")
            except Exception as exc:  # noqa: BLE001 —— 并发下任何异常都记录并失败
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # 1 次 create + 12 次 save，全部成功提交（锁串行化 git index，无丢失/交错）
    assert len(git_log(store.data_dir).splitlines()) == 13
    loaded = store.load("proj_001")                 # 最终文件合法且为某线程最后写入
    assert loaded.title.startswith("线程 ")
    assert loaded.project_id == "proj_001"


# ---- M7-8.3 升版 shim（0.1/0.2 → 0.3）----

def test_load_legacy_v01_json_reports_030_in_memory(store: ProjectStore) -> None:
    """手写 v0.1 JSON（无任何 M6/M7 键）：加载成功，内存版本升 0.3，文件不变。"""
    legacy = (
        '{"project_id": "old", "schema_version": "0.1", "title": "旧项目",'
        ' "pipeline": {"direct": "pending", "confirm": "pending", "gen_assets": "pending",'
        ' "timeline": "pending", "export": "pending"}}'
    )
    store.json_path("old").parent.mkdir(parents=True, exist_ok=True)
    store.json_path("old").write_text(legacy, encoding="utf-8")

    loaded = store.load("old")
    assert loaded.schema_version == "0.3"           # 内存升版
    assert loaded.pipeline["animate"] == "pending"  # 旧 5 节点 pipeline 补新键
    assert loaded.brief is None                     # 新字段默认
    assert "0.1" in store.json_path("old").read_text(encoding="utf-8")   # 文件未迁移


def test_save_persists_030(store: ProjectStore, sample_project: Project) -> None:
    """升版后的项目 save 时持久化 0.3。"""
    sample_project.schema_version = "0.1"
    store.create(sample_project)
    loaded = store.load("proj_001")
    assert loaded.schema_version == "0.3"
    store.save(loaded, message="升版")
    assert '"0.3"' in store.json_path("proj_001").read_text(encoding="utf-8")
