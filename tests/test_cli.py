"""M0-1.3 验收：CLI 骨架 —— avpo new 产出合法 JSON，avpo status 打印状态树。"""

from pathlib import Path

from typer.testing import CliRunner

from app.cli import create_app
from app.core.project import ProjectStore
from app.core.schema import Project


def test_new_creates_valid_project(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    result = CliRunner().invoke(app, ["new", "proj_001", "--title", "AI 产品口播"])
    assert result.exit_code == 0, result.output

    project = ProjectStore(tmp_path / "data").load("proj_001")
    assert project.title == "AI 产品口播"
    assert isinstance(project, Project)


def test_new_duplicate_exits_1(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    assert runner.invoke(app, ["new", "proj_001"]).exit_code == 0
    assert runner.invoke(app, ["new", "proj_001"]).exit_code == 1


def test_status_prints_tree(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001", "--title", "AI 产品口播"])

    result = runner.invoke(app, ["status", "proj_001"])
    assert result.exit_code == 0, result.output
    assert "proj_001" in result.output
    assert "「AI 产品口播」" in result.output
    # 状态树覆盖 pipeline 全部节点
    for node in ("direct", "confirm", "gen_assets", "timeline", "export"):
        assert node in result.output
    assert "pending" in result.output


def test_status_missing_project_exits_1(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    result = CliRunner().invoke(app, ["status", "nope"])
    assert result.exit_code == 1


# ---------------------------------------------------------------- M1-2.8 CLI 命令

def _raise_missing_key(config):
    raise KeyError("缺少 DASHSCOPE_API_KEY：请在项目根 .env 配置（模板见 .env.example）")


def test_direct_missing_key_exits_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_director", _raise_missing_key)
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["direct", "proj_001", "--text", "你好。"])
    assert result.exit_code == 1
    assert "DASHSCOPE_API_KEY" in result.output


def test_direct_runs_with_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_director", lambda config: None)
    # 假 run_direct：不动真实 LLM API
    monkeypatch.setattr("app.cli.run_direct", lambda store, p, d, text: True)
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["direct", "proj_001", "--text", "你好。"])
    assert result.exit_code == 0, result.output
    assert "direct 完成" in result.output


def test_gen_assets_missing_key_exits_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_image", _raise_missing_key)
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["gen-assets", "proj_001"])
    assert result.exit_code == 1
    assert "DASHSCOPE_API_KEY" in result.output


def test_gen_assets_runs_with_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_image", lambda config: None)
    monkeypatch.setattr("app.cli.run_gen_assets", lambda store, p, tts, image: True)
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["gen-assets", "proj_001"])
    assert result.exit_code == 0, result.output
    assert "gen_assets 完成" in result.output


def test_new_with_dashscope_provider(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    result = runner.invoke(app, ["new", "proj_002", "--provider", "dashscope"])
    assert result.exit_code == 0, result.output

    project = ProjectStore(tmp_path / "data").load("proj_002")
    assert project.config.llm.provider == "dashscope"
    assert project.config.llm.model == "qwen-plus"
    assert project.config.image.model == "wanx2.1-t2i-turbo"


def test_new_unknown_provider_exits_1(tmp_path: Path) -> None:
    app = create_app(tmp_path / "data")
    result = CliRunner().invoke(app, ["new", "proj_003", "--provider", "openai"])
    assert result.exit_code == 1


# ---------------------------------------------------------------- M2-3.3 avpo run

def _make_direct_done_project(data_dir: Path) -> None:
    """预置一个 direct 已完成、带 2 个分镜的项目（confirm 测试用）。"""
    from app.core.schema import Scene

    store = ProjectStore(data_dir)
    store.init_repo()
    project = store.load("proj_001")
    project.pipeline["direct"] = "done"
    project.scenes = [
        Scene(scene_id="s1", narration="第一段文案。", visual="画面一", motion="none"),
        Scene(scene_id="s2", narration="第二段文案。", visual="画面二", motion="zoom_in_slow"),
    ]
    store.save(project)


def _mock_pipeline(monkeypatch) -> dict:
    """把 run 命令里所有节点 mock 掉，返回调用记录。"""
    calls: dict[str, int] = {}

    def record(name):
        def fn(*args, **kwargs):
            calls[name] = calls.get(name, 0) + 1
            return True
        return fn

    monkeypatch.setattr("app.cli._load_env", lambda: None)
    monkeypatch.setattr("app.cli.make_director", lambda config: None)
    monkeypatch.setattr("app.cli.make_image", lambda config: None)
    monkeypatch.setattr("app.cli.run_direct", record("direct"))
    monkeypatch.setattr("app.cli.run_gen_assets", record("gen_assets"))
    monkeypatch.setattr("app.cli.run_timeline", record("timeline"))
    monkeypatch.setattr("app.cli.run_export", record("export"))
    return calls


def test_run_requires_text_when_direct_pending(tmp_path: Path, monkeypatch) -> None:
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["run", "proj_001"])
    assert result.exit_code == 1
    assert "--text" in result.output


def test_run_full_chain_yes(tmp_path: Path, monkeypatch) -> None:
    """--yes 跳过确认：五个节点依次执行，输出总耗时。"""
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])
    calls = _mock_pipeline(monkeypatch)

    result = runner.invoke(app, ["run", "proj_001", "--text", "你好。", "--yes"])
    assert result.exit_code == 0, result.output
    assert "run 完成" in result.output
    assert "总耗时" in result.output
    for node in ("direct", "gen_assets", "timeline", "export"):
        assert calls.get(node) == 1, f"{node} 未被调用: {calls}"
    # confirm 节点由真实 run_confirm 落盘
    project = ProjectStore(tmp_path / "data").load("proj_001")
    assert project.pipeline["confirm"] == "done"


def test_run_confirm_n_aborts(tmp_path: Path, monkeypatch) -> None:
    """confirm 输 n：退出、不标记失败、gen_assets 不执行。"""
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])
    _make_direct_done_project(tmp_path / "data")
    monkeypatch.setattr("app.cli._load_env", lambda: None)

    def boom(*args, **kwargs):
        raise AssertionError("confirm 未通过时 gen_assets 不应执行")

    monkeypatch.setattr("app.cli.run_gen_assets", boom)

    result = runner.invoke(app, ["run", "proj_001"], input="n\n")
    assert result.exit_code == 1
    assert "已取消" in result.output
    project = ProjectStore(tmp_path / "data").load("proj_001")
    assert project.pipeline["confirm"] == "pending"


def test_run_confirm_y_proceeds(tmp_path: Path, monkeypatch) -> None:
    """confirm 输 y：confirm 落盘 done，链条继续（gen_assets 被调用）。"""
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])
    _make_direct_done_project(tmp_path / "data")
    calls = _mock_pipeline(monkeypatch)

    result = runner.invoke(app, ["run", "proj_001"], input="y\n")
    assert result.exit_code == 0, result.output
    assert calls.get("gen_assets") == 1
    assert "分镜预览" in result.output
    project = ProjectStore(tmp_path / "data").load("proj_001")
    assert project.pipeline["confirm"] == "done"
