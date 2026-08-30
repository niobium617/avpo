"""AVPO Streamlit 工作台 —— 项目管理 / 策划 / 分镜确认 / 流水线 / 成本面板。

启动（推荐，缓存目录重定向到 data/webhome，不写用户目录）：
    avpo web --port 8501
手动启动（在仓库根执行；streamlit 缓存将写入用户目录）：
    streamlit run app/web/app.py

设计约定：
- 单文件 + 侧边栏 radio 导航（AppTest 一次只测一个入口脚本）；M6-7.7 起五页
  按创作流排序：项目管理 → 策划 → 分镜确认 → 流水线 → 成本面板；
- 所有 core 调用走模块引用（pipeline.run_direct / providers.make_image），
  AppTest 按 app.core.* 模块属性 monkeypatch 才能命中；
- M5 后台任务模型：节点在 worker 线程执行（主脚本立即返回），进度经
  app/web/tasks.py 的 TaskContainer（Lock 保护）传递；UI 用
  @st.fragment(run_every=1.0) 轮询渲染 st.progress，完成自动 st.rerun 刷新徽章；
  worker 线程绝不调 st.* / 绝不碰 st.session_state；
- M6-7.6 编辑语义：人类触发的单次编辑（策划保存/参考图/分镜修改）全部走
  app/core/edits.py 同步落盘 + 按需失效下游（不走后台任务）；上传临时文件
  写项目目录、处理完即删（data/ 本地 git 不上推，历史噪声可接受）；
- widget key 带项目 id 前缀，切换项目不残留旧项目脏值。
"""

import streamlit as st
from pathlib import Path

from app.audio import beats
from app.audio.whisper import WHISPER_MODELS
from app.core import edits, env, errors, pipeline, providers, styles
from app.web import tasks
from app.core.cost import DEFAULT_BUDGET, summarize
from app.core.project import ProjectStore
from app.core.schema import PIPELINE_NODES, Brief, MotionKind, Project, Scene, ShotSize, TransitionKind
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
                st.session_state["pending_pid"] = pid   # 选中项经 sidebar 预设（pid 是 widget key，不可直写）
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
                st.session_state["pending_pid"] = pid   # 选中项经 sidebar 预设（pid 是 widget key，不可直写）
                st.rerun()
            st.progress(done_n / len(PIPELINE_NODES), text=f"进度 {done_n}/{len(PIPELINE_NODES)} done")

    _render_new_project_form(store)


# ---------------------------------------------------------------- 页面 2：策划（M6-7.7）

# Brief 表单字段（顺序 = 创作流）：8 基础 + 3 风格关键词包 + bgm_hint。
# 长文本用 text_area，短值用 text_input（AppTest 按 key 断言，类型随实现走）。
BRIEF_FIELDS: list[tuple[str, str]] = [
    ("theme", "主题"),
    ("worldview", "世界观"),
    ("art_style", "整体画风"),
    ("duration", "时长（如 60s）"),
    ("platform", "发布平台"),
    ("protagonist", "主角形象"),
    ("plot", "核心剧情"),
    ("emotion", "情绪基调"),
]
BRIEF_KEYWORDS: list[tuple[str, str]] = [
    ("palette", "主色调"),
    ("lighting", "光影指令"),
    ("character", "人物核心特征"),
]
BRIEF_LONG = {"theme", "worldview", "protagonist", "plot", "character", "bgm_hint"}


def _brief_widget(field: str, label: str, value: str, pid: str):
    """长文本字段用 text_area，短值用 text_input（key 统一 brief_<field>_<pid>）。"""
    key = f"brief_{field}_{pid}"
    if field in BRIEF_LONG:
        return st.text_area(label, value=value, key=key)
    return st.text_input(label, value=value, key=key)


def _bgm_beat_count(store: ProjectStore, pid: str) -> int | None:
    """已上传 BGM 的节拍数（按文件 mtime 缓存于 session —— 每次页面重渲染不重复解码）。

    返回 None = 解码失败（调用方展示降级文案）。
    """
    bgm_path = store.project_dir(pid) / "assets" / "bgm.mp3"
    if not bgm_path.is_file():
        return None
    mtime = bgm_path.stat().st_mtime_ns
    key = f"bgm_beats_{pid}"
    cached = st.session_state.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        n: int | None = len(beats.detect_beats(bgm_path))
    except ValueError:
        n = None
    st.session_state[key] = (mtime, n)
    return n


