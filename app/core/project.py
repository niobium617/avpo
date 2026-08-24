"""ProjectStore —— project.json 的原子读写 + git 版本快照。

目录约定（EXECUTION_PLAN §4）：
    data/projects/<project_id>/project.json
    data/projects/<project_id>/assets/
    data/projects/<project_id>/exports/

写入协议（IMPLEMENTATION_PLAN §5）：
    写 project.json.tmp → os.replace（原子）→ git commit
    三步顺序执行，进程死在任意一步都不损坏现有数据。
    状态与产物永远同一次提交落盘 —— 这是断点续跑可靠的前提。
"""

import json
import subprocess
import threading
from pathlib import Path

from pydantic import ValidationError

from app.core.schema import Project

TMP_SUFFIX = ".tmp"

# M5：单进程内串行化「原子写 + git 提交」——git index 非线程安全，后台任务线程与
# 多个浏览器会话并发 save 时会竞争损坏。跨进程（CLI 与 Web 同时跑）不受此锁保护，
# 记为已知限制。
_SAVE_LOCK = threading.Lock()


class ProjectStore:
    """读写 data/projects/<project_id>/project.json，并以 git 提交做版本快照。"""

    def __init__(self, data_dir: Path | str, git: bool = True):
        self.data_dir = Path(data_dir)
        self.projects_dir = self.data_dir / "projects"
        self.git = git

    # ------------------------------------------------------------ 路径

    def project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def json_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def exists(self, project_id: str) -> bool:
        return self.json_path(project_id).is_file()

    # ------------------------------------------------------------ git

    def init_repo(self) -> None:
        """在 data/ 下初始化 git 仓库（版本历史免费获得）。不覆盖已有仓库。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not (self.data_dir / ".git").exists():
            self._git("init")
        # 身份只配仓库级，不动用户全局（C 盘）配置
        if not self._git("config", "--get", "user.name", check=False).strip():
            self._git("config", "user.name", "avpo")
            self._git("config", "user.email", "avpo@local")

    def commit(self, message: str) -> str | None:
        """提交 data/ 全部变更，返回 commit hash；未启用 git 或无可提交时返回 None。"""
        if not self.git:
            return None
        self._git("add", "-A")
        # 空提交无意义；diff --cached --quiet 返回码 0 = 无差异，1 = 有差异
        if not self._has_staged_changes():
            return None
        self._git("commit", "-m", message)
        return self._git("rev-parse", "--short", "HEAD").strip()

    def _has_staged_changes(self) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.data_dir), "diff", "--cached", "--quiet"],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 1

    def _git(self, *args: str, check: bool = True) -> str:
        """在 data/ 目录内执行 git。"""
        proc = subprocess.run(
            ["git", "-C", str(self.data_dir), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
        return proc.stdout

    # ------------------------------------------------------------ 读写

    def create(self, project: Project) -> Project:
        """新建项目：建目录、写 project.json、git 提交。"""
        if self.exists(project.project_id):
            raise FileExistsError(f"项目已存在: {project.project_id}")
        self.save(project, message=f"{project.project_id}: 创建项目")
        return project

    def load(self, project_id: str) -> Project:
        """读取并校验 project.json；不存在/损坏都会报错，不做静默兜底。"""
        path = self.json_path(project_id)
        if not path.is_file():
            raise FileNotFoundError(f"项目不存在: {project_id}（{path}）")
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def list_project_ids(self) -> list[str]:
        """枚举项目 id：projects/ 下含 project.json 的目录名，按名排序。

        只检查文件存在、不解析内容 —— 一个损坏的 project.json 不会让枚举崩溃
        （UI 项目选择器用；选中后的加载错误由调用方展示）。
        """
        if not self.projects_dir.is_dir():
            return []
        return sorted(
            d.name for d in self.projects_dir.iterdir()
            if d.is_dir() and (d / "project.json").is_file()
        )

    def list_projects(self) -> list[Project]:
        """枚举全部项目对象；坏 JSON 抛 ValueError（含项目 id），不静默兜底。"""
        projects: list[Project] = []
        for pid in self.list_project_ids():
            try:
                projects.append(self.load(pid))
            except ValidationError as exc:
                raise ValueError(f"项目 {pid} 的 project.json 损坏: {exc}") from exc
        return projects

    def save(self, project: Project, message: str | None = None) -> Project:
        """原子写 + git 提交。message 缺省时用 project_id 生成。"""
        with _SAVE_LOCK:                        # 原子写与 git 提交一次持锁（create 也走 save）
            path = self.json_path(project.project_id)
            path.parent.mkdir(parents=True, exist_ok=True)

            text = json.dumps(project.model_dump(mode="json"), ensure_ascii=False, indent=2)

            # 原子写：先写 tmp 再替换，进程中途死亡不损坏 project.json
            tmp = path.with_name(path.name + TMP_SUFFIX)
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)

            if self.git:
                self.commit(message or f"{project.project_id}: 保存")

        return project
