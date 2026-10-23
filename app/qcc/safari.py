"""Safari 真实登录会话。独立窗口，顺序访问，验证时保留现场。"""
import subprocess
import time
import html
import re
from urllib.parse import parse_qs, quote, urlsplit

from .errors import BrowserActionRequired, PageNotReady, QueryError
from .extraction import company_data, page_state, search_target


def apple(script, *args, timeout=20):
    try:
        result = subprocess.run(["osascript", "-e", "on run argv", "-e", script,
                                 "-e", "end run", "--", *args], capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PageNotReady("Safari 仍在加载页面。") from exc
    except FileNotFoundError as exc:
        raise BrowserActionRequired("无法连接 Safari，请确认在 Mac 本机运行且浏览器响应正常。") from exc
    if result.returncode:
        raise BrowserActionRequired("Safari 操作失败，请检查窗口是否存在，以及系统是否允许本平台控制 Safari。")
    return result.stdout.rstrip("\n")


class SafariSession:
    def __init__(self, window_id=None, timeout=25):
        self.window_id = window_id
        self.preserve_window = False
        self.timeout = timeout
        self.before_navigation = lambda: None
        self.before_call = lambda: None

    def _call(self, script, *args, timeout=20):
        self.before_call()
        return apple(script, *args, timeout=timeout)

    def __enter__(self):
        if self.window_id and str(self.window_id).isdigit():
            try:
                url = self._call('tell application "Safari" to get URL of current tab of window id (item 1 of argv as integer)', self.window_id)
                if urlsplit(url).hostname in {"www.qcc.com", "m.qcc.com", "qcc.com"}:
                    return self
            except QueryError:
                pass
        self.window_id = self._call('''tell application "Safari"
set beforeIds to id of every window
make new document with properties {URL:"about:blank"}
set afterIds to id of every window
repeat with candidate in afterIds
if candidate is not in beforeIds then return candidate as string
end repeat
end tell''')
        if not self.window_id.isdigit():
            raise BrowserActionRequired("未能识别 Safari 查询窗口，请检查浏览器。")
        return self

    def __exit__(self, kind, error, traceback):
        if isinstance(error, BrowserActionRequired):
            self.preserve_window = True
        if self.window_id and not self.preserve_window:
            try:
                self._call('tell application "Safari" to close window id (item 1 of argv as integer)', self.window_id)
            except QueryError:
                pass

    def _page(self):
        # 分别读取前后 URL，防止用户导航时把另一页面的资料绑定到旧链接。
        script = 'tell application "Safari" to get {} of current tab of window id (item 1 of argv as integer)'
        before = self._call(script.format("URL"), self.window_id)
        title = self._call(script.format("name"), self.window_id)
        try:
            page_state("<title>" + html.escape(title) + "</title>", before)
        except PageNotReady:
            pass
        source = self._call(script.format("source"), self.window_id, timeout=5)
        after = self._call(script.format("URL"), self.window_id)
        if before != after:
            raise PageNotReady("Safari 正在切换页面。")
        return after, source

    def _load(self, target, extract):
        self.before_navigation()
        self._call('tell application "Safari" to set URL of current tab of window id (item 1 of argv as integer) to item 2 of argv', self.window_id, target)
        deadline = time.monotonic() + self.timeout
        target_path = urlsplit(target).path
        search_key = parse_qs(urlsplit(target).query).get("key")
        while time.monotonic() < deadline:
            try:
                url, source = self._page()
                page_state(source, url)  # 任意跳转上的验证或登录页都应立即停止。
                if urlsplit(url).path != target_path:
                    raise PageNotReady("目标页面尚未加载。")
                if search_key and parse_qs(urlsplit(url).query).get("key") != search_key:
                    raise PageNotReady("搜索页仍是上一家企业。")
                return extract(source, url)
            except PageNotReady:
                time.sleep(0.5)
        raise BrowserActionRequired("页面在 25 秒内未提供完整资料，已暂停。请在 Safari 检查页面后继续。")

    def query(self, name, credit_code="", detail_url=""):
        try:
            # 续查时先复用刚完成验证的目标页，不必重新搜索同一企业。
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