def _render_brief(store: ProjectStore, project: Project) -> None:
    """页面 2：策划 —— 创作简报（Brief）+ 参考图组 + BGM 卡点/音效库（M6-7.7 + M8）。

    阶段一定位：brief 是创作者的「不变量」输入，AI 按简报提案分镜与画面；
    保存后 direct 起全下游重置待运行（update_brief 失效语义）。
    参考图/BGM/音效上传为同步编辑：落盘 + 按需失效下游，不走后台任务。
    渠道能力提示（image_capabilities）让 UI 按渠道自适应，不实例化渠道。
    """
    st.header("策划")
    pid = project.project_id
    busy = st.session_state.get("task") is not None
    brief = project.brief or Brief()
    if project.brief is None:
        st.info("尚未策划：填写创作简报保存后，direct 将按简报生成分镜提案（AI 是协作者，简报是你的创作输入）。")

    new_brief: dict[str, str] = {}
    edited = False
    st.subheader("创作简报")
    cols = st.columns(2)
    for i, (field, label) in enumerate(BRIEF_FIELDS):
        with cols[i % 2]:
            value = _brief_widget(field, label, getattr(brief, field), pid)
        new_brief[field] = value
        if value != getattr(brief, field):
            edited = True

    st.caption("风格关键词包（生图注入）：光影指令优先于风格模板默认光影（compose_image_prompt）")
    kcols = st.columns(3)
    for i, (field, label) in enumerate(BRIEF_KEYWORDS):
        with kcols[i]:
            value = _brief_widget(field, label, getattr(brief, field), pid)
        new_brief[field] = value
        if value != getattr(brief, field):
            edited = True

    bgm_hint = st.text_area(
        "BGM 节奏/卡点描述", value=brief.bgm_hint, key=f"brief_bgm_hint_{pid}",
        help="重拍/高潮/骤停 —— 分镜 planned_duration_ms 对齐节奏；完整卡点剪辑 M8",
    )
    new_brief["bgm_hint"] = bgm_hint
    if bgm_hint != brief.bgm_hint:
        edited = True

    st.caption("保存后 direct 及下游节点将重置待运行（分镜按新简报重新生成）。")
    if st.button("保存策划", key=f"save_brief_{pid}", disabled=not edited or busy):
        try:
            edits.update_brief(store, project, Brief(**new_brief))
        except ValueError as exc:
            st.error(str(exc))
        else:
            for k in list(st.session_state):
                if k.startswith("brief_"):
                    st.session_state.pop(k, None)
            st.success("简报已保存，direct 及下游节点已重置为待运行")
            st.rerun()

    st.divider()
    st.subheader("参考图组（多角度）")
    caps = providers.image_capabilities(project.config.image)
    if caps.supports_reference_image:
        st.caption(
            f"当前渠道支持参考图注入（上限 {caps.max_reference_images} 张）："
            f"已上传参考图将作为固定输入注入生图。"
        )
    else:
        st.caption("当前渠道不支持参考图注入：将仅用风格关键词包保持一致（可切换渠道/模型解锁）。")

    for ref in project.reference_images:
        with st.container(border=True):
            rc1, rc2 = st.columns([1, 5])
            img_path = store.project_dir(pid) / ref.path
            if img_path.is_file():
                rc1.image(str(img_path), width=120)
            rc2.markdown(f"**{ref.id}**  angle={ref.angle or '-'}  role={ref.role or '-'}")
            if rc2.button("删除", key=f"delref_{pid}_{ref.id}", disabled=busy):
                edits.remove_reference_image(store, project, ref.id)
                st.rerun()

    up = st.file_uploader(
        "上传参考图（png/jpg）", type=["png", "jpg", "jpeg"],
        key=f"refup_{pid}", disabled=busy,
    )
    angle = st.text_input("拍摄角度标签", value="", key=f"ref_angle_{pid}", placeholder="如 正面 / 侧面 / 45°仰视")
    role = st.text_input("用途/角色标签", value="", key=f"ref_role_{pid}", placeholder="如 主角 / 场景 / 道具")
    if up is not None:
        # 上传临时文件写项目目录，处理完即删（data/ 本地 git 不上推）
        tmp = store.project_dir(pid) / (".tmp_ref_upload" + (Path(up.name).suffix or ".png"))
        tmp.write_bytes(up.getbuffer())
        try:
            edits.add_reference_image(store, project, tmp, angle=angle, role=role)
        except ValueError as exc:
            st.error(str(exc))
        else:
            for k in ("refup", "ref_angle", "ref_role"):
                st.session_state.pop(f"{k}_{pid}", None)
            st.success("参考图已添加，gen_assets 起重置待运行")
            st.rerun()
        finally:
            tmp.unlink(missing_ok=True)

    st.divider()
    st.subheader("BGM 与卡点（M8 音频）")
    st.caption("BGM 拷贝到 assets/bgm.mp3：导出时铺满全片；开启卡点对齐后，时间线组装把场景切换点对齐到节拍。")
    if (store.project_dir(pid) / "assets" / "bgm.mp3").is_file():
        st.caption("已上传 BGM")
        n_beats = _bgm_beat_count(store, pid)
        if n_beats is None:
            st.caption("BGM 无法解析节拍（可能不是有效音频，卡点将跳过）")
        else:
            st.caption(f"检测到 {n_beats} 个节拍（卡点开启时切换点 snap 到最近节拍）")
    bgm_up = st.file_uploader(
        "上传 BGM（mp3）", type=["mp3"], key=f"bgmup_{pid}", disabled=busy,
    )
    if bgm_up is not None:
        tmp = store.project_dir(pid) / ".tmp_bgm_upload.mp3"
        tmp.write_bytes(bgm_up.getbuffer())
        try:
            edits.add_bgm(store, project, tmp)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(f"bgmup_{pid}", None)
            st.success("BGM 已上传，timeline 起重置待运行")
            st.rerun()
        finally:
            tmp.unlink(missing_ok=True)

    beat_sync = st.checkbox(
        "BGM 卡点对齐（场景切换点对齐节拍）", value=project.config.beat_sync,
        key=f"beatsync_{pid}", disabled=busy,
        help="需要先上传 BGM；切换点最多向前顺延 0.4s 对齐最近节拍（配音不重叠，停顿处只有 BGM）",
    )
    if beat_sync != project.config.beat_sync:
        try:
            edits.set_beat_sync(store, project, beat_sync)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.rerun()

    st.divider()
    st.subheader("本地字幕（M10 Whisper）")
    st.caption(
        "分镜页上传「自带音频」的场景由本地 whisper 转写字幕（替代 TTS 配音与文案字幕）。"
        "模型首次使用需联网下载（缓存于数据目录 whisper_models/，可用 AVPO_WHISPER_CACHE/HF_ENDPOINT 配置）。"
    )
    w1, w2 = st.columns(2)
    wmodel = w1.selectbox(
        "whisper 模型", list(WHISPER_MODELS),
        index=list(WHISPER_MODELS).index(project.config.whisper_model),
        key=f"wmodel_{pid}", disabled=busy,
        help="越大越准越慢；small 为中文口播平衡点",
    )
    wlang = w2.text_input(
        "转写语言", value=project.config.whisper_language, key=f"wlang_{pid}", disabled=busy,
        help="如 zh/ja/en；留空 = whisper 自动检测",
    )
    if wmodel != project.config.whisper_model or wlang != project.config.whisper_language:
        try:
            edits.set_whisper_config(store, project, wmodel, wlang)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.rerun()

    st.divider()
    st.subheader("音效库（场景切点音效）")
    st.caption("上传 mp3/wav 音效入库（assets/sfx/），到「分镜确认」页给场景绑定切点音效（M8）。")
    sfx_assets = sorted(
        aid for aid, a in project.assets.items() if aid.startswith("sfx_") and a.type == "audio"
    )
    if not sfx_assets:
        st.caption("（暂无音效素材）")
    for aid in sfx_assets:
        s1, s2 = st.columns([6, 1])
        s1.caption(f"{aid}  ·  {project.assets[aid].path}")
        if s2.button("删除", key=f"delsfx_{pid}_{aid}", disabled=busy):
            edits.remove_sfx(store, project, aid)
            st.rerun()
    sfx_up = st.file_uploader(
        "上传音效（mp3/wav）", type=["mp3", "wav"], key=f"sfxup_{pid}", disabled=busy,
    )
    if sfx_up is not None:
        tmp = store.project_dir(pid) / (".tmp_sfx_upload" + Path(sfx_up.name).suffix)
        tmp.write_bytes(sfx_up.getbuffer())
        try:
            edits.add_sfx(store, project, tmp)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(f"sfxup_{pid}", None)
            st.success("音效已入库，timeline 起重置待运行")
            st.rerun()
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- 页面 4：流水线

