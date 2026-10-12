"""浏览器接管 · CDP 客户端（零第三方依赖）

原理（对应《内网本地自动化方案.md》的「人工登录 + 程序接管浏览器」）：
  1. 人工用调试模式启动 Chrome/Edge（--remote-debugging-port=9222）
  2. 人工正常登录内网系统（含短信验证码）
  3. 程序通过 CDP 连接 9222 端口，复用已登录会话
  4. 程序继续执行后续动作（点击、下载）

⚠️ 安全说明：仅用于**本人已授权账号**的自动化操作。
   验证码由本人在场手工输入，程序不绕过任何认证。

零依赖实现：只用标准库 http.client / websocket 手写 CDP 客户端。
（WebSocket 帧协议手写，避免引入 websocket-client）
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..core.logger import get_logger

log = get_logger("browser")


# ------------------------------------------------------------------ CDP 客户端
class CDP:
    """极简 CDP 客户端（手写 WebSocket，仅依赖标准库）"""

    def __init__(self, ws_url: str, timeout: float = 20.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._msg_id = 0

    # ---- 握手 ----
    def connect(self) -> None:
        from urllib.parse import urlparse

        u = urlparse(self.ws_url)
        host, port = u.hostname, u.port or 80
        path = u.path + (f"?{u.query}" if u.query else "")

        sock = socket.create_connection((host, port), timeout=self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(req.encode())

        # 读取握手响应头
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        if b"101" not in buf.split(b"\r\n")[0]:
            raise ConnectionError(f"WebSocket 握手失败：{buf.split(chr(13).encode())[0]!r}")

        sock.settimeout(self.timeout)
        self._sock = sock
        log.debug("CDP 已连接: %s", self.ws_url)

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ---- 帧收发 ----
    def _send(self, payload: dict) -> int:
        self._msg_id += 1
        payload["id"] = self._msg_id
        data = json.dumps(payload).encode()
        frame = self._encode_frame(data)
        assert self._sock
        self._sock.sendall(frame)
        return self._msg_id

    def _encode_frame(self, data: bytes) -> bytes:
        header = bytearray([0x81])          # FIN + text
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return bytes(header) + masked

    def _recv_frame(self) -> bytes:
        assert self._sock
        hdr = self._read_exact(2)
        ln = hdr[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._read_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._read_exact(8))[0]
        return self._read_exact(ln)

    def _read_exact(self, n: int) -> bytes:
        assert self._sock
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("连接已关闭")
            buf += chunk
        return buf

    def call(self, method: str, params: dict | None = None) -> dict:
        """发一条 CDP 命令并等结果"""
        mid = self._send({"method": method, "params": params or {}})
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            msg = json.loads(self._recv_frame().decode())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP 错误: {msg['error']}")
                return msg.get("result", {})
            # 忽略事件推送
        raise TimeoutError(f"CDP 调用超时: {method}")

    # ---- 便捷方法 ----
    def eval_js(self, expr: str, await_promise: bool = False) -> Any:
        r = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        return r.get("result", {}).get("value")

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})

    def goto_and_wait(self, url: str, wait: float = 3.0) -> None:
        self.navigate(url)
        time.sleep(wait)

    def click(self, selector: str) -> bool:
        """按 CSS 选择器点击"""
        js = f"""(function(){{
            const el = document.querySelector({json.dumps(selector)});
            if(!el) return false;
            el.click();
            return true;
        }})()"""
        return bool(self.eval_js(js))

    def click_text(self, text: str, tag: str = "*") -> bool:
        """按可见文本点击"""
        js = f"""(function(){{
            const els = [...document.querySelectorAll({json.dumps(tag)})];
            const el = els.find(e => e.textContent.trim() === {json.dumps(text)})
                    || els.find(e => e.textContent.trim().includes({json.dumps(text)}));
            if(!el) return false;
            el.click();
            return true;
        }})()"""
        return bool(self.eval_js(js))

    def set_file_input(self, selector: str, files: list[str]) -> None:
        """给 file input 设置文件（需 CDP DOM.setFileInputFiles）"""
        doc = self.call("DOM.getDocument")
        node = self.call("DOM.querySelector", {
            "nodeId": doc["root"]["nodeId"], "selector": selector,
        })
        if not node.get("nodeId"):
            raise RuntimeError(f"未找到文件输入框: {selector}")
        self.call("DOM.setFileInputFiles", {"files": files, "nodeId": node["nodeId"]})

    def wait_for(self, js_expr: str, timeout: float = 30.0, interval: float = 0.5) -> bool:
        """轮询等待某条件成立"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval_js(js_expr):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False


# ------------------------------------------------------------------ 连接管理
@dataclass
class BrowserInfo:
    version: str = ""
    ws_url: str = ""
    ok: bool = False
    message: str = ""


def check_debug_port(port: int = 9222, host: str = "127.0.0.1") -> BrowserInfo:
    """探测调试端口是否可用（Chrome/Edge 已用调试模式启动）"""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=3) as r:
            data = json.loads(r.read().decode())
        return BrowserInfo(
            version=data.get("Browser", "未知"),
            ws_url=data.get("webSocketDebuggerUrl", ""),
            ok=True,
            message="调试端口可用",
        )
    except Exception as e:
        return BrowserInfo(ok=False, message=f"调试端口不可用：{e}")


def list_pages(port: int = 9222, host: str = "127.0.0.1") -> list[dict]:
    """列出所有标签页"""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/json/list", timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return []


def connect_page(port: int = 9222, host: str = "127.0.0.1",
                 url_contains: str | None = None) -> CDP:
    """连接到一个标签页

    url_contains: 按 URL 关键词选定页面（如 "132.121" 是 CDP 系统）
    """
    pages = [p for p in list_pages(port, host) if p.get("type") == "page"]
    if not pages:
        raise RuntimeError("没有可用的标签页（请先在浏览器里打开目标系统）")

    target = None
    if url_contains:
        for p in pages:
            if url_contains in (p.get("url") or ""):
                target = p
                break
        if target is None:
            urls = [p.get("url", "")[:60] for p in pages]
            raise RuntimeError(
                f"未找到 URL 含「{url_contains}」的标签页。当前页面：{urls}"
            )
    else:
        target = pages[0]

    cdp = CDP(target["webSocketDebuggerUrl"])
    cdp.connect()
    return cdp


def launch_hint(browser: str = "chrome", port: int = 9222) -> str:
    """生成"如何启动调试模式"的提示（给用户看）"""
    if browser.lower() in ("edge",):
        path = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
        name = "Microsoft Edge"
    else:
        path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        name = "Google Chrome"

    return f"""请按以下步骤开启调试模式（{name}）：

1. 完全退出 {name}（Cmd+Q）

2. 打开「终端」，执行：
   "{path}" --remote-debugging-port={port}

3. 在弹出的浏览器里，正常登录目标系统（含短信验证码）

4. 回到本平台，点「接管浏览器」即可

⚠️ 注意：调试模式下会使用独立的配置目录，可能需要重新登录一次。
   这是正常的 —— 之后同一次会话内的操作都无需再登录。
"""
