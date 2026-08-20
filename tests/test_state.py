"""M1-2.6 任务状态机测试：done 跳过 / 重试退避 / 失败落盘 / 状态与产物同一次提交。"""

import subprocess

import pytest

from app.core.schema import Project
from app.core.state import FatalError, TransientError, run_task


@pytest.fixture()
def project() -> Project:
    return Project(project_id="p1", title="状态机测试")


def test_done_node_skips_fn(store, project):
    project.pipeline["direct"] = "done"
    store.create(project)
    calls: list[int] = []
    assert run_task(store, project, "direct", lambda p: calls.append(1)) is True
    assert calls == []


def test_success_persists_done(store, project):
    store.create(project)

    def fn(p: Project) -> None:
        p.title = "改过的标题"

    assert run_task(store, project, "direct", fn) is True
    assert project.pipeline["direct"] == "done"
    loaded = store.load("p1")
    assert loaded.pipeline["direct"] == "done" and loaded.title == "改过的标题"


def test_products_committed_with_done(store, project):
    """状态与产物同一次提交落盘 —— 断点续跑的前提。"""
    store.create(project)

    def fn(p: Project) -> None:
        (store.project_dir("p1") / "assets").mkdir(exist_ok=True)
        (store.project_dir("p1") / "assets" / "s1.png").write_bytes(b"png")

    run_task(store, project, "direct", fn)

    data_dir = store.data_dir
    last_msg = subprocess.run(
        ["git", "-C", str(data_dir), "log", "--format=%s", "-1"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    assert last_msg == "p1: direct done"
    files = subprocess.run(
        ["git", "-C", str(data_dir), "show", "HEAD", "--name-only", "--format="],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.split()
    assert "projects/p1/assets/s1.png" in files


def test_running_persisted_before_fn_runs(store, project):
    """fn 执行期间磁盘上已是 running —— 进程死在 fn 里也能从 running 恢复。"""
    store.create(project)

    def fn(p: Project) -> None:
        assert store.load("p1").pipeline["direct"] == "running"
        raise FatalError("格式错误", hint="检查文案标点")

    assert run_task(store, project, "direct", fn) is False
    assert store.load("p1").pipeline["direct"] == "failed"


def test_transient_retries_with_backoff_then_succeeds(store, project, monkeypatch):
    store.create(project)
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.state.time.sleep", sleeps.append)
    attempts = {"n": 0}

    def fn(p: Project) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientError("网络抖动")

    assert run_task(store, project, "direct", fn) is True
    assert attempts["n"] == 3
    assert sleeps == [1, 2]                        # 指数退避
    assert store.load("p1").pipeline["direct"] == "done"


def test_transient_exhausted_marks_failed_with_summary(store, project, monkeypatch):
    store.create(project)
    monkeypatch.setattr("app.core.state.time.sleep", lambda s: None)

    def fail(p: Project) -> None:
        raise TransientError("上游服务不可达")

    assert run_task(store, project, "direct", fail) is False
    loaded = store.load("p1")
    assert loaded.pipeline["direct"] == "failed"
    assert len(loaded.errors) == 1
    assert loaded.errors[0].node == "direct"
    assert "上游服务不可达" in loaded.errors[0].error


def test_fatal_error_no_retry(store, project):
    store.create(project)
    attempts = {"n": 0}

    def fn(p: Project) -> None:
        attempts["n"] += 1
        raise FatalError("剪映草稿损坏", hint="重新导出或升级剪映")

    assert run_task(store, project, "direct", fn) is False
    assert attempts["n"] == 1                       # 不重试
    loaded = store.load("p1")
    assert loaded.pipeline["direct"] == "failed"
    assert "重新导出或升级剪映" in loaded.errors[0].error


def test_unclassified_exception_treated_as_transient(store, project, monkeypatch):
    """未分类异常按 Transient 处理：重试 ×3 后失败。"""
    store.create(project)
    monkeypatch.setattr("app.core.state.time.sleep", lambda s: None)
    attempts = {"n": 0}

    def fn(p: Project) -> None:
        attempts["n"] += 1
        raise ValueError("未预期错误")

    assert run_task(store, project, "direct", fn) is False
    assert attempts["n"] == 3
    assert store.load("p1").pipeline["direct"] == "failed"


def test_unknown_node_raises(store, project):
    store.create(project)
    with pytest.raises(KeyError, match="未知"):
        run_task(store, project, "not_a_node", lambda p: None)