def _render_export_output(store: ProjectStore, project: Project) -> None:
    if project.export.status == "done" and project.export.path:
        st.success(f"剪映草稿已导出: {project.export.path}")
    project_dir = store.project_dir(project.project_id)
    for zp in sorted((project_dir / "exports").glob("*.zip")) if (project_dir / "exports").is_dir() else []:
        with open(zp, "rb") as f:
            st.download_button(f"下载 {zp.name}", f.read(), file_name=zp.name, key=f"dl_{zp.name}")


# ---------------------------------------------------------------- 后台任务（M5）

def _start_node(store: ProjectStore, project: Project, node: str, text: str = "",
                scene_id: str | None = None) -> None:
    """启动单节点后台任务：主脚本立即返回，worker 线程执行 pipeline。

    scene_id（M6-7.8）：ad-hoc 单场景操作（reroll/refine/rewrite）的目标场景。
    """
    if st.session_state.get("task") is not None:      # 双开兜底（按钮禁用为主防线）
        st.warning("已有后台任务运行中，请等待完成")
        return
    container = tasks.start_task(store, project, node, text, scene_id=scene_id)
    st.session_state["task"] = container
    st.session_state.pop("task_result", None)


def _start_chain(store: ProjectStore, project: Project, text: str) -> None:
    """一键全链路：confirm 未确认在 UI 前置拦截（与现行为一致），其余交给 worker。"""
    if project.pipeline["confirm"] != "done":
        st.warning("分镜待确认：请到「分镜确认」页确认后，再点击一键全链路")
        return
    if st.session_state.get("task") is not None:
        st.warning("已有后台任务运行中，请等待完成")
        return
    container = tasks.start_task(store, project, "", chain=True)
    st.session_state["task"] = container
    st.session_state.pop("task_result", None)


