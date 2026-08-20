"""任务状态机（IMPLEMENTATION_PLAN §5）。

一行规则：run_task 前检查该节点 status ——
    done 跳过；failed/pending/running 执行。
执行前写 running 并立即 commit；成功写 done 并 commit；
失败写 failed + 错误摘要并 commit。
**状态与产物永远同一次提交落盘** —— 这是断点续跑可靠的前提。

fn 的契约：
- fn(project) 只改内存中的 project + 写产物文件，不自己 save；
- fn 必须可重入：重试时重复执行覆盖同一产物/同一 asset_id；
- 网络/API 类失败抛 TransientError（重试 ×3，指数退避）；
- 格式/剪映类失败抛 FatalError（不重试）；其余异常按 Transient 处理。
"""

import time
from typing import Callable

from app.core.project import ProjectStore
from app.core.schema import PipelineError, Project

MAX_ATTEMPTS = 3


class TransientError(Exception):
    """网络/API 类失败 —— 值得重试。"""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class FatalError(Exception):
    """格式/剪映/参数类失败 —— 重试无意义，报修复动作。"""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


def run_task(store: ProjectStore, project: Project, node: str, fn: Callable[[Project], None]) -> bool:
    """执行 pipeline 节点：done 跳过，失败重试 ×3，状态与产物同一次提交落盘。"""
    if node not in project.pipeline:
        raise KeyError(f"未知 pipeline 节点: {node}")

    if project.pipeline[node] == "done":
        return True

    project.pipeline[node] = "running"
    store.save(project, message=f"{project.project_id}: {node} running")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            fn(project)
        except FatalError as exc:
            _mark_failed(store, project, node, exc)
            return False
        except Exception as exc:  # noqa: BLE001 —— TransientError 与未分类异常都重试
            if attempt == MAX_ATTEMPTS:
                _mark_failed(store, project, node, exc)
                return False
            time.sleep(2 ** (attempt - 1))     # 1s, 2s
            continue
        project.pipeline[node] = "done"
        store.save(project, message=f"{project.project_id}: {node} done")
        return True

    raise AssertionError("不可达")  # pragma: no cover


def _mark_failed(store: ProjectStore, project: Project, node: str, exc: Exception) -> None:
    project.pipeline[node] = "failed"
    hint = getattr(exc, "hint", None)
    message = str(exc)
    if hint:
        message = f"{message}（{hint}）"
    # 错误摘要持久化到 project.json：status 命令也能读到
    project.errors.append(PipelineError(node=node, error=message))
    store.save(project, message=f"{project.project_id}: {node} failed")
