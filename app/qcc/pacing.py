"""限制页面访问压力，跨批次/重启保留间隔与验证后的冷却期。"""
import json
import time
from pathlib import Path

from .errors import QueryError, QueryPaused


class InterfacePacer:
    """Limit the gap between consecutive browser/interface calls."""
    def __init__(self, path, interval=3, stopped=lambda: False, clock=time.time, sleep=time.sleep):
        self.path = Path(path)
        self.interval = max(3, interval)
        self.stopped, self.clock, self.sleep = stopped, clock, sleep
        try:
            self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not isinstance(self.state, dict):
                raise ValueError()
            value = self.state.get("last_call", 0)
            if type(value) not in (int, float) or not 0 <= value < float("inf"):
                raise ValueError()
        except (ValueError, OSError) as exc:
            raise QueryError("接口访问间隔状态无法读取，已暂停以避免连续请求。") from exc

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state))
        temp.replace(self.path)

    def before_call(self):
        due = self.state.get("last_call", 0) + self.interval
        while self.clock() < due:
            if self.stopped():
                raise QueryPaused("已暂停，接口访问等待已取消。")
            self.sleep(min(.5, max(0, due - self.clock())))
        if self.stopped():
            raise QueryPaused("已暂停，未发送新的接口访问。")
        self.state["last_call"] = self.clock()
        self._save()


class NavigationPacer:
    def __init__(self, path, interval=60, stopped=lambda: False, report=lambda *args: None,
                 clock=time.time, sleep=time.sleep):
        self.path = Path(path)
        self.interval = max(60, interval)
        self.stopped, self.report, self.clock, self.sleep = stopped, report, clock, sleep
        try:
            self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not isinstance(self.state, dict):
                raise ValueError()
            for value in self.state.values():
                if type(value) not in (int, float) or not 0 <= value < float('inf'):
                    raise ValueError()
        except (ValueError, OSError) as exc:
            raise QueryError("访问间隔状态无法读取，已暂停以避免连续请求。") from exc

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state))
        temp.replace(self.path)

    def before_navigation(self):
        spacing = max(self.interval, self.state.get("penalty_interval", 0))
        due = max(self.state.get("last_navigation", 0) + spacing, self.state.get("cooldown_until", 0))
        last_report = 0
        while self.clock() < due:
            if self.stopped():
                raise QueryPaused("已暂停，访问等待已取消。")
            if not last_report or self.clock() - last_report >= 30:
                self.report(due, spacing)
                last_report = self.clock()
            self.sleep(min(.5, max(0, due - self.clock())))
        if self.stopped():
            raise QueryPaused("已暂停，未发送新的页面访问。")
        self.state["last_navigation"] = self.clock()
        count = self.state.get("navigation_count", 0) + 1
        self.state["navigation_count"] = count % 10
        if count >= 10:
            self.state["cooldown_until"] = self.clock() + 300
        self._save()

    def blocked(self):
        strikes = self.state.get("blocks", 0) + 1
        self.state.update(blocks=strikes,
                          penalty_interval=min(600, max(self.interval, self.state.get("penalty_interval", 0)) * 2),
                          cooldown_until=self.clock() + min(3600, 900 * 2 ** min(strikes - 1, 2)))
        self._save()