@st.fragment(run_every=1.0)
def _render_task_progress() -> None:
    """后台任务进度轮询 UI（M5）。

    语义（已核实 1.62）：fragment 函数体在全页 run 时内联执行，AppTest 每次
    at.run() 都会执行；run_every 的自动重跑由前端 timer 驱动，真实运行 ~1s
    轮询一次，测试里用 at.run() 手动驱动。
    完成分支：先删任务再 st.rerun()（默认 scope="app"，fragment 内合法）——
    防止下一轮全页 run 再次触发 rerun 死循环。
    """
    task = st.session_state.get("task")
    if task is None:
        return
    state, events, error = task.snapshot()
    last = events[-1] if events else None
    st.caption(f"后台任务：{task.node}")
    if state == "running":
        if last is not None and last.percent is not None:
            st.progress(min(last.percent, 1.0), text=last.message)
        else:
            st.progress(0.0, text=last.message if last else "启动中…")
        return                                        # 运行中：不消费、不 rerun
    st.progress(1.0 if state == "done" else 0.0, text=last.message if last else "")
    if state == "done":
        st.success(f"{task.node} 完成")
        result = (task.node, "done", "")
    elif state == "aborted":
        st.warning(error)
        result = (task.node, "aborted", error)
    else:
        st.error(f"{task.node} 失败: {error}")
        result = (task.node, "failed", error)
    del st.session_state["task"]                      # 只消费一次
    st.session_state["task_result"] = result          # 持久结果（主脚本区渲染）
    st.rerun()                                        # scope="app"：刷新徽章/按钮/错误区


