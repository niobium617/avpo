"""M4-5.3~5.6 验收：Streamlit 工作台四页 AppTest 全流程。

约定：
- AVPO_DATA 环境变量必须在首次 at.run() 之前设置（脚本每次执行都读它）；
- 工作台脚本对 core 走模块引用（pipeline.run_*），monkeypatch 按 app.core.* 模块属性打补丁；
- widget 改动后必须显式 at.run()（AppTest 不自动重跑）；
- 全链路节点全 mock，无真实 API 等待。
"""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.core.project import ProjectStore
from app.core.schema import Project, Scene

APP = Path(__file__).resolve().parents[1] / "app" / "web" / "app.py"

SCENES = [
    Scene(scene_id="s1", narration="你好。", visual="旧画面", image_prompt="old p", motion="none"),
    Scene(scene_id="s2", narration="世界。", visual="旧画面2", image_prompt="old p2", motion="pan_left"),
]


@pytest.fixture()
def at(tmp_path, monkeypatch):
    """AppTest 会话：AVPO_DATA 指向测试临时数据目录（先设环境再建会话）。"""
    monkeypatch.setenv("AVPO_DATA", str(tmp_path / "data"))
    return AppTest.from_file(str(APP), default_timeout=30)


def _store() -> ProjectStore:
    return ProjectStore(Path(os.environ["AVPO_DATA"]))


def _mk_project(pid: str = "proj_ui", scenes=None, pipeline=None) -> Project:
    store = _store()
    store.init_repo()
    project = Project(project_id=pid, title="UI 测试")
    if scenes is not None:
        project.scenes = scenes
    if pipeline:
        project.pipeline.update(pipeline)
    store.create(project)
    return project


def _goto(at: AppTest, section: str, pid: str | None = None) -> None:
    if pid:
        at.sidebar.selectbox(key="pid").set_value(pid)
    at.sidebar.radio(key="section").set_value(section)
    at.run()


def _mock_pipeline(monkeypatch) -> list[str]:
    """mock 三节点与 provider 构造，返回调用顺序记录；节点 mock 把 pipeline 置 done。"""
    calls: list[str] = []

    def rec(name: str):
        def fake(*args, **kwargs):
            calls.append(name)
            if len(args) > 1:
                args[1].pipeline[name] = "done"          # 模拟真实节点落盘
                args[0].save(args[1], message=f"web 测试 {name}")
            return True
        return fake

    monkeypatch.setattr("app.core.pipeline.run_direct", rec("direct"))
    monkeypatch.setattr("app.core.pipeline.run_gen_assets", rec("gen_assets"))
    monkeypatch.setattr("app.core.pipeline.run_timeline", rec("timeline"))
    monkeypatch.setattr("app.core.pipeline.run_export", rec("export"))
    monkeypatch.setattr("app.core.providers.make_director", lambda config: object())
    monkeypatch.setattr("app.core.providers.make_image", lambda config: object())
    monkeypatch.setattr("app.core.env.load_project_env", lambda: None)
    return calls


# ---------------------------------------------------------------- 页面 1：项目管理

def test_empty_data_dir_shows_create_form(at: AppTest) -> None:
    at.run()
    assert not at.exception
    assert any("暂无项目" in i.value for i in at.info)
    assert at.button(key="btn_create")


def test_create_project_form(at: AppTest) -> None:
    at.run()
    at.text_input(key="new_pid").set_value("proj_ui")
    at.text_input(key="new_title").set_value("UI 测试项目")
    at.button(key="btn_create").click()
    at.run()

    assert not at.exception
    project = _store().load("proj_ui")
    assert project.title == "UI 测试项目"
    assert project.config.style == "default"
    assert project.config.tts.voice == "zh-CN-YunxiNeural"
    # 创建成功后自动选中
    assert at.sidebar.selectbox(key="pid").value == "proj_ui"


# ---------------------------------------------------------------- 页面 2：流水线

def test_run_all_calls_nodes_in_order(at: AppTest, monkeypatch) -> None:
    calls = _mock_pipeline(monkeypatch)
    _mk_project(scenes=SCENES, pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_all_proj_ui").click()
    at.run()

    assert not at.exception
    assert calls == ["gen_assets", "timeline", "export"]
    # 全链路后 pipeline 全 done（节点 mock 落盘 + rerun 后徽章状态）
    loaded = _store().load("proj_ui")
    assert all(v == "done" for v in loaded.pipeline.values())


def test_run_all_stops_at_unconfirmed(at: AppTest, monkeypatch) -> None:
    calls = _mock_pipeline(monkeypatch)
    _mk_project(scenes=SCENES, pipeline={"direct": "done"})   # confirm 未确认

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_all_proj_ui").click()
    at.run()

    assert not at.exception
    assert calls == []                                          # 未确认 → 中断，不跑任何节点
    assert any("分镜待确认" in w.value for w in at.warning)


def test_single_node_buttons(at: AppTest, monkeypatch) -> None:
    calls = _mock_pipeline(monkeypatch)
    _mk_project(pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_tl_proj_ui").click()
    at.run()
    at.button(key="run_exp_proj_ui").click()
    at.run()

    assert not at.exception
    assert calls == ["timeline", "export"]


# ---------------------------------------------------------------- 页面 3：分镜确认

def test_storyboard_edit_saves_and_invalidates(at: AppTest) -> None:
    _mk_project(scenes=SCENES, pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "分镜确认", pid="proj_ui")
    assert at.text_area(key="visual_proj_ui_s1").value == "旧画面"

    at.text_area(key="visual_proj_ui_s1").set_value("新画面")
    at.run()                                                    # 先重跑：edited=True → 保存按钮可用
    at.button(key="save_scenes_proj_ui").click()
    at.run()

    assert not at.exception
    loaded = _store().load("proj_ui")
    assert loaded.scenes[0].visual == "新画面"
    assert loaded.scenes[0].narration == "你好。"               # narration 未被动过
    assert loaded.pipeline["direct"] == "done"                  # 上游不变
    assert loaded.pipeline["confirm"] == "pending"              # 下游全部重置
    assert loaded.pipeline["gen_assets"] == "pending"


def test_confirm_button_marks_done(at: AppTest) -> None:
    _mk_project(scenes=SCENES, pipeline={"direct": "done"})     # confirm 待确认

    at.run()
    _goto(at, "分镜确认", pid="proj_ui")
    at.button(key="confirm_proj_ui").click()
    at.run()

    assert not at.exception
    assert _store().load("proj_ui").pipeline["confirm"] == "done"


# ---------------------------------------------------------------- 页面 4：成本面板

def test_cost_panel_metrics_and_budget(at: AppTest) -> None:
    scenes = [Scene(scene_id="s1", narration="你好。", visual="v", image_prompt="p",
                    cost={"image": 3.0, "llm": 0.5})]
    _mk_project(scenes=scenes)

    at.run()
    _goto(at, "成本面板", pid="proj_ui")

    assert at.metric[0].value == "3.5000"                       # 总成本
    assert any("预算内" in s.value for s in at.success)          # 默认预算 5

    at.number_input(key="budget").set_value(3.0)
    at.run()
    assert any("超出预算" in e.value for e in at.error)


# ---------------------------------------------------------------- 容错

def test_corrupt_project_json_shows_inline_error(at: AppTest) -> None:
    store = _store()
    store.init_repo()
    bad = store.project_dir("bad_proj")
    bad.mkdir(parents=True)
    (bad / "project.json").write_text("{ 损坏", encoding="utf-8")

    at.run()
    assert not at.exception
    assert any("bad_proj" in e.value for e in at.error)
