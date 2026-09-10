"""生成 docs/screenshots/ 的工作台截图（一次性工具，非运行时依赖）。

用法：
    pip install -e .[dev]
    python -m playwright install chromium     # 浏览器缓存位置见 PLAYWRIGHT_BROWSERS_PATH
    python tools/screenshot_workbench.py

做法：把若干演示项目拷进**隔离数据目录**（不碰仓库 data/），经 app/web/run.py 启动
工作台（Windows 必须走 SelectorEventLoop，见该文件 docstring），playwright 逐页截图。
截的是真实渲染，不做任何图像拼贴 —— 页面里出现的都是项目自身的真实数据。

**前置条件**：`data/projects/` 下要有 DEMOS 列出的演示项目（真实跑过、含生图与
配音产物）。`data/` 不随仓库分发（它是独立的本地 git 仓库，见 .gitignore），所以
这个脚本是给「本地已有真实产物的开发者」重新生成截图用的，不是 CI 能跑的工具。

数据目录：遵循 tests/conftest.py 的 AVPO_TEST_TMP 约定（多个候选 os.pathsep 分隔，
按序取第一个可创建者；未配置退回系统临时目录），公开仓库不含本机路径。隔离数据
目录、chromium 的临时 profile 都落在这里 —— 不往用户目录写东西。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "screenshots"
PORT = 8599

# 演示项目：源项目目录 → 展示用 project_id（= 数据目录里的目录名，必须与
# project.json 的 project_id 一致，否则保存会写错路径）+ 展示用标题。
# 源项目是开发期真实跑出来的（含真实生图/TTS 产物），这里只换 id 与标题。
DEMOS: list[tuple[str, str, str]] = [
    ("proj_m2speed", "demo_001", "AI 视频工作流演示"),
    ("proj_m2live", "demo_002", "一键流水线演示"),
    ("proj_m1demo", "demo_003", "真实链路演示"),
]

# demo_001 的展示用策划简报：源项目跑在 M6 之前，没有 brief 数据，不补的话策划页
# 是一张全空表单。内容按该项目**真实产出的口播与既有画面描述**回填 —— 截图展示的
# 是产品形态，不是某个真实创作者填过的简报。
DEMO_BRIEF: dict[str, str] = {
    "theme": "AI 做视频到底靠不靠谱",
    "worldview": "短视频创作者视角，回答观众的真实疑问",
    "art_style": "简洁科技感，图示 + 实拍混合",
    "duration": "60s",
    "platform": "抖音 / B站",
    "protagonist": "干练的短视频创作者，简洁科技感办公室",
    "plot": "从疑问出发，对比传统流程与 AI 流程，落到「一条命令出片」",
    "emotion": "直接、笃定、有说服力",
    "palette": "科技蓝 + 白底，高对比",
    "lighting": "clean studio lighting, bright and even",
    "character": "东亚年轻创作者，简洁商务休闲装",
    "bgm_hint": "节奏明快的电子乐，全片稳定推进，结尾一句收住",
}

# 逐页截图：(文件名, 侧边栏 radio 文案, 主区就绪锚点, 下滚像素)
# 下滚像素用于把关键内容带进视野 —— 分镜页首屏是文案/画面描述等表单字段，
# 生成图在表单之后，不滚动就截不到图。
PAGES: list[tuple[str, str, str, int]] = [
    ("01-projects.png", "项目管理", "项目管理", 0),
    ("02-brief.png", "策划", "创作简报", 0),
    ("03-storyboard.png", "分镜确认", "分镜确认", 600),
    ("04-pipeline.png", "流水线", "流水线", 0),
]

# 隐藏 streamlit 自身的开发态 chrome（Deploy/Stop/状态挂件）—— 截图只展示应用本身。
HIDE_STREAMLIT_CHROME = """
[data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], [data-testid="stHeader"] { display: none !important; }
"""


def _pick_tmp_root() -> Path:
    """与 tests/conftest.py 同源：读 AVPO_TEST_TMP，未配置退回系统临时目录。"""
    raw = os.environ.get("AVPO_TEST_TMP", "")
    for candidate in (Path(p) for p in raw.split(os.pathsep) if p.strip()):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    fallback = Path(tempfile.gettempdir()) / "avpo-screenshots"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


# 必须在 tempfile / playwright 使用临时目录之前设置（同 tests/conftest.py）：
# chromium 的 profile 临时目录也读 TMP/TEMP，重定向后不落用户目录。
_TMP_ROOT = _pick_tmp_root()
os.environ["TMP"] = str(_TMP_ROOT)
os.environ["TEMP"] = str(_TMP_ROOT)


def build_sandbox(root: Path) -> Path:
    """拷贝演示项目到隔离数据目录，返回该目录。"""
    data_dir = root / "screenshot-data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    projects = data_dir / "projects"
    projects.mkdir(parents=True)

    for src_id, dst_id, title in DEMOS:
        src = ROOT / "data" / "projects" / src_id
        if not src.is_dir():
            raise SystemExit(f"缺少演示项目: {src}（需要本机 data/ 下的真实产物）")
        dst = projects / dst_id
        shutil.copytree(src, dst)

        # 导出目录名内嵌 project_id，改 id 就一并改名，截图里路径才自洽
        exports = dst / "exports"
        for old, new in ((f"{src_id}_draft", f"{dst_id}_draft"),
                         (f"{src_id}_draft.zip", f"{dst_id}_draft.zip")):
            if (exports / old).exists():
                (exports / old).rename(exports / new)

        # project_id 必须与目录名一致（ProjectStore 按 project_id 定位文件）
        doc = json.loads((dst / "project.json").read_text(encoding="utf-8"))
        doc["project_id"] = dst_id
        doc["title"] = title
        if doc.get("export"):
            doc["export"]["path"] = f"exports/{dst_id}_draft"
        if dst_id == "demo_001":
            doc["brief"] = DEMO_BRIEF
        (dst / "project.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return data_dir


def start_workbench(data_dir: Path) -> subprocess.Popen:
    """按 app/cli.py 的 web 命令同款方式启动（含 C 盘规避与 SelectorEventLoop）。"""
    web_home = data_dir / "webhome"
    web_home.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "AVPO_DATA": str(data_dir),
        "USERPROFILE": str(web_home),   # streamlit 磁盘缓存不落用户目录
        "HOME": str(web_home),
    }
    cmd = [
        sys.executable, "-m", "app.web.run", "run",
        str(ROOT / "app" / "web" / "app.py"),
        "--server.port", str(PORT),
        "--server.headless", "true",
        "--server.address", "localhost",
        "--browser.gatherUsageStats", "false",
    ]
    return subprocess.Popen(cmd, cwd=ROOT, env=env)


def wait_for_port(port: int, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise SystemExit(f"工作台 {timeout:.0f}s 内未在端口 {port} 就绪")


def main() -> None:
    from playwright.sync_api import sync_playwright

    data_dir = build_sandbox(_TMP_ROOT)
    print(f"隔离数据目录: {data_dir}")
    print(f"演示项目: {', '.join(dst for _, dst, _ in DEMOS)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    proc = start_workbench(data_dir)
    try:
        wait_for_port(PORT)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1600, "height": 1000}, device_scale_factor=2
            )
            page.goto(f"http://localhost:{PORT}", wait_until="load")
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=60000)
            page.add_style_tag(content=HIDE_STREAMLIT_CHROME)
            main_area = page.locator('[data-testid="stMain"]').first
            sidebar = page.locator('[data-testid="stSidebar"]').first

            for filename, section, anchor, scroll in PAGES:
                sidebar.get_by_text(section, exact=True).first.click()
                # 页头文本随数据变化（如「流水线 「标题」」），exact 匹配不上时退回子串
                try:
                    main_area.get_by_text(anchor, exact=True).first.wait_for(timeout=15000)
                except Exception:
                    main_area.get_by_text(anchor).first.wait_for(timeout=45000)
                page.wait_for_timeout(2000)   # 图片/图表落地
                # stMain 是内部滚动容器（不是 body），full_page 截不到，只能显式滚动
                main_area.evaluate("(el, y) => { el.scrollTop = y; }", scroll)
                page.wait_for_timeout(400)
                path = OUT_DIR / filename
                page.screenshot(path=str(path))
                print(f"  写出 {path.relative_to(ROOT)}")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n完成：{len(PAGES)} 张 → {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