def _render_task_result() -> None:
    """上次后台任务的持久结果区（替代原 st.success/st.error 瞬态分支）。"""
    result = st.session_state.get("task_result")
    if not result:
        return
    node, state, error = result
    if state == "done":
        st.success(f"{node} 完成")
    elif state == "aborted":
        st.warning(error)
    else:
        st.error(f"{node} 失败: {error}")


def _render_pipeline(store: ProjectStore, project: Project) -> None:
    st.header(f"流水线 「{project.title or project.project_id}」")
    pid = project.project_id
    busy = st.session_state.get("task") is not None
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
    if c1.button("运行 direct", key=f"run_direct_{pid}", disabled=busy):
        _start_node(store, project, "direct", text=text)
    if c2.button("运行 gen_assets", key=f"run_gen_{pid}", disabled=busy):
        _start_node(store, project, "gen_assets")
    if c3.button("运行 transcribe", key=f"run_tsc_{pid}", disabled=busy):
        _start_node(store, project, "transcribe")
    if c4.button("运行 timeline", key=f"run_tl_{pid}", disabled=busy):
        _start_node(store, project, "timeline")
    if c5.button("运行 export", key=f"run_exp_{pid}", disabled=busy):
        _start_node(store, project, "export")
    c6.caption("confirm 在「分镜确认」页")
    with st.expander("自动模式（高级）"):          # M6-7.9 人审优先：全链路降级为高级选项
        st.caption(
            "一键全链路跳过人工确认与逐镜选图（候选默认用 v1）。"
            "M6 默认人审：建议走 策划 → direct → 分镜确认（选图/精修）→ gen_assets 流程；"
            "自动模式仅用于自动化或重跑场景。"
        )
        if st.button("一键全链路", type="primary", key=f"run_all_{pid}", disabled=busy):
            _start_chain(store, project, text)

    _render_errors(project)
    _render_export_output(store, project)


# ---------------------------------------------------------------- 页面 3：分镜确认

SHOT_SIZES = list(ShotSize.__args__)                   # 含 ""（未指定）
# M9 止损转场中文标签（分镜页 selectbox format_func）
TRANSITION_LABELS = {
    "auto": "自动（无结束帧→闪白）",
    "none": "无",
    "flash_white": "闪白 0.3s",
    "shake": "震动 0.3s",
}


def _next_scene_id(project: Project) -> str:
    """下一个可用 scene_id：s<最大数字后缀+1>（删除后复用空闲 id，可接受）。"""
    nums = []
    for s in project.scenes:
        if s.scene_id.startswith("s") and s.scene_id[1:].isdigit():
            nums.append(int(s.scene_id[1:]))
    return f"s{max(nums) + 1}" if nums else "s1"


def _clear_scene_edit_keys(pid: str) -> None:
    """清本项目分镜编辑 widget key（保存/增删/排序后调用，防脏值残留）。"""
    for k in list(st.session_state):
        if k.startswith(("narration_", "visual_", "imgprompt_", "motion_",
                         "shotsize_", "dur_", "sfx_", "sfxsel_", "trans_")):
            st.session_state.pop(k, None)


