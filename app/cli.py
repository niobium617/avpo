"""AVPO CLI —— MVP 无 UI，命令行驱动（阶段 1 加 Streamlit 工作台）。

用法：
    avpo new proj_001 --title "AI 产品口播"
    avpo status proj_001
"""

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.tree import Tree

from app.core.project import ProjectStore
from app.core.schema import Project

console = Console()

# 默认数据目录 = AVPO/data；环境变量 AVPO_DATA 可覆盖（测试用）
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def create_app(data_dir: Path = DEFAULT_DATA_DIR) -> typer.Typer:
    store = ProjectStore(data_dir)
    app = typer.Typer(help="AI 视频工作流操作系统 —— 连接剧本、AI 生成模型与剪映的中间层")

    @app.command()
    def new(
        project_id: str = typer.Argument(..., help="项目 ID，如 proj_001"),
        title: str = typer.Option("", "--title", "-t", help="项目标题"),
        voice: str = typer.Option("zh-CN-YunxiNeural", "--voice", help="TTS 音色"),
    ) -> None:
        """创建新项目：生成带默认配置的 project.json 并 git 提交。"""
        store.init_repo()
        project = Project(project_id=project_id, title=title or project_id)
        project.config.tts.voice = voice
        try:
            store.create(project)
        except FileExistsError:
            console.print(f"[red]项目已存在: {project_id}[/red]")
            raise typer.Exit(code=1)
        console.print(f"[green]项目已创建[/green] {store.json_path(project_id)}")
        console.print(f"下一步: avpo status {project_id}")

    @app.command()
    def status(
        project_id: str = typer.Argument(..., help="项目 ID"),
    ) -> None:
        """打印项目状态树：pipeline 节点、场景、配音、字幕、素材、导出。"""
        try:
            project = store.load(project_id)
        except FileNotFoundError:
            console.print(f"[red]项目不存在: {project_id}[/red]")
            raise typer.Exit(code=1)

        root = Tree(f"[bold]{project.project_id}[/bold] 「{project.title or '未命名'}」 schema v{project.schema_version}")

        pipe = root.add("[cyan]pipeline[/cyan]")
        for node, st in project.pipeline.items():
            pipe.add(f"{node}  [{_style(st)}]{st}[/{_style(st)}]")

        scenes = root.add(f"[cyan]scenes[/cyan] ({len(project.scenes)})")
        for s in project.scenes:
            scenes.add(
                f"{s.scene_id}  [{_style(s.status)}]{s.status}[/{_style(s.status)}]  "
                f"motion={s.motion}  img={s.image_asset_id or '-'}  "
                f"文案 {len(s.narration)} 字"
            )

        vo = root.add("[cyan]voiceover[/cyan]")
        vo.add(
            f"[{_style(project.voiceover.status)}]{project.voiceover.status}[/{_style(project.voiceover.status)}]  "
            f"asset={project.voiceover.asset_id or '-'}  "
            f"duration_ms={project.voiceover.duration_ms or '-'}"
        )

        root.add(f"[cyan]subtitles[/cyan] ({len(project.subtitles)})")

        assets = root.add(f"[cyan]assets[/cyan] ({len(project.assets)})")
        for aid, a in project.assets.items():
            assets.add(
                f"{aid}  {a.type}  [{_style(a.status)}]{a.status}[/{_style(a.status)}]  "
                f"{a.path}  cost={a.cost}"
            )

        root.add(
            f"[cyan]export[/cyan]  {project.export.format}  "
            f"[{_style(project.export.status)}]{project.export.status}[/{_style(project.export.status)}]  "
            f"path={project.export.path or '-'}"
        )

        console.print(root)

    return app


def _style(status: str) -> str:
    return {"pending": "yellow", "running": "blue", "done": "green", "failed": "red"}.get(status, "white")


app = create_app()

if __name__ == "__main__":
    app()
