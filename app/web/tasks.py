"""M5 后台任务执行器：worker 线程 + 线程安全进度容器。

线程纪律（已核实 streamlit 1.62 源码）：
- worker 线程绝不调用任何 st.* API —— st.session_state 在无 ScriptRunContext 的
  线程里会落到全局 mock 单例（session_state_proxy.py:52-65），与本会话脱钩；
- worker 只写 container（内部 Lock 保护），UI 轮询线程只读 snapshot()；
- 单会话单任务：start_task 由 UI 在保证「无运行中任务」后调用（按钮禁用兜底）。
"""

import threading
from dataclasses import dataclass, field

from app.core import errors, pipeline, providers
from app.core.progress import ProgressEvent
from app.core.project import ProjectStore
from app.core.schema import Project
from app.tts.tts_edge import EdgeTTS


@dataclass
class TaskContainer:
    """后台任务状态：worker 写、UI 读。state: running | done | failed | aborted。"""
    node: str                              # 节点名；"chain" = 一键全链路
    events: list[ProgressEvent] = field(default_factory=list)
    state: str = "running"
    error: str = ""
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, ev: ProgressEvent) -> None:   # 直接作为 pipeline progress 回调
        with self._lock:
            self.events.append(ev)

    def snapshot(self) -> tuple[str, list[ProgressEvent], str]:
        with self._lock:
            return self.state, list(self.events), self.error

    def finish(self, state: str, error: str = "") -> None:
        with self._lock:
            self.state, self.error = state, error
        self.done.set()


def start_task(store: ProjectStore, project: Project, node: str,
               text: str = "", *, chain: bool = False) -> TaskContainer:
    """启动后台任务（主脚本线程调用，立即返回）。daemon 线程：进程退出不滞留。"""
    container = TaskContainer(node="chain" if chain else node)
    args = (store, project, text, container) if chain else (store, project, node, text, container)
    threading.Thread(
        target=_run_chain if chain else _run_node,
        args=args,
        name=f"avpo-{container.node}", daemon=True,
    ).start()
    return container


def _run_node(store: ProjectStore, project: Project, node: str, text: str,
              container: TaskContainer) -> None:
    """单节点 worker：镜像原 app.py _run_node 语义。一切异常都落到容器（绝不吞、绝不 st.*）。"""
    try:
        if node == "direct":
            director = providers.make_director(project.config.llm)
            ok = pipeline.run_direct(store, project, director, text, progress=container.emit)
        elif node == "confirm":
            ok = pipeline.run_confirm(store, project, progress=container.emit)
        elif node == "gen_assets":
            image = providers.make_image(project.config.image)
            ok = pipeline.run_gen_assets(
                store, project, EdgeTTS(project.config.tts), image, progress=container.emit
            )
        elif node == "timeline":
            ok = pipeline.run_timeline(store, project, progress=container.emit)
        else:  # export
            ok = pipeline.run_export(store, project, progress=container.emit)
    except Exception as exc:  # noqa: BLE001 —— 兜底：任何异常都必须进容器
        container.finish("failed", f"{node} 异常: {exc}")
        return
    if ok:
        container.finish("done")
    else:
        container.finish("failed", errors.describe_list(project.errors))


def _run_chain(store: ProjectStore, project: Project, text: str,
               container: TaskContainer) -> None:
    """一键全链路 worker：镜像 CLI run 语义（done 跳过、失败即停）。confirm 由 UI 前置拦截。"""
    try:
        if project.pipeline["direct"] != "done":
            if not text.strip():
                container.finish("aborted", "direct 未完成：请先填写口播文案，或单独运行 direct")
                return
            director = providers.make_director(project.config.llm)
            if not pipeline.run_direct(store, project, director, text, progress=container.emit):
                container.finish("failed", errors.describe_list(project.errors))
                return
        if project.pipeline["gen_assets"] != "done":
            image = providers.make_image(project.config.image)
            if not pipeline.run_gen_assets(
                store, project, EdgeTTS(project.config.tts), image, progress=container.emit
            ):
                container.finish("failed", errors.describe_list(project.errors))
                return
        if not pipeline.run_timeline(store, project, progress=container.emit):
            container.finish("failed", errors.describe_list(project.errors))
            return
        if not pipeline.run_export(store, project, progress=container.emit):
            container.finish("failed", errors.describe_list(project.errors))
            return
    except Exception as exc:  # noqa: BLE001
        container.finish("failed", f"全链路异常: {exc}")
        return
    container.finish("done")