def _render_candidate_gallery(store: ProjectStore, project: Project, scene, pid: str,
                              busy: bool) -> None:
    """候选画廊（M6-7.5/7.8 + M7-8.8）：每镜 N 列缩略图，人审「选中」改选 + 「结束帧」；
    reroll/refine 后台任务。

    选中候选打 ✓（首帧，时间线用）；结束帧（首尾帧）独立选择 —— animate 节点渲染为
    配音后的 0.4s 静态尾拍（硬切；M9 起无结束帧的切点自动闪白止损，转场选择见上方
    selectbox），走 edits.select_end_frame/clear_end_frame
    （animate 起重跑）。refine（图生图精修）仅渠道支持参考图注入时渲染（能力标志内省）。
    """
    caps = providers.image_capabilities(project.config.image)
    project_dir = store.project_dir(pid)
    candidates = scene.image_candidates
    if candidates:
        cols = st.columns(len(candidates))
        for col, cid in zip(cols, candidates):
            asset = project.assets.get(cid)
            with col:
                if asset and (project_dir / asset.path).is_file():
                    st.image(str(project_dir / asset.path))
                if asset:
                    st.caption(f"v{cid.rsplit('_v', 1)[-1]} · seed={asset.seed}")
                if scene.image_asset_id == cid:
                    st.caption(f"✓ 已选中 v{cid.rsplit('_v', 1)[-1]}")
                elif st.button("选中", key=f"pickimg_{pid}_{scene.scene_id}_{cid}", disabled=busy):
                    edits.select_image_candidate(store, project, scene.scene_id, cid)
                    st.rerun()
                if scene.end_image_asset_id == cid:
                    st.caption("◼ 结束帧")
                    if st.button("取消结束帧", key=f"clearendf_{pid}_{scene.scene_id}_{cid}",
                                 disabled=busy):
                        edits.clear_end_frame(store, project, scene.scene_id)
                        st.rerun()
                elif st.button("设为结束帧", key=f"setendf_{pid}_{scene.scene_id}_{cid}",
                               disabled=busy):
                    edits.select_end_frame(store, project, scene.scene_id, cid)
                    st.rerun()
        st.caption("结束帧 = 镜头结束画面：渲染为配音后的静态尾拍（约 0.4s，硬切；未设结束帧的切点 M9 自动闪白止损）")
        r1, r2 = st.columns(2)
        if r1.button("重新生成候选（换种子）", key=f"reroll_{pid}_{scene.scene_id}", disabled=busy):
            _start_node(store, project, "reroll_scene", scene_id=scene.scene_id)
        if caps.supports_reference_image:
            if r2.button("精修（图生图）", key=f"refine_{pid}_{scene.scene_id}", disabled=busy):
                _start_node(store, project, "refine_scene", scene_id=scene.scene_id)
    elif scene.image_asset_id and (asset := project.assets.get(scene.image_asset_id)):
        # M6 前旧式单图（无候选清单）：保持单图展示
        img_path = project_dir / asset.path
        if img_path.is_file():
            st.image(str(img_path), width=320)


