"""AVPO Streamlit 工作台（M4）—— 项目管理 / 流水线 / 分镜确认 / 成本面板。

启动（推荐，缓存目录重定向到 data/webhome，不写用户目录）：
    avpo web --port 8501
手动启动（在仓库根执行；streamlit 缓存将写入用户目录）：
    streamlit run app/web/app.py

设计约定：
- 单文件 + 侧边栏 radio 导航（AppTest 一次只测一个入口脚本）；
- 所有 core 调用走模块引用（pipeline.run_direct / providers.make_image），
  AppTest 按 app.core.* 模块属性 monkeypatch 才能命中；
- 长任务同步阻塞 + st.status 阶段文字（M4 接受；progress 参数为线程化预留）；
- widget key 带项目 id 前缀，切换项目不残留旧项目脏值。
"""

import streamlit as st

from app.core import env, errors, pipeline, providers, styles
from app.core.cost import DEFAULT_BUDGET, summarize
from app.core.project import ProjectStore
from app.core.schema import PIPELINE_NODES, MotionKind, Project
from app.tts.tts_edge import EdgeTTS

STATUS_COLORS = {"pending": "orange", "running": "blue", "done": "green", "failed": "red"}


# ---------------------------------------------------------------- 通用

def _load_project(store: ProjectStore, pid: str) -> Project | None:
    """加载项目；不存在/损坏内联报错，不崩页面。"""
    try:
        return store.load(pid)
    except FileNotFoundError:
        st.error(f"项目不存在: {pid}")
    except Exception as exc:  # noqa: BLE001 —— pydantic 校验失败等，页面级容错
        st.error(f"项目 {pid} 加载失败（project.json 损坏？）: {exc}")
    return None


def _require_project(store: ProjectStore) -> Project | None:
    pid = st.session_state.get("pid")
    if not pid:
        st.info("请先在「项目管理」创建并选择项目")
        return None
    return _load_project(store, pid)


def _render_errors(project: Project) -> None:
    if project.errors:
        with st.expander(f"错误记录 ({len(project.errors)})", expanded=True):
            for e in project.errors[-5:]:
                st.error(errors.describe_list([e]))


# ---------------------------------------------------------------- 页面 1：项目管理

def _render_new_project_form(store: ProjectStore) -> None:
    with st.expander("新建项目", expanded=not store.list_project_ids()):
        c1, c2 = st.columns(2)
        c1.text_input("项目 ID", key="new_pid", placeholder="如 proj_001")
        c2.text_input("标题", key="new_title", placeholder="可选，默认同项目 ID")
        c3, c4 = st.columns(2)
        c3.selectbox("风格模板", styles.list_styles(), key="new_style")
        c4.selectbox("渠道", ["siliconflow", "dashscope"], key="new_provider")
        st.text_input("TTS 音色", value="zh-CN-YunxiNeural", key="new_voice")

        if st.button("创建项目", key="btn_create"):
            pid = (st.session_state.get("new_pid") or "").strip()
            if not pid:
                st.error("项目 ID 不能为空")
                return
            try:
                config = providers.config_for_provider(st.session_state.get("new_provider", "siliconflow"))
                styles.load_style(st.session_state.get("new_style", "default"))
            except ValueError as exc:
                st.error(str(exc))
                return
            project = Project(project_id=pid, title=st.session_state.get("new_title") or pid, config=config)
            project.config.style = st.session_state.get("new_style", "default")
            project.config.tts.voice = st.session_state.get("new_voice", "zh-CN-YunxiNeural")
            store.init_repo()
            try:
                store.create(project)
            except FileExistsError:
                st.error(f"项目已存在: {pid}")
            else:
                st.session_state.pid = pid
                st.success(f"项目已创建: {pid}")
                st.rerun()


def _render_projects(store: ProjectStore) -> None:
    st.header("项目管理")
    ids = store.list_project_ids()
    if not ids:
        st.info("暂无项目。用下方表单创建第一个项目。")

    for pid in ids:
        try:
            p = store.load(pid)
        except Exception as exc:  # noqa: BLE001 —— 单个坏项目不中断列表
            st.error(f"项目 {pid} 加载失败（project.json 损坏？）: {exc}")
            continue
        done_n = sum(1 for v in p.pipeline.values() if v == "done")
        with st.container(border=True):
            cols = st.columns([3, 1, 1, 1, 1])
            cols[0].markdown(f"**{p.title or pid}**  `{pid}`")
            cols[1].caption(f"风格: {p.config.style}")
            cols[2].caption(f"渠道: {p.config.image.provider}")
            cols[3].caption(f"成本 ¥{summarize(p).total:.3f}")
            if cols[4].button("选择", key=f"pick_{pid}"):
                st.session_state.pid = pid
                st.rerun()
            st.progress(done_n / len(PIPELINE_NODES), text=f"进度 {done_n}/{len(PIPELINE_NODES)} done")

    _render_new_project_form(store)


