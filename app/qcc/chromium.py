"""Chrome/Edge remote-debugging backend for QCC queries.

The user starts Chrome or Edge with a local debugging port and logs in by hand.
The worker only reads/navigates the already open qcc.com tab through CDP. It
does not bypass login, CAPTCHA, or access restrictions.
"""
import html
import json
import re
import time
from urllib.parse import parse_qs, quote, urlsplit

from ..core.browser import CDP, check_debug_port, list_pages
from .errors import BrowserActionRequired, PageNotReady, QueryError
from .extraction import company_data, page_state, search_target


QCC_HOSTS = {"www.qcc.com", "m.qcc.com", "qcc.com"}


def is_qcc_page(page):
    try:
        parsed = urlsplit(page.get("url") or "")
        return page.get("type") == "page" and parsed.scheme == "https" and parsed.hostname in QCC_HOSTS
    except ValueError:
        return False


def browser_status(browser, port):
    name = "Microsoft Edge" if browser == "edge" else "Google Chrome"
    info = check_debug_port(port)
    if not info.ok:
        return {"browser": browser, "port": port, "ready": False, "connected": False,
                "message": f"{name} 调试端口 {port} 不可用。请按页面说明启动浏览器。"}
    version = info.version.lower()
    matches = ("edg/" in version or "edge/" in version) if browser == "edge" else (
        ("chrome/" in version or "chromium/" in version) and "edg/" not in version and "edge/" not in version)
    if not matches:
        return {"browser": browser, "port": port, "ready": False, "connected": False,
                "message": f"端口 {port} 当前属于 {info.version}，与所选 {name} 不一致。"}
    all_pages = list_pages(port)
    pages = [page for page in all_pages if is_qcc_page(page)]
    if not pages:
        return {"browser": browser, "port": port, "ready": False, "connected": True,
                "message": f"{name} 已连接；请在调试窗口打开并登录 https://www.qcc.com/。",
                "pages": pages, "all_pages": all_pages}
    if len(pages) > 1:
        return {"browser": browser, "port": port, "ready": False, "connected": True,
                "message": f"找到 {len(pages)} 个企查查标签页。新批次请只保留一个企查查标签页，避免选错页面。",
                "pages": pages, "all_pages": all_pages}
    return {"browser": browser, "port": port, "ready": True, "connected": True,
            "message": f"{name} 已连接，找到 {len(pages)} 个企查查标签页。",
            "pages": pages, "all_pages": all_pages}


class ChromiumSession:
    def __init__(self, window_id=None, timeout=25, browser="chrome", debug_port=9222):
        self.window_id = window_id
        self.timeout = timeout
        self.browser = browser
        self.debug_port = int(debug_port)
        self.preserve_window = False
        self.before_navigation = lambda: None
        self.before_call = lambda: None
        self.cdp = None

    def _call(self, method, params=None):
        self.before_call()
        try:
            return self.cdp.call(method, params or {})
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise BrowserActionRequired(f"{self.browser.title()} 调试连接已断开，请检查浏览器窗口后继续：{exc}") from exc

    def _eval(self, expression):
        self.before_call()
        try:
            return self.cdp.eval_js(expression)
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise BrowserActionRequired(f"{self.browser.title()} 调试连接已断开，请检查浏览器窗口后继续：{exc}") from exc

    def __enter__(self):
        status = browser_status(self.browser, self.debug_port)
        if not status.get("connected") or (not self.window_id and not status["ready"]):
            raise BrowserActionRequired(status["message"])
        if self.window_id:
            target = next((page for page in status["all_pages"] if page.get("type") == "page" and page.get("id") == self.window_id), None)
            if target is None:
                raise BrowserActionRequired("原企查查标签页已关闭。请恢复原标签页，或创建新批次。")
        else:
            target = status["pages"][0]
            self.window_id = target.get("id")
        try:
            ws_url = urlsplit(target.get("webSocketDebuggerUrl") or "")
            safe_socket = (ws_url.scheme == "ws" and ws_url.hostname in {"127.0.0.1", "localhost", "::1"}
                           and ws_url.port == self.debug_port)
        except ValueError:
            safe_socket = False
        if not self.window_id or not safe_socket:
            raise BrowserActionRequired("浏览器返回的调试连接地址不是当前本机端口，已拒绝连接。")
        try:
            self.cdp = CDP(target["webSocketDebuggerUrl"])
            self.cdp.connect()
        except Exception as exc:
            raise BrowserActionRequired(f"无法连接 {self.browser.title()} 企查查标签页：{exc}") from exc
        return self

    def __exit__(self, *_args):
        if self.cdp:
            self.cdp.close()
            self.cdp = None

    def _page(self):
        result = self._eval("JSON.stringify({url:location.href,title:document.title,ready:document.readyState,html:document.documentElement.outerHTML})")
        if not result:
            raise PageNotReady("浏览器页面尚未提供资料。")
        try:
            data = json.loads(result)
        except (TypeError, ValueError) as exc:
            raise PageNotReady("浏览器页面资料仍在加载。") from exc
        url, title, source = data.get("url", ""), data.get("title", ""), data.get("html", "")
        page_state("<title>" + html.escape(title) + "</title>" + source, url)
        return url, source

    def _load(self, target, extract):
        self.before_navigation()
        self._call("Page.navigate", {"url": target})
        deadline = time.monotonic() + self.timeout
        target_path = urlsplit(target).path
        search_key = parse_qs(urlsplit(target).query).get("key")
        while time.monotonic() < deadline:
            try:
                url, source = self._page()
                if urlsplit(url).path != target_path:
                    raise PageNotReady("目标页面尚未加载。")
                if search_key and parse_qs(urlsplit(url).query).get("key") != search_key:
                    raise PageNotReady("搜索页仍是上一家企业。")
                return extract(source, url)
            except PageNotReady:
                time.sleep(0.5)
        self.preserve_window = True
        raise BrowserActionRequired("页面在 25 秒内未提供完整资料，已暂停。请在浏览器检查页面后继续。")

    def query(self, name, credit_code="", detail_url=""):
        try:
            try:
                url, source = self._page()
                if urlsplit(url).path.startswith("/firm/"):
                    return company_data(source, name, credit_code, url, by_code=bool(credit_code))
            except BrowserActionRequired:
                raise
            except QueryError:
                pass
            by_code = bool(credit_code)
            if detail_url:
                parsed = urlsplit(detail_url)
                if parsed.scheme != "https" or parsed.hostname not in {"www.qcc.com", "m.qcc.com", "qcc.com"} or not re.fullmatch(r"/firm/(?:[0-9a-f]{32}|g[0-9a-f]{32})\.html", parsed.path):
                    raise QueryError("已保存的企业详情链接无效。")
                target = detail_url
            else:
                target = self._load("https://www.qcc.com/web/search?key=" + quote(credit_code or name),
                                    lambda source, url: search_target(source, name, credit_code, url, by_code=by_code))
            return self._load(target, lambda source, url: company_data(source, name, credit_code, url, by_code=by_code))
        except BrowserActionRequired:
            self.preserve_window = True
            raise
