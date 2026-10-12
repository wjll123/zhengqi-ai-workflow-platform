"""CDAP 页面探测工具 —— 帮你快速校准选择器

用法（先人工用调试模式启动浏览器并登录 CDAP）：
    ./.venv/bin/python3 -m app.tools.probe_cdap

它会把当前页面的候选元素列出来，你挑准的填进
data/config/cdap_selectors.json 即可。
"""
from __future__ import annotations

import json
import sys

from ..core import browser
from ..modules.data_cdap import CDAP, save_selectors, load_selectors


PROBE_JS = r"""
(function(){
  function info(el, i){
    if(!el) return null;
    return {
      idx: i,
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      cls: (typeof el.className === 'string' ? el.className : '').slice(0,70),
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      ph: el.getAttribute('placeholder') || '',
      title: el.getAttribute('title') || '',
      text: (el.textContent||'').trim().slice(0,40),
      sel: guess(el)
    };
  }
  function guess(el){
    if(el.id) return '#' + el.id;
    if(el.getAttribute('name')) return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    if(el.getAttribute('type')) return el.tagName.toLowerCase() + '[type="' + el.getAttribute('type') + '"]';
    const cls = (typeof el.className === 'string' ? el.className : '').split(/\s+/).filter(Boolean);
    if(cls.length) return el.tagName.toLowerCase() + '.' + cls[0];
    return el.tagName.toLowerCase();
  }
  const out = {};
  out.title = document.title;
  out.url = location.href;
  out.inputs = [...document.querySelectorAll('input')].slice(0,25).map(info).filter(Boolean);
  out.selects = [...document.querySelectorAll('select')].slice(0,25).map(info).filter(Boolean);
  out.buttons = [...document.querySelectorAll('button, a[role=button], .btn, [class*=btn]')].slice(0,40).map(info).filter(Boolean);
  out.tables = [...document.querySelectorAll('table, .table, [class*=table], [class*=list]')].slice(0,15).map(info).filter(Boolean);
  out.menus = [...document.querySelectorAll('nav a, .menu a, .sidebar a, [class*=menu] a, [class*=nav] a')].slice(0,40).map(info).filter(Boolean);
  return JSON.stringify(out);
})()
"""


def main() -> int:
    info = browser.check_debug_port(CDAP["debug_port"])
    if not info.ok:
        print("✕ 调试端口不可用，请先：")
        print(browser.launch_hint(port=CDAP["debug_port"]))
        return 1

    print(f"✓ 浏览器已连接：{info.version}\n")

    try:
        cdp = browser.connect_page(CDAP["debug_port"], url_contains=CDAP["url_marker"])
    except Exception as e:
        print(f"✕ 未找到 CDAP 标签页：{e}")
        pages = [p for p in browser.list_pages(CDAP["debug_port"]) if p.get("type") == "page"]
        print("\n当前打开的页面：")
        for i, p in enumerate(pages):
            print(f"  [{i}] {p.get('title','')[:40]}")
            print(f"      {p.get('url','')[:80]}")
        print("\n请先在浏览器里打开 CDAP，然后重跑本工具。")
        return 1

    raw = cdp.eval_js(PROBE_JS)
    cdp.close()

    if not raw:
        print("✕ 探测失败（页面可能未加载完）")
        return 1

    d = json.loads(raw)
    print("=" * 70)
    print(f"页面标题：{d['title']}")
    print(f"页面地址：{d['url'][:90]}")
    print("=" * 70)

    def show(name: str, items: list[dict]) -> None:
        print(f"\n【{name}】共 {len(items)} 个")
        for it in items[:15]:
            desc = []
            if it["text"]:
                desc.append(f"文本={it['text']!r}")
            if it["ph"]:
                desc.append(f"placeholder={it['ph']!r}")
            if it["title"]:
                desc.append(f"title={it['title']!r}")
            if it["type"]:
                desc.append(f"type={it['type']}")
            print(f"  · {it['sel']}")
            if desc:
                print(f"      {' | '.join(desc)}")

    show("输入框（找「搜索框」）", d["inputs"])
    show("下拉框（找「账期/分群/存增量」）", d["selects"])
    show("按钮（找「搜索/输出/导出」）", d["buttons"])
    show("菜单链接（找「报表中心」）", d["menus"])
    show("表格/列表容器", d["tables"])

    print()
    print("=" * 70)
    print("下一步：把上面合适的 CSS 选择器填入")
    print("  data/config/cdap_selectors.json")
    print("=" * 70)

    sel = load_selectors()
    print("\n当前配置：")
    for k, v in sel.items():
        if k.startswith("_"):
            continue
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
