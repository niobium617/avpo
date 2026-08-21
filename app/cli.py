"""AVPO CLI —— MVP 无 UI，命令行驱动（阶段 1 加 Streamlit 工作台）。

用法：
    avpo new proj_001 --title "AI 产品口播"
    avpo direct proj_001 --text "口播文案全文"
    avpo gen-assets proj_001
    avpo timeline proj_001
    avpo export proj_001
    avpo status proj_001
"""

import os
import sys
import time
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.tree import Tree

from app.core.pipeline import run_confirm, run_direct, run_export, run_gen_assets, run_timeline
from app.core.project import ProjectStore
from app.core.providers import config_for_provider, make_director, make_image
from app.core.schema import Project
from app.tts.tts_edge import EdgeTTS

# 本机控制台是 GBK：¥/emoji 等字符打不出会抛 UnicodeEncodeError。
# 保留 GBK 编码（中文正常显示），不可编码字符替换为 ?，不崩溃。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass

console = Console()

# 默认数据目录 = AVPO/data；环境变量 AVPO_DATA 可覆盖（测试用）
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_env() -> None:
    """加载项目根 .env（不存在则跳过），把渠道 key/base_url 注入环境变量。"""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def _load_project_or_exit(store: ProjectStore, project_id: str) -> Project:
    try:
        return store.load(project_id)
    except FileNotFoundError:
        console.print(f"[red]项目不存在: {project_id}[/red]")
        raise typer.Exit(code=1)