def _render_storyboard(store: ProjectStore, project: Project) -> None:
    st.header("分镜确认")
    st.caption("创作者枢纽（M6 默认人审）：先确认分镜与逐镜选图，再到「流水线」页生成素材。")
    pid = project.project_id
    busy = st.session_state.get("task") is not None      # 运行中禁改分镜：防 worker 副本覆盖
    if project.pipeline["confirm"] == "done":
        st.success("分镜已确认。若修改分镜，将重置为待确认，需重新确认。")
    if not project.scenes:
        st.info("暂无分镜：请到「流水线」页运行 direct 生成分镜，或「添加分镜」从零起草")

    motions = list(MotionKind.__args__)
    new_scenes = []
    edited = False
    for i, s in enumerate(project.scenes):
        title = s.narration[:24] + ("…" if len(s.narration) > 24 else "")
        with st.expander(f"{s.scene_id} · {title}", expanded=(i == 0)):
            narration = st.text_area(
                "文案", value=s.narration, key=f"narration_{pid}_{s.scene_id}",
                help="文案改动后该场景配音/字幕自动重新合成（TTS 缓存按文案哈希失效）",
            )
            visual = st.text_area("画面描述", value=s.visual, key=f"visual_{pid}_{s.scene_id}")
            prompt = st.text_area("生图提示词（英文）", value=s.image_prompt, key=f"imgprompt_{pid}_{s.scene_id}")
            m1, m2, m3 = st.columns(3)
            motion = m1.selectbox(
                "运镜", motions, index=motions.index(s.motion), key=f"motion_{pid}_{s.scene_id}",
            )
            shot = m2.selectbox(
                "景别", SHOT_SIZES, index=SHOT_SIZES.index(s.shot_size),
                key=f"shotsize_{pid}_{s.scene_id}", format_func=lambda v: v or "未指定",
            )
            dur = m3.number_input(
                "规划时长(ms)", min_value=0, step=100, value=s.planned_duration_ms or 0,
                key=f"dur_{pid}_{s.scene_id}", help="对齐 BGM 节奏（0 = 未指定）",
            )
            sfx = st.text_input(
                "音效描述", value=s.sfx, key=f"sfx_{pid}_{s.scene_id}",
                help="文本标注（导演提案）；实际切点音效在下方选择（素材来自策划页音效库）",
            )
            sfx_ids = sorted(
                aid for aid, a in project.assets.items() if aid.startswith("sfx_") and a.type == "audio"
            )
            sfx_options: list[str | None] = [None, *sfx_ids]
            sfx_sel = st.selectbox(
                "切点音效（场景起点）", sfx_options,
                index=sfx_options.index(s.sfx_asset_id) if s.sfx_asset_id in sfx_ids else 0,
                format_func=lambda v: "无" if v is None else v,
                key=f"sfxsel_{pid}_{s.scene_id}", disabled=busy,
                help="M8：音效在场景切换点播放（时间线组装 + 导出音效轨）",
            )
            if sfx_sel != s.sfx_asset_id:
                try:
                    edits.select_scene_sfx(store, project, s.scene_id, sfx_sel)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            transitions = list(TransitionKind.__args__)
            trans_sel = st.selectbox(
                "转场（进入下一镜的切点）", transitions,
                index=transitions.index(s.transition),
                format_func=lambda v: TRANSITION_LABELS[v],
                key=f"trans_{pid}_{s.scene_id}", disabled=busy,
                help="M9 止损：auto = 无结束帧的切点自动闪白 0.3s 覆盖不可修帧（有结束帧保持硬切）；显式选择逐镜覆盖",
            )
            if trans_sel != s.transition:
                try:
                    edits.set_scene_transition(store, project, s.scene_id, trans_sel)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            # M10 自带音频：创作者录音替代 TTS 配音，经本地 Whisper 转写字幕
            if s.user_audio_asset_id:
                audio_asset = project.assets.get(s.user_audio_asset_id)
                st.caption(f"自带音频: {audio_asset.path if audio_asset else '-'}（替代 TTS 配音）")
                if st.button("移除自带音频", key=f"rmua_{pid}_{s.scene_id}", disabled=busy,
                             help="移除后回到 TTS 配音，gen_assets 起重跑"):
                    try:
                        edits.remove_user_audio(store, project, s.scene_id)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
            else:
                st.caption("可上传自己的录音（mp3/wav/m4a）替代 TTS 配音，本地 Whisper 转写为字幕")
                ua_up = st.file_uploader(
                    "上传自带音频", type=["mp3", "wav", "m4a"],
                    key=f"uaup_{pid}_{s.scene_id}", disabled=busy,
                )
                if ua_up is not None:
                    # 上传临时文件写项目目录，处理完即删（data/ 本地 git 不上推）
                    tmp = store.project_dir(pid) / (".tmp_ua_upload" + (Path(ua_up.name).suffix or ".mp3"))
                    tmp.write_bytes(ua_up.getbuffer())
                    try:
                        edits.add_user_audio(store, project, s.scene_id, tmp)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop(f"uaup_{pid}_{s.scene_id}", None)
                        st.success("自带音频已上传：请到「流水线」页运行 transcribe 生成字幕")
                        st.rerun()
                    finally:
                        tmp.unlink(missing_ok=True)
            scene_subs = [sub.text for sub in project.subtitles if sub.scene_id == s.scene_id]
            if scene_subs:
                st.caption("转写字幕: " + " / ".join(scene_subs)[:120])
            st.caption(f"status={s.status}  start={s.start_ms}ms  cost={s.cost}")

            _render_candidate_gallery(store, project, s, pid, busy)

            op1, op2, op3 = st.columns(3)
            if op1.button("上移", key=f"moveup_{pid}_{s.scene_id}",
                          disabled=busy or i == 0):
                reordered = list(project.scenes)
                reordered[i - 1], reordered[i] = reordered[i], reordered[i - 1]
                edits.update_scenes(store, project, reordered)
                _clear_scene_edit_keys(pid)
                st.rerun()
            if op2.button("下移", key=f"movedown_{pid}_{s.scene_id}",
                          disabled=busy or i == len(project.scenes) - 1):
                reordered = list(project.scenes)
                reordered[i], reordered[i + 1] = reordered[i + 1], reordered[i]
                edits.update_scenes(store, project, reordered)
                _clear_scene_edit_keys(pid)
                st.rerun()
            if op3.button("删除", key=f"delscene_{pid}_{s.scene_id}", disabled=busy):
                edits.update_scenes(
                    store, project, [x for x in project.scenes if x.scene_id != s.scene_id]
                )
                _clear_scene_edit_keys(pid)
                st.rerun()

            ri1, ri2 = st.columns([4, 1])
            instr = ri1.text_input(
                "AI 重写指令", value="", key=f"rewrite_instr_{pid}_{s.scene_id}",
                placeholder="如：画面改为夜晚，景别拉近（不填则重写画面/运镜，文案默认不动）",
                disabled=busy,
            )
            if ri2.button("AI 重写", key=f"rewrite_{pid}_{s.scene_id}",
                          disabled=busy or not instr.strip()):
                _start_node(store, project, "rewrite_scene", text=instr, scene_id=s.scene_id)

            new_scenes.append(
                s.model_copy(update={
                    "narration": narration, "visual": visual, "image_prompt": prompt,
                    "motion": motion, "shot_size": shot,
                    "planned_duration_ms": dur or None, "sfx": sfx,
                })
            )
            if (narration, visual, prompt, motion, shot, dur or None, sfx) != (
                s.narration, s.visual, s.image_prompt, s.motion, s.shot_size,
                s.planned_duration_ms, s.sfx,
            ):
                edited = True

    if st.button("添加分镜", key=f"addscene_{pid}", disabled=busy):
        edits.update_scenes(
            store, project, [*project.scenes, Scene(scene_id=_next_scene_id(project), narration="")]
        )
        _clear_scene_edit_keys(pid)
        st.rerun()

    if st.button("保存全部修改", key=f"save_scenes_{pid}", disabled=not edited or busy):
        try:
            edits.update_scenes(store, project, new_scenes)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _clear_scene_edit_keys(pid)
            st.success("修改已保存，下游节点已重置为待运行")
            st.rerun()

    if project.pipeline["confirm"] != "done":
        if st.button("确认分镜（进入素材生成）", type="primary", key=f"confirm_{pid}", disabled=busy):
            if pipeline.run_confirm(store, project):
                st.success("已确认")
                st.rerun()
            else:
                st.error(f"确认失败: {errors.describe_list(project.errors)}")


