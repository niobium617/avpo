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

def test_direct_missing_key_exits_1(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli._load_env", lambda: None)      # 无 .env 密钥
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["direct", "proj_001", "--text", "你好。"])
    assert result.exit_code == 1
    assert "SILICONFLOW_API_KEY" in result.output


def test_direct_runs_with_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli._load_env", lambda: "sk-test")
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
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["gen-assets", "proj_001"])
    assert result.exit_code == 1
    assert "SILICONFLOW_API_KEY" in result.output


def test_gen_assets_runs_with_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli._load_env", lambda: "sk-test")
    monkeypatch.setattr("app.cli.run_gen_assets", lambda store, p, tts, image: True)
    app = create_app(tmp_path / "data")
    runner = CliRunner()
    runner.invoke(app, ["new", "proj_001"])

    result = runner.invoke(app, ["gen-assets", "proj_001"])
    assert result.exit_code == 0, result.output
    assert "gen_assets 完成" in result.output
