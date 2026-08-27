"""M4-5.3~5.6 验收：Streamlit 工作台四页 AppTest 全流程。M5-6.2 起后台任务异步模式。

约定：
- AVPO_DATA 环境变量必须在首次 at.run() 之前设置（脚本每次执行都读它）；
- 工作台脚本对 core 走模块引用（pipeline.run_*），monkeypatch 按 app.core.* 模块属性打补丁；
- widget 改动后必须显式 at.run()（AppTest 不自动重跑）；
- 全链路节点全 mock，无真实 API 等待；
- M5 后台任务：点击按钮后 worker 线程异步执行，测试用 _wait_task 等容器 done Event，
  再 at.run() 让进度 fragment 消费任务（st.rerun 链在同一 run 内处理）。
"""

import os
import threading
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.core import edits
from app.core.progress import ProgressEvent
from app.core.project import ProjectStore
from app.core.providers import ImageCapabilities
from app.core.schema import Asset, PipelineError, Project, Scene

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
    monkeypatch.setattr("app.core.pipeline.run_animate", rec("animate"))
    monkeypatch.setattr("app.core.pipeline.run_timeline", rec("timeline"))
    monkeypatch.setattr("app.core.pipeline.run_export", rec("export"))
    monkeypatch.setattr("app.core.providers.make_director", lambda config: object())
    monkeypatch.setattr("app.core.providers.make_image", lambda config: object())
    monkeypatch.setattr("app.core.env.load_project_env", lambda: None)
    return calls


def _ss(at: AppTest, key: str):
    """AppTest 的 SafeSessionState 没有 .get：用 in + [] 读取。"""
    return at.session_state[key] if key in at.session_state else None


def _wait_task(at: AppTest, timeout: float = 10.0) -> None:
    """等待后台任务完成。worker 线程独立于脚本线程：直接等容器的 done Event。

    mock 秒回时任务可能已被同一次 at.run() 里的 fragment 消费（task 为 None）——
    此时断言结果区已生成即视为任务跑完。
    """
    task = _ss(at, "task")
    if task is None:
        assert _ss(at, "task_result") is not None, "未启动后台任务也无结果"
        return
    assert task.done.wait(timeout), f"后台任务 {task.node} 超时（state={task.state}）"


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


def test_create_second_project_auto_selects(at: AppTest) -> None:
    """回归：已有项目时 selectbox(key=pid) 已实例化，再建项目不得写 widget key 崩溃。"""
    _mk_project(pid="proj_a")
    at.run()
    at.text_input(key="new_pid").set_value("proj_b")
    at.button(key="btn_create").click()
    at.run()

    assert not at.exception
    assert _store().exists("proj_b")
    assert at.sidebar.selectbox(key="pid").value == "proj_b"


def test_pick_button_switches_project(at: AppTest) -> None:
    """回归：项目卡片「选择」按钮经 pending_pid 预设，不直写 widget key。"""
    _mk_project(pid="proj_a")
    _mk_project(pid="proj_b")
    at.run()

    at.button(key="pick_proj_b").click()
    at.run()

    assert not at.exception
    assert at.sidebar.selectbox(key="pid").value == "proj_b"


# ---------------------------------------------------------------- 页面 4：流水线