# ---------------------------------------------------------------- 页面 5：成本面板

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
        section = st.radio(
            "功能", ["项目管理", "策划", "分镜确认", "流水线", "成本面板"], key="section",
        )
        ids = store.list_project_ids()
        if ids:
            # 创建/「选择」按钮改写的选中项：必须在 selectbox（widget key=pid）实例化
            # 之前预设，否则报 "cannot be modified after the widget ... is instantiated"
            pending = st.session_state.pop("pending_pid", None)
            if pending in ids:
                st.session_state["pid"] = pending
            if st.session_state.get("pid") not in ids:   # 会话 pid 已被删除 → 守卫
                st.session_state.pop("pid", None)
            st.selectbox("项目", ids, key="pid",
                         index=ids.index(st.session_state["pid"]) if st.session_state.get("pid") in ids else 0)
        else:
            st.info("暂无项目，请先在「项目管理」创建")

    if section == "项目管理":
        _render_projects(store)
        _render_task_result()
        _render_task_progress()   # fragment 全页注册：切到项目管理页也消费任务
        return
    project = _require_project(store)
    if project is None:
        return
    if section == "策划":
        _render_brief(store, project)
    elif section == "流水线":
        _render_pipeline(store, project)
    elif section == "分镜确认":
        _render_storyboard(store, project)
    else:
        _render_cost(project)
    # M6-7.8 起结果区 + 进度 fragment 挂在 main：ad-hoc 任务（reroll/refine/rewrite）
    # 从分镜确认页发起，进度/结果也在该页消费（进度条跨页可见）。
    _render_task_result()
    _render_task_progress()       # 注册并内联执行进度 fragment（全页放最后）


main()