def create_app(data_dir: Path = DEFAULT_DATA_DIR) -> typer.Typer:
    store = ProjectStore(data_dir)
    app = typer.Typer(help="AI 视频工作流操作系统 —— 连接剧本、AI 生成模型与剪映的中间层")

    @app.command()
    def new(
        project_id: str = typer.Argument(..., help="项目 ID，如 proj_001"),
        title: str = typer.Option("", "--title", "-t", help="项目标题"),
        voice: str = typer.Option("zh-CN-YunxiNeural", "--voice", help="TTS 音色"),
        provider: str = typer.Option("siliconflow", "--provider", help="LLM/生图渠道: siliconflow | dashscope"),
    ) -> None:
        """创建新项目：生成带默认配置的 project.json 并 git 提交。"""
        store.init_repo()
        try:
            config = config_for_provider(provider)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        project = Project(project_id=project_id, title=title or project_id, config=config)
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

        clips = project.timeline.video
        tl = root.add("[cyan]timeline[/cyan]")
        if clips:
            tl.add(
                f"{len(clips)} 段 总 {project.voiceover.duration_ms or '-'}ms  "
                f"末段止于 {clips[-1].start_ms + clips[-1].duration_ms}ms"
            )
        else:
            tl.add("空（未组装）")

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

    @app.command("gen-assets")
    def gen_assets(
        project_id: str = typer.Argument(..., help="项目 ID"),
    ) -> None:
        """gen_assets 节点：逐场景生成配音段 + 字幕 + 图（prompt_hash 缓存），归档入 assets/。"""
        _load_env()
        store.init_repo()
        project = _load_project_or_exit(store, project_id)
        try:
            image = make_image(project.config.image)
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        ok = run_gen_assets(store, project, tts=EdgeTTS(project.config.tts), image=image)
        if ok:
            console.print(f"[green]gen_assets 完成[/green] {len(project.scenes)} 场景归档")
        else:
            console.print(f"[red]gen_assets 失败[/red] {[e.error for e in project.errors]}")
            raise typer.Exit(code=1)

    @app.command()
    def timeline(
        project_id: str = typer.Argument(..., help="项目 ID"),
    ) -> None:
        """timeline 节点：逐场景素材组装全局时间轴（场景时长 = 配音实际时长）。"""
        store.init_repo()
        project = _load_project_or_exit(store, project_id)

        if run_timeline(store, project):
            total = project.voiceover.duration_ms or 0
            console.print(
                f"[green]timeline 完成[/green] {len(project.timeline.video)} 段视频轨 + "
                f"{len(project.timeline.voiceover)} 段音频轨，总时长 {total}ms"
            )
        else:
            console.print(f"[red]timeline 失败[/red] {[e.error for e in project.errors]}")
            raise typer.Exit(code=1)

    @app.command("export")
    def export_cmd(
        project_id: str = typer.Argument(..., help="项目 ID"),
    ) -> None:
        """export 节点：导出剪映草稿目录（三轨 + 字幕样式 + 淡入淡出）+ zip。"""
        store.init_repo()
        project = _load_project_or_exit(store, project_id)

        if run_export(store, project):
            console.print(f"[green]export 完成[/green] {project.export.path}")
            console.print("下一步: 拷贝草稿目录进剪映草稿路径后打开剪映验证")
        else:
            console.print(f"[red]export 失败[/red] {[e.error for e in project.errors]}")
            raise typer.Exit(code=1)

    @app.command()
    def run(
        project_id: str = typer.Argument(..., help="项目 ID"),
        text: str = typer.Option("", "--text", help="口播文案全文（direct 未完成时必需）"),
        yes: bool = typer.Option(False, "--yes", "-y", help="跳过分镜确认（自动化/重跑）"),
    ) -> None:
        """一键流水线：direct → confirm → gen_assets → timeline → export。

        已 done 的节点自动跳过（断点续跑）；失败的节点重试。
        confirm 节点展示分镜后等 y/n，n 则退出（改文案重跑 direct，不标记失败）。
        """
        _load_env()
        store.init_repo()
        project = _load_project_or_exit(store, project_id)
        start = time.time()
        times: list[str] = []

        def _fail(node: str) -> None:
            console.print(f"[red]run 在 {node} 节点失败[/red] {[e.error for e in project.errors]}")
            raise typer.Exit(code=1)

        # 1) direct：文案 → 分镜
        if project.pipeline["direct"] != "done":
            if not text.strip():
                console.print("[red]direct 未完成，需要 --text 提供口播文案[/red]")
                raise typer.Exit(code=1)
            try:
                director = make_director(project.config.llm)
            except KeyError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            t0 = time.time()
            if not run_direct(store, project, director, text):
                _fail("direct")
            times.append(f"direct {time.time() - t0:.1f}s")

        # 2) confirm：展示分镜，等 y/n（n 退出不标记失败，改文案重跑）
        if project.pipeline["confirm"] != "done":
            _print_storyboard(project)
            if not yes and not typer.confirm("分镜确认？"):
                console.print("已取消。修改文案后重新执行 avpo run --text，分镜将重新生成。")
                raise typer.Exit(code=1)
            run_confirm(store, project)

        # 3) gen_assets：配音 + 字幕 + 生图
        if project.pipeline["gen_assets"] != "done":
            try:
                image = make_image(project.config.image)
            except KeyError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            t0 = time.time()
            if not run_gen_assets(store, project, tts=EdgeTTS(project.config.tts), image=image):
                _fail("gen_assets")
            times.append(f"gen_assets {time.time() - t0:.1f}s")

        # 4) timeline → 5) export
        t0 = time.time()
        if not run_timeline(store, project):
            _fail("timeline")
        times.append(f"timeline {time.time() - t0:.1f}s")

        t0 = time.time()
        if not run_export(store, project):
            _fail("export")
        times.append(f"export {time.time() - t0:.1f}s")

        console.print(
            f"[green]run 完成[/green] {project.project_id} 全链路 done，"
            f"总耗时 {time.time() - start:.1f}s（{' / '.join(times)}）"
        )
        console.print(f"草稿: {project.export.path}")

    @app.command()
    def direct(
        project_id: str = typer.Argument(..., help="项目 ID"),
        text: str = typer.Option(..., "--text", help="口播文案全文"),
    ) -> None:
        """direct 节点：文案 → 分镜（LLM 强制 JSON），校验后写入 project.json。"""
        _load_env()
        store.init_repo()
        project = _load_project_or_exit(store, project_id)
        try:
            director = make_director(project.config.llm)
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if run_direct(store, project, director, text):
            console.print(f"[green]direct 完成[/green] {len(project.scenes)} 个分镜")
            for s in project.scenes:
                console.print(f"  {s.scene_id}  motion={s.motion}  narration={len(s.narration)}字")
        else:
            console.print(f"[red]direct 失败[/red] {[e.error for e in project.errors]}")
            raise typer.Exit(code=1)

    return app


def _style(status: str) -> str:
    return {"pending": "yellow", "running": "blue", "done": "green", "failed": "red"}.get(status, "white")


def _print_storyboard(project: Project) -> None:
    """confirm 前展示分镜：场景、运镜、文案、画面描述。"""
    console.print("[cyan]分镜预览[/cyan]")
    for s in project.scenes:
        console.print(f"  {s.scene_id} [{s.motion}] {s.narration}")
        console.print(f"    画面: {s.visual}")


app = create_app()

if __name__ == "__main__":
    app()