def test_run_all_calls_nodes_in_order(at: AppTest, monkeypatch) -> None:
    calls = _mock_pipeline(monkeypatch)
    _mk_project(scenes=SCENES, pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_all_proj_ui").click()
    at.run()
    _wait_task(at)
    at.run()                                                    # fragment 消费任务 + 刷新

    assert not at.exception
    assert calls == ["gen_assets", "animate", "timeline", "export"]
    # 全链路后 pipeline 全 done（节点 mock 落盘 + rerun 后徽章状态）
    loaded = _store().load("proj_ui")
    assert all(v == "done" for v in loaded.pipeline.values())
    assert _ss(at, "task") is None                 # 任务已消费
    assert any("chain 完成" in s.value for s in at.success)


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
    assert _ss(at, "task") is None                 # 未启动任务


def test_single_node_buttons(at: AppTest, monkeypatch) -> None:
    calls = _mock_pipeline(monkeypatch)
    _mk_project(pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_tl_proj_ui").click()
    at.run()
    _wait_task(at)
    at.run()
    at.button(key="run_exp_proj_ui").click()
    at.run()
    _wait_task(at)
    at.run()

    assert not at.exception
    assert calls == ["timeline", "export"]


# ---------------------------------------------------------------- M5 后台任务

def test_node_run_is_non_blocking_with_live_progress(at: AppTest, monkeypatch) -> None:
    """核心验收：点击后主脚本立即返回（不阻塞），运行中按钮禁用，完成后恢复并落盘。"""
    calls = _mock_pipeline(monkeypatch)
    started = threading.Event()

    def slow_timeline(store, p, progress=None):
        calls.append("timeline")
        started.set()
        time.sleep(2.0)
        p.pipeline["timeline"] = "done"
        store.save(p, message="web 测试 timeline")
        return True

    monkeypatch.setattr("app.core.pipeline.run_timeline", slow_timeline)
    _mk_project(pipeline={"direct": "done", "confirm": "done", "gen_assets": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_tl_proj_ui").click()
    at.run()                                    # 若主脚本阻塞，30s 超时抛错 —— 秒回即证非阻塞
    at.run()                                    # busy 在 run 顶部计算：再跑一次才渲染禁用态

    assert not at.exception
    assert at.session_state["task"].state == "running"
    assert at.button(key="run_tl_proj_ui").disabled is True     # 运行中禁用
    assert at.button(key="run_all_proj_ui").disabled is True
    assert started.wait(1.0)                    # worker 真在跑（2s 慢 mock 未完成）
    _wait_task(at)
    at.run()                                    # fragment 消费 + 刷新

    assert _ss(at, "task") is None
    assert any("timeline 完成" in s.value for s in at.success)
    assert at.button(key="run_tl_proj_ui").disabled is False    # 完成恢复
    assert _store().load("proj_ui").pipeline["timeline"] == "done"


def test_double_start_prevention(at: AppTest, monkeypatch) -> None:
    """运行中所有按钮禁用：点不动的按钮不会开出第二个任务。"""
    calls = _mock_pipeline(monkeypatch)

    def slow_timeline(store, p, progress=None):
        calls.append("timeline")
        time.sleep(1.0)
        p.pipeline["timeline"] = "done"
        store.save(p, message="web 测试 timeline")
        return True

    monkeypatch.setattr("app.core.pipeline.run_timeline", slow_timeline)
    _mk_project(pipeline={"direct": "done", "confirm": "done", "gen_assets": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_tl_proj_ui").click()
    at.run()
    at.run()                                    # 再跑一次仍在运行：按钮保持禁用

    assert at.button(key="run_exp_proj_ui").disabled is True
    at.button(key="run_exp_proj_ui").click()    # 点禁用按钮：不触发回调
    at.run()
    _wait_task(at)
    at.run()

    assert not at.exception
    assert calls == ["timeline"]                # 未双开


def test_node_failure_path(at: AppTest, monkeypatch) -> None:
    """节点失败：错误进容器 → 消费后持久错误区展示，落盘 failed，按钮恢复。"""
    _mock_pipeline(monkeypatch)

    def fake_fail(store, p, tts, image, progress=None):
        p.pipeline["gen_assets"] = "failed"
        p.errors.append(PipelineError(node="gen_assets", error="测试失败", kind="UNKNOWN", hint=""))
        store.save(p, message="web 测试失败")
        return False

    monkeypatch.setattr("app.core.pipeline.run_gen_assets", fake_fail)
    _mk_project(scenes=SCENES, pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_gen_proj_ui").click()
    at.run()
    _wait_task(at)
    at.run()

    assert not at.exception
    assert any("测试失败" in e.value for e in at.error)
    assert _ss(at, "task") is None
    assert _store().load("proj_ui").pipeline["gen_assets"] == "failed"
    assert at.button(key="run_gen_proj_ui").disabled is False


def test_chain_aborted_missing_text(at: AppTest, monkeypatch) -> None:
    """一键全链路缺文案：worker aborted 分支，警告持久展示，无节点运行。"""
    calls = _mock_pipeline(monkeypatch)
    _mk_project(scenes=SCENES, pipeline={"confirm": "done"})   # direct 待跑、文案留空

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_all_proj_ui").click()
    at.run()
    _wait_task(at)
    at.run()

    assert not at.exception
    assert calls == []
    assert any("direct 未完成" in w.value for w in at.warning)
    assert _ss(at, "task") is None


def test_progress_events_recorded(at: AppTest, monkeypatch) -> None:
    """worker 进度事件进容器：消费前断言事件序列完整（AppTest 无 progress 元素类）。"""
    _mock_pipeline(monkeypatch)

    def fake_gen(store, p, tts, image, progress=None):
        for msg, pct in [("配音 1/2", 0.25), ("配音 2/2", 0.5), ("生图完成", 1.0)]:
            progress(ProgressEvent(node="gen_assets", message=msg, percent=pct))
        time.sleep(0.3)                         # 确保点击 run 时 fragment 读到 running
        p.pipeline["gen_assets"] = "done"
        store.save(p, message="web 测试 gen_assets")
        return True

    monkeypatch.setattr("app.core.pipeline.run_gen_assets", fake_gen)
    _mk_project(scenes=SCENES, pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_gen_proj_ui").click()
    at.run()
    _wait_task(at)

    task = at.session_state["task"]
    assert task is not None                    # 尚未被消费（sleep 保证 running 窗口）
    assert [(e.message, e.percent) for e in task.events] == [
        ("配音 1/2", 0.25), ("配音 2/2", 0.5), ("生图完成", 1.0),
    ]
    at.run()                                    # 消费
    assert _ss(at, "task") is None


def test_task_container_cleaned_between_tasks(at: AppTest, monkeypatch) -> None:
    """前一任务消费后能立刻开下一任务：task_result 清空、新容器正常。"""
    _mock_pipeline(monkeypatch)
    _mk_project(pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")
    at.button(key="run_tl_proj_ui").click()
    at.run()
    _wait_task(at)
    at.run()                                    # 消费 timeline → task_result 有值

    at.button(key="run_exp_proj_ui").click()
    at.run()                                    # 启动 export：清 task_result、新容器

    assert not at.exception
    assert _ss(at, "task_result") is None
    assert _ss(at, "task") is not None
    _wait_task(at)
    at.run()

    assert _ss(at, "task") is None
    assert any("export 完成" in s.value for s in at.success)
    assert _store().load("proj_ui").pipeline["export"] == "done"


# ---------------------------------------------------------------- 页面 2：策划（M6-7.7）

BRIEF_LONG_FIELDS = {"theme", "worldview", "protagonist", "plot", "character", "bgm_hint"}
BRIEF_SHORT_FIELDS = {"art_style", "duration", "platform", "emotion", "palette", "lighting"}


def test_brief_page_renders_all_fields(at: AppTest) -> None:
    """策划页渲染 Brief 全部 12 字段 + 两个上传器；默认 siliconflow 显示能力降级提示。"""
    _mk_project()
    at.run()
    _goto(at, "策划", pid="proj_ui")

    assert not at.exception
    for field in BRIEF_LONG_FIELDS:
        assert at.text_area(key=f"brief_{field}_proj_ui").value == ""
    for field in BRIEF_SHORT_FIELDS:
        assert at.text_input(key=f"brief_{field}_proj_ui").value == ""
    assert at.file_uploader(key="refup_proj_ui")
    assert at.file_uploader(key="bgmup_proj_ui")
    # 默认渠道 siliconflow（FLUX）不支持参考图注入 → 降级提示
    assert any("不支持参考图" in c.value for c in at.caption)


def test_brief_save_persists_and_invalidates_from_direct(at: AppTest) -> None:
    _mk_project()
    at.run()
    _goto(at, "策划", pid="proj_ui")

    at.text_area(key="brief_theme_proj_ui").set_value("赛博朋克都市")
    at.run()                                                    # edited=True → 保存按钮可用
    at.button(key="save_brief_proj_ui").click()
    at.run()

    assert not at.exception
    loaded = _store().load("proj_ui")
    assert loaded.brief.theme == "赛博朋克都市"
    assert loaded.pipeline["direct"] == "pending"              # 简报是 direct 的输入
    assert loaded.pipeline["confirm"] == "pending"
    assert loaded.pipeline["export"] == "pending"


def _tiny_png() -> bytes:
    """合法 1x1 PNG（st.image 会用 PIL 解码，假字节会抛 UnidentifiedImageError）。"""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)    # 1x1, 8-bit, RGB
    idat = zlib.compress(b"\x00\xff\x00\x00")             # 一个绿像素
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_reference_list_and_delete(at: AppTest, tmp_path) -> None:
    """参考图列表展示 id/angle/role，删除按钮走 remove_reference_image 并失效 gen_assets。"""
    store = _store()
    store.init_repo()
    project = Project(project_id="proj_ui", title="UI 测试")
    store.create(project)
    src = tmp_path / "ref.png"
    src.write_bytes(_tiny_png())
    edits.add_reference_image(store, project, src, angle="正面", role="主角")

    at.run()
    _goto(at, "策划", pid="proj_ui")

    assert not at.exception
    assert any("ref_1" in m.value for m in at.markdown)
    at.button(key="delref_proj_ui_ref_1").click()
    at.run()

    assert not at.exception
    loaded = _store().load("proj_ui")
    assert loaded.reference_images == []
    assert loaded.pipeline["gen_assets"] == "pending"


def test_bgm_uploader_and_convention_caption(at: AppTest) -> None:
    _mk_project()
    at.run()
    _goto(at, "策划", pid="proj_ui")

    assert not at.exception
    assert at.file_uploader(key="bgmup_proj_ui")
    assert any("assets/bgm.mp3" in c.value for c in at.caption)


def test_capability_caption_supports_reference_image(at: AppTest, monkeypatch) -> None:
    """渠道能力提示随 image_capabilities 自适应：支持注入时显示上限张数。"""
    monkeypatch.setattr(
        "app.core.providers.image_capabilities",
        lambda config: ImageCapabilities(supports_reference_image=True, max_reference_images=1),
    )
    _mk_project()
    at.run()
    _goto(at, "策划", pid="proj_ui")

    assert not at.exception
    assert any("支持参考图" in c.value and "上限 1 张" in c.value for c in at.caption)


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


# ---------------------------------------------------------------- 分镜编辑器改版（M6-7.8）

def _mk_candidate_project(pid: str = "proj_ui") -> ProjectStore:
    """带 3 张候选图资产（真实 PNG 文件，st.image 需 PIL 可解码）的项目。"""
    store = _store()
    store.init_repo()
    project = Project(project_id=pid, title="UI 测试")
    project.pipeline.update({"direct": "done", "confirm": "done", "gen_assets": "done"})
    pdir = store.project_dir(pid)
    (pdir / "assets").mkdir(parents=True)
    for i in range(1, 4):                       # 先落资产文件，再建资产字典
        (pdir / "assets" / f"img_s1_v{i}.png").write_bytes(_tiny_png())
    project.assets = {
        f"img_s1_v{i}": Asset(
            type="image", path=f"assets/img_s1_v{i}.png", seed=100 + i, status="done",
        )
        for i in range(1, 4)
    }
    project.scenes = [                          # 资产先于场景赋值（校验器逐次重跑）
        Scene(scene_id="s1", narration="你好。", visual="v", image_prompt="p",
              image_candidates=["img_s1_v1", "img_s1_v2", "img_s1_v3"],
              image_asset_id="img_s1_v1", status="done"),
    ]
    store.create(project)
    return store


def _has_button(at: AppTest, key: str) -> bool:
    """AppTest 按钮按 key 查询缺失时抛 KeyError（WidgetList 语义）→ 转布尔。"""
    try:
        at.button(key=key)
        return True
    except KeyError:
        return False


def test_storyboard_narration_editable_and_persists(at: AppTest) -> None:
    """文案可编辑（M6 守卫放开）：保存后落盘，direct 保持 done，confirm 起失效。"""
    _mk_project(scenes=SCENES, pipeline={"direct": "done", "confirm": "done"})
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    at.text_area(key="narration_proj_ui_s1").set_value("新文案。")
    at.run()                                                    # edited=True → 保存可用
    at.button(key="save_scenes_proj_ui").click()
    at.run()

    assert not at.exception
    loaded = _store().load("proj_ui")
    assert loaded.scenes[0].narration == "新文案。"
    assert loaded.pipeline["direct"] == "done"                  # 分镜人改保持 direct done
    assert loaded.pipeline["confirm"] == "pending"
    assert loaded.pipeline["gen_assets"] == "pending"


def test_storyboard_new_fields_render_and_save(at: AppTest) -> None:
    """M6 新字段：景别/规划时长/音效描述可编辑并落盘。"""
    _mk_project(scenes=SCENES, pipeline={"direct": "done"})
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    at.selectbox(key="shotsize_proj_ui_s1").set_value("特写")
    at.number_input(key="dur_proj_ui_s1").set_value(1500)
    at.text_input(key="sfx_proj_ui_s1").set_value("风声")
    at.run()
    at.button(key="save_scenes_proj_ui").click()
    at.run()

    assert not at.exception
    s1 = _store().load("proj_ui").scenes[0]
    assert s1.shot_size == "特写"
    assert s1.planned_duration_ms == 1500
    assert s1.sfx == "风声"


def test_storyboard_add_scene_appends(at: AppTest) -> None:
    _mk_project(scenes=SCENES)
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    at.button(key="addscene_proj_ui").click()
    at.run()

    assert not at.exception
    loaded = _store().load("proj_ui")
    assert [s.scene_id for s in loaded.scenes] == ["s1", "s2", "s3"]
    assert loaded.scenes[-1].narration == ""                    # 空文案允许（gen_assets 有守卫）


def test_storyboard_delete_scene(at: AppTest) -> None:
    _mk_project(scenes=SCENES)
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    at.button(key="delscene_proj_ui_s2").click()
    at.run()

    assert not at.exception
    assert [s.scene_id for s in _store().load("proj_ui").scenes] == ["s1"]


def test_storyboard_reorder_scenes(at: AppTest) -> None:
    _mk_project(scenes=SCENES)
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    at.button(key="moveup_proj_ui_s2").click()
    at.run()

    assert not at.exception
    assert [s.scene_id for s in _store().load("proj_ui").scenes] == ["s2", "s1"]


def test_candidate_gallery_and_select(at: AppTest) -> None:
    """候选画廊：3 张候选 + 默认选中 v1 打 ✓；改选 v2 只失效 timeline/export。"""
    _mk_candidate_project()
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    assert not at.exception
    for v in ("v2", "v3"):                                     # 未选中者带「选中」按钮
        assert at.button(key=f"pickimg_proj_ui_s1_img_s1_{v}")
    assert _has_button(at, "pickimg_proj_ui_s1_img_s1_v1") is False   # 选中者只有 ✓
    assert any("已选中 v1" in c.value for c in at.caption)

    at.button(key="pickimg_proj_ui_s1_img_s1_v2").click()
    at.run()

    assert not at.exception
    loaded = _store().load("proj_ui")
    assert loaded.scenes[0].image_asset_id == "img_s1_v2"
    assert loaded.pipeline["timeline"] == "pending"             # 改选 → 时间线起重跑
    assert loaded.pipeline["export"] == "pending"
    assert loaded.pipeline["confirm"] == "done"                 # confirm 不动
    assert loaded.pipeline["gen_assets"] == "done"              # 素材不重跑


def test_candidate_gallery_end_frame_toggle(at: AppTest) -> None:
    """M7 结束帧：候选画廊「设为结束帧」→ animate 起重跑；已设者显示「取消结束帧」。"""
    _mk_candidate_project()
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    for v in ("v1", "v2", "v3"):                                 # 初始全部可设结束帧
        assert at.button(key=f"setendf_proj_ui_s1_img_s1_{v}")
    assert _has_button(at, "clearendf_proj_ui_s1_img_s1_v2") is False

    at.button(key="setendf_proj_ui_s1_img_s1_v2").click()
    at.run()

    assert not at.exception
    loaded = _store().load("proj_ui")
    assert loaded.scenes[0].end_image_asset_id == "img_s1_v2"
    assert loaded.pipeline["animate"] == "pending"               # 计划重解析
    assert loaded.pipeline["timeline"] == "pending"
    assert loaded.pipeline["gen_assets"] == "done"               # 素材不重跑
    assert any("结束帧" in c.value for c in at.caption)

    at.button(key="clearendf_proj_ui_s1_img_s1_v2").click()
    at.run()

    assert not at.exception
    assert _store().load("proj_ui").scenes[0].end_image_asset_id is None


def test_reroll_dispatches_to_worker(at: AppTest, monkeypatch) -> None:
    """重新生成候选 → 后台任务分发 edits.reroll_scene_candidates(scene_id)。"""
    calls: list[str] = []

    def fake_reroll(store, project, scene_id, image, progress=None):
        calls.append(scene_id)
        project.pipeline["timeline"] = "pending"                # 模拟真实落盘失效
        store.save(project, message="web 测试 reroll")
        return project

    monkeypatch.setattr("app.core.edits.reroll_scene_candidates", fake_reroll)
    monkeypatch.setattr("app.core.providers.make_image", lambda config: object())
    _mk_candidate_project()
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    at.button(key="reroll_proj_ui_s1").click()
    at.run()
    _wait_task(at)
    at.run()                                                    # fragment 消费 + 刷新

    assert not at.exception
    assert calls == ["s1"]
    assert _ss(at, "task") is None
    assert any("reroll_scene 完成" in s.value for s in at.success)


def test_refine_button_visibility_follows_capability(at: AppTest, monkeypatch) -> None:
    """精修按钮随渠道能力显隐：默认 siliconflow（FLUX）不支持 → 隐藏；支持 → 显示。"""
    _mk_candidate_project()
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    assert not at.exception
    assert _has_button(at, "refine_proj_ui_s1") is False        # 默认渠道不支持 → 隐藏

    monkeypatch.setattr(
        "app.core.providers.image_capabilities",
        lambda config: ImageCapabilities(supports_reference_image=True, max_reference_images=1),
    )
    at.run()
    assert _has_button(at, "refine_proj_ui_s1") is True         # 支持 → 显示


def test_rewrite_dispatches_instructions(at: AppTest, monkeypatch) -> None:
    """AI 重写 → 后台任务分发 edits.rewrite_scene(scene_id, 指令)。"""
    calls: list[tuple] = []

    def fake_rewrite(store, project, director, scene_id, instructions, progress=None):
        calls.append((scene_id, instructions))
        store.save(project, message="web 测试 rewrite")
        return project

    monkeypatch.setattr("app.core.edits.rewrite_scene", fake_rewrite)
    monkeypatch.setattr("app.core.providers.make_director", lambda config: object())
    _mk_project(scenes=SCENES)
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    at.text_input(key="rewrite_instr_proj_ui_s1").set_value("夜晚")
    at.run()                                                    # 指令非空 → AI 重写可用
    at.button(key="rewrite_proj_ui_s1").click()
    at.run()
    _wait_task(at)
    at.run()

    assert not at.exception
    assert calls == [("s1", "夜晚")]
    assert _ss(at, "task") is None


# ---------------------------------------------------------------- 默认人审叙事（M6-7.9）

def test_run_all_moved_into_advanced_expander(at: AppTest, monkeypatch) -> None:
    """人审优先：一键全链路降级为「自动模式（高级）」expander，按钮仍触发全链路。"""
    calls = _mock_pipeline(monkeypatch)
    _mk_project(scenes=SCENES, pipeline={"direct": "done", "confirm": "done"})

    at.run()
    _goto(at, "流水线", pid="proj_ui")

    assert not at.exception
    assert any("自动模式" in e.label for e in at.expander)      # expander 存在
    at.button(key="run_all_proj_ui").click()                    # expander 内按钮仍可用
    at.run()
    _wait_task(at)
    at.run()

    assert not at.exception
    assert calls == ["gen_assets", "animate", "timeline", "export"]
    assert _ss(at, "task") is None


def test_storyboard_hub_hint_caption(at: AppTest) -> None:
    """分镜确认页流程提示：先确认分镜与选图，再生成素材。"""
    _mk_project(scenes=SCENES)
    at.run()
    _goto(at, "分镜确认", pid="proj_ui")

    assert not at.exception
    assert any("先确认分镜与逐镜选图" in c.value for c in at.caption)


# ---------------------------------------------------------------- 页面 5：成本面板

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