# ---------------------------------------------------------------- 页面 2：流水线

def _run_node(store: ProjectStore, project: Project, node: str, text: str = "") -> bool:
    """跑单节点：成功 st.rerun 刷新徽章，失败展示错误不 rerun。"""
    with st.status(f"运行 {node}…", expanded=True) as status:
        try:
            if node == "direct":
                try:
                    director = providers.make_director(project.config.llm)
                except KeyError as exc:
                    st.error(str(exc))
                    return False
                ok = pipeline.run_direct(
                    store, project, director, text, progress=lambda m: status.update(label=m)
                )
            elif node == "confirm":
                ok = pipeline.run_confirm(store, project)
            elif node == "gen_assets":
                try:
                    image = providers.make_image(project.config.image)
                except KeyError as exc:
                    st.error(str(exc))
                    return False
                ok = pipeline.run_gen_assets(
                    store, project, EdgeTTS(project.config.tts), image,
                    progress=lambda m: status.update(label=m),
                )
            elif node == "timeline":
                ok = pipeline.run_timeline(store, project)
            else:  # export
                ok = pipeline.run_export(store, project)
        except Exception as exc:  # noqa: BLE001 —— 节点异常不崩页面
            st.error(f"{node} 异常: {exc}")
            return False
    if ok:
        st.success(f"{node} 完成")
        st.rerun()
    st.error(f"{node} 失败: {errors.describe_list(project.errors)}")
    return False


def _run_full_chain(store: ProjectStore, project: Project, text: str) -> None:
    """一键全链路，镜像 CLI run 语义：done 跳过；confirm 未确认则提示去分镜页。"""
    with st.status("一键全链路 direct→confirm→gen_assets→timeline→export", expanded=True) as status:
        if project.pipeline["direct"] != "done":
            if not text.strip():
                st.error("direct 未完成：请先填写口播文案，或单独运行 direct")
                return
            try:
                director = providers.make_director(project.config.llm)
            except KeyError as exc:
                st.error(str(exc))
                return
            if not pipeline.run_direct(store, project, director, text, progress=lambda m: status.update(label=m)):
                st.error(errors.describe_list(project.errors))
                return
        if project.pipeline["confirm"] != "done":
            st.warning("分镜待确认：请到「分镜确认」页确认后，再点击一键全链路")
            return
        if project.pipeline["gen_assets"] != "done":
            try:
                image = providers.make_image(project.config.image)
            except KeyError as exc:
                st.error(str(exc))
                return
            if not pipeline.run_gen_assets(
                store, project, EdgeTTS(project.config.tts), image,
                progress=lambda m: status.update(label=m),
            ):
                st.error(errors.describe_list(project.errors))
                return
        if not pipeline.run_timeline(store, project):
            st.error(errors.describe_list(project.errors))
            return
        if not pipeline.run_export(store, project):
            st.error(errors.describe_list(project.errors))
            return
    st.success("全链路完成，草稿已导出")
    st.rerun()


def _render_export_output(store: ProjectStore, project: Project) -> None:
    if project.export.status == "done" and project.export.path:
        st.success(f"剪映草稿已导出: {project.export.path}")
    project_dir = store.project_dir(project.project_id)
    for zp in sorted((project_dir / "exports").glob("*.zip")) if (project_dir / "exports").is_dir() else []:
        with open(zp, "rb") as f:
            st.download_button(f"下载 {zp.name}", f.read(), file_name=zp.name, key=f"dl_{zp.name}")


def _render_pipeline(store: ProjectStore, project: Project) -> None:
    st.header(f"流水线 「{project.title or project.project_id}」")
    pid = project.project_id
    cols = st.columns(len(PIPELINE_NODES))
    for col, node in zip(cols, PIPELINE_NODES):
        status = project.pipeline[node]
        color = STATUS_COLORS.get(status, "gray")
        col.markdown(f":{color}[**{node}**]")
        col.caption(status)

    text = ""
    if project.pipeline["direct"] != "done":
        text = st.text_area(
            "口播文案", key=f"direct_text_{pid}", height=120,
            placeholder="粘贴口播文案全文（direct 未完成时必需）",
        )

    st.divider()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    if c1.button("运行 direct", key=f"run_direct_{pid}"):
        _run_node(store, project, "direct", text=text)
    if c2.button("运行 gen_assets", key=f"run_gen_{pid}"):
        _run_node(store, project, "gen_assets")
    if c3.button("运行 timeline", key=f"run_tl_{pid}"):
        _run_node(store, project, "timeline")
    if c4.button("运行 export", key=f"run_exp_{pid}"):
        _run_node(store, project, "export")
    c5.caption("confirm 在「分镜确认」页")
    if c6.button("一键全链路", type="primary", key=f"run_all_{pid}"):
        _run_full_chain(store, project, text)

    _render_errors(project)
    _render_export_output(store, project)


