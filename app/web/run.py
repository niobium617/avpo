"""Windows 工作台启动入口：强制 SelectorEventLoop（修 WinError 10014 accept 失败）。

问题：asyncio 默认 ProactorEventLoop 在部分 Windows 机器上 accept 新连接时报
OSError 10014（WSAEFAULT 无效指针）——IOCP accept 路径被杀软/winsock 状态干扰，
浏览器一连服务器就炸（2026-08-24 本机复现）。SelectorEventLoop 不走 IOCP accept，
避开该问题。

取舍（Python 官方文档）：SelectSelector 最多 512 socket（本工具单用户 localhost
远够）；不支持 asyncio 子进程（streamlit server 不需要）。

必须在 import streamlit 之前设置 policy，否则 uvicorn 仍按默认策略建 Proactor 循环。
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from streamlit.web.cli import main  # noqa: E402  —— 必须在 policy 设置之后 import

if __name__ == "__main__":
    main(prog_name="streamlit")
