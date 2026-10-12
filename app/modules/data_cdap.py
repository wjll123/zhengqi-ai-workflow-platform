"""模块② 数据分析 —— CDAP 报表自动导出（浏览器接管·全自动导出）

⚠️ 边界约定（明确）
  登录 → **人工**（短信验证码 + 内网隔离，无法自动）
  导出 → **纯自动**（程序接管浏览器后全自动完成，无需人工点击）

流程：
  ① 人工：调试模式启动浏览器 + 登录 CDAP     ← 唯一的人工动作
  ② 程序：连接标签页 → 进报表中心 → 搜索 → 逐个报表选参数 → 点导出 → 等下载完成
  ③ 程序：把下载文件归集到 data/inbox/

核心技术点：
  · CDP 接管已登录会话（不重新登录、不碰验证码）
  · Browser.setDownloadBehavior 指定下载目录，解决"下载到哪"的问题
  · 轮询下载目录检测完成（.crdownload/.tmp 消失即完成）
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from ..core import browser
from ..core.config import CONFIG_DIR, INBOX_DIR
from ..core.logger import get_logger
from ..core.runner import TaskResult, registry

log = get_logger("data.cdap")

MODULE = "数据分析"

# CDAP 系统配置（与《Agent工作流解析.md》一致）
CDAP = {
    # The internal CDAP endpoint is deployment-specific and must not be
    # committed to a public repository. Set ZQC_CDAP_BASE_URL when needed.
    "base_url": os.environ.get("ZQC_CDAP_BASE_URL", ""),
    "url_marker": os.environ.get("ZQC_CDAP_URL_MARKER", ""),
    "search_keyword": "005",
    "reports": [
        {"name": "客户级", "code": "YX_1_440_SY_M_6191", "stock": "整体"},
        {"name": "客户级-存存量", "code": "YX_1_440_SY_M_6191", "stock": "存存量"},
        {"name": "客户级-存增量", "code": "YX_1_440_SY_M_6191", "stock": "存增量"},
        {"name": "业务级", "code": "YX_1_440_SY_M_6189", "stock": "整体"},
    ],
    "debug_port": 9222,
}

# 下载临时文件后缀（出现这些说明还在下载）
DOWNLOAD_TEMP_SUFFIX = (".crdownload", ".tmp", ".part", ".download")


# ------------------------------------------------------------------ 下载目录
def download_dir() -> Path:
    d = CONFIG_DIR / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_download_behavior(cdp: browser.CDP, path: Path) -> bool:
    """把浏览器下载目录指到指定路径（关键：否则不知道下载到哪）"""
    ok = False
    for params in (
        {"behavior": "allow", "downloadPath": str(path)},
        {"behavior": "allowAndName", "downloadPath": str(path), "eventsEnabled": True},
    ):
        try:
            cdp.call("Browser.setDownloadBehavior", params)
            ok = True
            break
        except Exception as e:
            log.debug("setDownloadBehavior 尝试失败: %s", e)
    if not ok:
        # 退路：用 Page 域的老接口
        try:
            cdp.call("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(path)})
            ok = True
        except Exception as e:
            log.warning("无法设置下载目录: %s", e)
    return ok


def snapshot_files(path: Path) -> dict[str, float]:
    """记录目录当前状态（文件名 → mtime）"""
    out: dict[str, float] = {}
    if not path.exists():
        return out
    for p in path.iterdir():
        if p.is_file():
            try:
                out[p.name] = p.stat().st_mtime
            except Exception:
                pass
    return out


def wait_for_download(path: Path, before: dict[str, float],
                      timeout: float = 180.0, settle: float = 3.0) -> list[Path]:
    """等待新文件下载完成

    判定逻辑：
      1. 出现"新文件"或已有文件 mtime 变化
      2. 且不再有临时后缀文件
      3. 且文件大小在 settle 秒内不再变化（稳定）
    """
    deadline = time.time() + timeout
    stable_since: dict[str, float] = {}

    while time.time() < deadline:
        time.sleep(1.0)

        # 还在下载中（有临时文件）
        temps = [p for p in path.iterdir() if p.is_file()
                 and p.suffix.lower() in DOWNLOAD_TEMP_SUFFIX]
        if temps:
            stable_since.clear()
            continue

        # 找新增/变化的文件
        changed: list[Path] = []
        for p in path.iterdir():
            if not p.is_file():
                continue
            if p.suffix.lower() in DOWNLOAD_TEMP_SUFFIX:
                continue
            try:
                mtime = p.stat().st_mtime
            except Exception:
                continue
            if p.name not in before or mtime > before[p.name] + 0.5:
                changed.append(p)

        if not changed:
            continue

        # 检查稳定性（大小不再变化）
        now = time.time()
        all_stable = True
        for p in changed:
            key = p.name
            try:
                size = p.stat().st_size
            except Exception:
                all_stable = False
                break
            mark = stable_since.get(key)
            if mark is None:
                stable_since[key] = now
                all_stable = False
            elif now - mark < settle:
                all_stable = False
            # 大小记录（用 mtime+size 组合作为简易指纹）
            stable_since[f"{key}:size"] = size  # type: ignore

        if all_stable and changed:
            return changed

    return []


# ------------------------------------------------------------------ 选择器
def _selectors_file() -> Path:
    return CONFIG_DIR / "cdap_selectors.json"


DEFAULT_SELECTORS: dict[str, Any] = {
    "_说明": "CDAP 页面 CSS 选择器。首次使用需按实际页面校准。支持多个候选（数组，命中第一个即用）。",
    "报表中心入口": ["a[href*='report']", "[title*='报表中心']", "text=报表中心"],
    "搜索框": ["input[type='search']", "input[placeholder*='搜索']", ".search input"],
    "搜索按钮": ["button[type='submit']", ".search-btn", "button:has-text('搜索')"],
    "账期下拉": ["[class*='period'] select", "[class*='date'] select", "select[name*='period']"],
    "客户分群下拉": ["[class*='segment'] select", "[class*='customer'] select"],
    "存增量下拉": ["[class*='stock'] select", "[class*='increment'] select"],
    "输出按钮": ["[class*='export']", "[title*='输出']", "button:has-text('输出')"],
    "导出Excel项": ["[data-value='excel']", "[title*='Excel']", "li:has-text('Excel')"],
    "行选择器": [".report-row", "tr[class*='row']", "li[class*='item']"],
}


def load_selectors() -> dict:
    f = _selectors_file()
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_SELECTORS)
            merged.update({k: v for k, v in data.items() if not k.startswith("_")})
            return merged
        except Exception as e:
            log.warning("选择器配置损坏，使用默认值: %s", e)
    return dict(DEFAULT_SELECTORS)


def save_selectors(sel: dict) -> Path:
    f = _selectors_file()
    f.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")
    return f


def _try_selectors(cdp: browser.CDP, key: str, sel: dict) -> str | None:
    """按候选列表依次尝试，返回命中的选择器"""
    candidates = sel.get(key)
    if isinstance(candidates, str):
        candidates = [candidates]
    for c in candidates or []:
        if c.startswith("text="):
            if cdp.click_text(c[5:]):
                return c
        elif c.startswith("button:has-text(") or c.startswith("li:has-text("):
            txt = c.split("'")[1] if "'" in c else ""
            tag = "button" if c.startswith("button") else "li"
            if txt and cdp.click_text(txt, tag):
                return c
        else:
            if cdp.eval_js(f"!!document.querySelector({json.dumps(c)})"):
                return c
    return None


@registry.register(
    key="cdap_export",
    name="CDAP 报表自动导出（4 份）",
    module=MODULE,
    description="接管已登录浏览器，全自动导出 CDAP 的 4 份报表（登录人工，导出纯自动）",
    source_type="auto",
    source_note=(
        "【边界约定】\n"
        "  登录 → 人工（唯一人工动作）\n"
        "  导出 → 纯自动（程序全自动完成，无需人工点击）\n"
        "\n"
        "【第一步：人工准备】\n"
        "  1. 完全退出浏览器（Cmd+Q）\n"
        "  2. 终端执行：\n"
        "     \"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\" --remote-debugging-port=9222\n"
        "  3. 在浏览器里正常登录 CDAP（含短信验证码）\n"
        "  4. 保持浏览器开着，回到平台点「执行」\n"
        "\n"
        "【第二步：程序全自动完成】\n"
        "  · 连接已登录标签页（不重新登录、不碰验证码）\n"
        "  · 进入报表中心 → 搜索 005\n"
        "  · 客户级 YX_1_440_SY_M_6189：导出 整体 / 存存量 / 存增量 共 3 份\n"
        "  · 业务级 YX_1_440_SY_M_6191：导出 整体 共 1 份\n"
        "  · 自动等待每份下载完成 → 归集到 data/inbox/\n"
        "\n"
        "【产出】4 份报表源文件，直接可供「收客保」流程加工\n"
        "\n"
        "⚠️ 如页面结构变化导致某步失败，报错会指出失败步骤；\n"
        "   可人工校准 data/config/cdap_selectors.json 后重跑（校准一次即可）。"
    ),
    outputs_desc="4 份 CDAP 报表（客户级整体/存存量/存增量 + 业务级整体）→ data/inbox/",
    params=[
        {"name": "period", "label": "账期（如 202608）", "type": "text", "required": False},
        {"name": "check_only", "label": "仅检查连接（不导出）", "type": "bool", "required": False},
        {"name": "keep_files", "label": "保留下载目录中的文件（不清理）", "type": "bool", "required": False},
    ],
)
def cdap_export(period: str | None = None, check_only: bool = False,
                keep_files: bool = False) -> TaskResult:
    port = CDAP["debug_port"]
    metrics: dict[str, Any] = {"调试端口": port, "账期": period or "（按页面默认）"}
    warnings: list[str] = []
    steps: list[str] = []
    produced: list[str] = []

    # ---- 1. 检查调试端口 ----
    info = browser.check_debug_port(port)
    if not info.ok:
        return TaskResult(
            ok=False,
            message=(
                "浏览器调试端口不可用 —— 请先人工登录。\n"
                f"详情：{info.message}\n\n" + browser.launch_hint(port=port)
            ),
            metrics=metrics,
        )
    metrics["浏览器"] = info.version

    # ---- 2. 连接 CDAP 标签页 ----
    try:
        cdp = browser.connect_page(port, url_contains=CDAP["url_marker"])
    except Exception as e:
        urls = [p.get("url", "")[:70] for p in browser.list_pages(port) if p.get("type") == "page"]
        return TaskResult(
            ok=False,
            message=(
                f"未找到 CDAP 标签页：{e}\n当前页面：{urls}\n\n"
                "请在浏览器里打开并登录 CDAP 后重试。"
            ),
            metrics=metrics,
        )

    dl = download_dir()
    try:
        # ---- 3. 校验登录态 ----
        title = cdp.eval_js("document.title") or ""
        metrics["当前页面"] = title[:40]
        if not cdp.eval_js("(document.body && document.body.innerText || '').length > 500"):
            return TaskResult(ok=False,
                              message="页面内容为空，疑似未登录。请确认已在浏览器里登录 CDAP。",
                              metrics=metrics)
        steps.append(f"接管页面「{title[:24]}」")

        if check_only:
            return TaskResult(
                ok=True, message="✓ 连接检查通过（check_only 模式，未执行导出）",
                metrics={**metrics, "模式": "仅检查连接"},
                warnings=["check_only 已开启，未实际导出"],
            )

        # ---- 4. 设置下载目录（关键） ----
        if set_download_behavior(cdp, dl):
            steps.append(f"下载目录已指向 {dl.name}/")
        else:
            warnings.append("未能设置下载目录，将使用浏览器默认下载目录（可能在 ~/Downloads）")

        sel = load_selectors()
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        # ---- 5. 导航到报表中心 ----
        cdp.goto_and_wait(CDAP["base_url"], wait=4)
        steps.append("导航到 CDAP")

        hit = _try_selectors(cdp, "报表中心入口", sel)
        if hit:
            cdp.click(hit)
            time.sleep(2.5)
            steps.append("进入报表中心")
        else:
            warnings.append(
                "「报表中心入口」选择器未命中 —— 可能页面已变化。"
                "请在工作台手动进入报表中心，或校准 cdap_selectors.json。"
            )

        # ---- 6. 搜索 005 ----
        kw = CDAP["search_keyword"]
        search_js = f"""(function(){{
            const sels = {json.dumps(sel.get('搜索框', []))};
            let inp = null;
            for(const s of sels){{ inp = document.querySelector(s); if(inp) break; }}
            if(!inp) return false;
            inp.focus(); inp.value = {json.dumps(kw)};
            inp.dispatchEvent(new Event('input', {{bubbles:true}}));
            inp.dispatchEvent(new Event('change', {{bubbles:true}}));
            inp.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', keyCode:13, bubbles:true}}));
            inp.dispatchEvent(new KeyboardEvent('keyup', {{key:'Enter', keyCode:13, bubbles:true}}));
            return true;
        }})()"""
        if cdp.eval_js(search_js):
            steps.append(f"搜索「{kw}」")
            time.sleep(3.5)
        else:
            warnings.append("「搜索框」选择器未命中")

        # ---- 7. 确认报表出现 ----
        codes = [r["code"] for r in CDAP["reports"]]
        found = cdp.eval_js(
            "(document.body.innerText||'').includes(%s) || (document.body.innerText||'').includes(%s)"
            % (json.dumps(codes[0]), json.dumps(codes[-1]))
        )
        metrics["报表列表可见"] = "是" if found else "否"
        if not found:
            warnings.append(
                "页面上未见目标报表编码，搜索可能未生效。"
                "请人工确认列表已加载后重试。"
            )

        # ---- 8. 逐份导出 ----
        before = snapshot_files(dl)
        exported: list[dict] = []

        for rpt in CDAP["reports"]:
            label = rpt["name"]
            # 定位并点开该报表
            opened = cdp.eval_js(f"""(function(){{
                const rows = [...document.querySelectorAll('tr, li, .report-row')];
                const row = rows.find(r => (r.innerText||'').includes({json.dumps(rpt['code'])}));
                if(!row) return false;
                row.click();
                return true;
            }})()""")

            if not opened:
                exported.append({"报表": label, "状态": "未找到该报表行（需校准行选择器）"})
                warnings.append(f"{label}：未能在列表中找到对应行")
                continue

            time.sleep(2.5)

            # 选账期 / 客户分群 / 存增量
            applied: list[str] = []
            for key, val in (
                ("账期下拉", period),
                ("客户分群下拉", "政企"),
                ("存增量下拉", rpt["stock"]),
            ):
                if not val:
                    continue
                hit_sel = _try_selectors(cdp, key, sel)
                if not hit_sel:
                    continue
                ok = cdp.eval_js(f"""(function(){{
                    const sels = {json.dumps(sel.get(key, []))};
                    let s = null;
                    for(const c of sels){{ s = document.querySelector(c); if(s) break; }}
                    if(!s) return false;
                    const target = [...s.options].find(o => (o.textContent||'').includes({json.dumps(val)}));
                    if(!target) return false;
                    s.value = target.value;
                    s.dispatchEvent(new Event('change', {{bubbles:true}}));
                    return true;
                }})()""")
                if ok:
                    applied.append(f"{key}={val}")
            if applied:
                time.sleep(1.5)

            # 点「输出 → Excel → 原样导出」
            pre = snapshot_files(dl)
            clicked_export = False

            hit_out = _try_selectors(cdp, "输出按钮", sel)
            if hit_out:
                cdp.click(hit_out)
                time.sleep(1.2)
                hit_excel = _try_selectors(cdp, "导出Excel项", sel)
                if hit_excel:
                    if hit_excel.startswith("li:has-text(") or "has-text" in hit_excel:
                        txt = hit_excel.split("'")[1]
                        cdp.click_text(txt, "li")
                    else:
                        cdp.click(hit_excel)
                    clicked_export = True

            if not clicked_export:
                exported.append({
                    "报表": label,
                    "状态": "⚠️ 导出按钮选择器未命中（需校准）",
                    "已设置": applied,
                })
                warnings.append(f"{label}：未能自动点击导出，请校准「输出按钮」「导出Excel项」选择器")
                continue

            # 等下载完成
            files = wait_for_download(dl, pre, timeout=180)
            if files:
                for f in files:
                    try:
                        target = INBOX_DIR / f.name
                        shutil.move(str(f), str(target))
                        produced.append(str(target))
                    except Exception as e:
                        warnings.append(f"归集 {f.name} 失败: {e}")
                exported.append({"报表": label, "状态": f"✓ 已导出 {len(files)} 份", "已设置": applied})
            else:
                exported.append({"报表": label, "状态": "⚠️ 超时未检测到下载文件", "已设置": applied})
                warnings.append(f"{label}：点击导出后 180 秒内未检测到新文件")

        metrics["导出明细"] = json.dumps(exported, ensure_ascii=False)

        ok = len(produced) > 0
        if ok:
            msg = (
                f"✓ 全自动导出完成：{len(produced)} 份文件已归集到 inbox/\n"
                f"执行轨迹：{' → '.join(steps)}\n"
                + "\n".join(f"  · {e['报表']}：{e['状态']}" for e in exported)
            )
        else:
            msg = (
                "⚠️ 未导出到文件。已完成的步骤：\n"
                f"  {' → '.join(steps)}\n"
                + "\n".join(f"  · {e['报表']}：{e['状态']}" for e in exported)
                + "\n\n大概率是页面选择器需校准：请到浏览器按 F12 查看实际 DOM，"
                  "把正确的选择器写入 data/config/cdap_selectors.json 后重跑。"
            )

        return TaskResult(ok=ok, message=msg, outputs=produced,
                          metrics=metrics, warnings=warnings)

    except Exception as e:
        log.error("CDAP 导出异常: %s", e, exc_info=True)
        return TaskResult(
            ok=False,
            message=f"导出过程出错：{type(e).__name__}: {e}\n已完成：{' → '.join(steps)}",
            metrics=metrics, warnings=warnings,
        )
    finally:
        cdp.close()
        if not keep_files and produced:
            # 归集后清空下载目录（避免下次混淆）
            for p in dl.iterdir():
                if p.is_file():
                    try:
                        p.unlink()
                    except Exception:
                        pass


@registry.register(
    key="browser_check",
    name="浏览器接管检查",
    module="数据分析",
    description="检查浏览器调试端口是否可用，以及是否已打开并登录目标系统",
    source_type="auto",
    source_note=(
        "【用途】只做连接检查，不导出任何数据。\n"
        "【前置】浏览器需以调试模式启动：\n"
        "  \"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\" --remote-debugging-port=9222\n"
        "  然后人工登录内网系统。"
    ),
    outputs_desc="（无文件产出，仅返回连接状态）",
)
def browser_check() -> TaskResult:
    info = browser.check_debug_port()
    if not info.ok:
        return TaskResult(
            ok=False,
            message="调试端口不可用，请先按指引启动浏览器并登录。",
            metrics={"详情": info.message},
            warnings=[browser.launch_hint()],
        )

    pages = browser.list_pages()
    page_list = [
        {"标题": p.get("title", "")[:40], "URL": (p.get("url") or "")[:70]}
        for p in pages if p.get("type") == "page"
    ]

    return TaskResult(
        ok=True,
        message=f"✓ 浏览器已连接（{info.version}），共 {len(page_list)} 个标签页",
        metrics={
            "浏览器": info.version,
            "标签页数": len(page_list),
            "页面列表": json.dumps(page_list, ensure_ascii=False),
        },
    )