# ---------------------------------------------------------------- 页面 3：分镜确认

def _render_storyboard(store: ProjectStore, project: Project) -> None:
    st.header("分镜确认")
    pid = project.project_id
    if project.pipeline["confirm"] == "done":
        st.success("分镜已确认。若修改分镜，将重置为待确认，需重新确认。")
    if not project.scenes:
        st.info("暂无分镜：请到「流水线」页运行 direct 生成分镜")
        return

    motions = list(MotionKind.__args__)
    new_scenes = []
    edited = False
    for i, s in enumerate(project.scenes):
        title = s.narration[:24] + ("…" if len(s.narration) > 24 else "")
        with st.expander(f"{s.scene_id} · {title}", expanded=(i == 0)):
            st.markdown(f"**文案**（不可编辑）：{s.narration}")
            visual = st.text_area("画面描述", value=s.visual, key=f"visual_{pid}_{s.scene_id}")
            prompt = st.text_area("生图提示词（英文）", value=s.image_prompt, key=f"imgprompt_{pid}_{s.scene_id}")
            motion = st.selectbox("运镜", motions, index=motions.index(s.motion), key=f"motion_{pid}_{s.scene_id}")
            st.caption(f"status={s.status}  start={s.start_ms}ms  cost={s.cost}")
            if s.image_asset_id and (asset := project.assets.get(s.image_asset_id)):
                img_path = store.project_dir(pid) / asset.path
                if img_path.is_file():
                    st.image(str(img_path), width=320)
            if (visual, prompt, motion) != (s.visual, s.image_prompt, s.motion):
                edited = True
            new_scenes.append(
                s.model_copy(update={"visual": visual, "image_prompt": prompt, "motion": motion})
            )

    if st.button("保存全部修改", key=f"save_scenes_{pid}", disabled=not edited):
        try:
            pipeline.update_scenes(store, project, new_scenes)
        except ValueError as exc:
            st.error(str(exc))
        else:
            for k in list(st.session_state):          # 清本项目编辑 key，防脏值残留
                if k.startswith(("visual_", "imgprompt_", "motion_")):
                    st.session_state.pop(k, None)
            st.success("修改已保存，下游节点已重置为待运行")
            st.rerun()

    if project.pipeline["confirm"] != "done":
        if st.button("确认分镜（进入素材生成）", type="primary", key=f"confirm_{pid}"):
            if pipeline.run_confirm(store, project):
                st.success("已确认")
                st.rerun()
            else:
                st.error(f"确认失败: {errors.describe_list(project.errors)}")


# ---------------------------------------------------------------- 页面 4：成本面板

def _render_cost(project: Project) -> None:
    st.header("成本面板")
    budget = st.number_input("预算（元）", min_value=0.1, value=DEFAULT_BUDGET, step=0.5, key="budget")
    s = summarize(project, budget)

    c1, c2 = st.columns(2)
    c1.metric("总成本（元）", f"{s.total:.4f}")
    c2.metric(
        "预算（元）", f"{s.budget:.2f}",
        delta=f"{s.budget - s.total:.2f} 结余" if not s.over_budget else f"{s.total - s.budget:.2f} 超支",
    )

    if s.over_budget:
        st.error(f"超出预算 {s.total - s.budget:.2f} 元：检查分镜数量/生图次数，或调高预算")
    elif s.total == 0:
        st.caption("尚无 API 成本（先运行 direct / gen_assets）")
    else:
        st.success(f"预算内（剩余 {s.budget - s.total:.2f} 元）")

    if s.by_scene:
        st.dataframe([{"场景": sid, **costs} for sid, costs in s.by_scene.items()])
    if s.by_asset:
        st.dataframe([{"资产": aid, "成本": c} for aid, c in s.by_asset.items()])


# ---------------------------------------------------------------- 入口

def main() -> None:
    st.set_page_config(page_title="AVPO 工作台", layout="wide")
    env.load_project_env()
    store = ProjectStore(env.resolve_data_dir())

    with st.sidebar:
        st.title("AVPO 工作台")
        section = st.radio("功能", ["项目管理", "流水线", "分镜确认", "成本面板"], key="section")
        ids = store.list_project_ids()
        if ids:
            if st.session_state.get("pid") not in ids:   # 会话 pid 已被删除 → 守卫
                st.session_state.pop("pid", None)
            st.selectbox("项目", ids, key="pid",
                         index=ids.index(st.session_state["pid"]) if st.session_state.get("pid") in ids else 0)
        else:
            st.info("暂无项目，请先在「项目管理」创建")

    if section == "项目管理":
        _render_projects(store)
        return
    project = _require_project(store)
    if project is None:
        return
    if section == "流水线":
        _render_pipeline(store, project)
    elif section == "分镜确认":
        _render_storyboard(store, project)
    else:
        _render_cost(project)


main()
