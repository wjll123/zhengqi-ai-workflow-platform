"""模块加载：导入即注册

⚠️ 当前策略（2026-10-09 起）：
    **暂不做 RPA（浏览器自动导出）**，专注「人工导出数据源 → 平台生成成品」。

    data_cdap（浏览器接管 / CDAP 自动导出）已从默认加载中移除，
    代码保留，待后续需要时再启用：把 data_cdap 加回下面的导入列表即可。
"""
from __future__ import annotations

import os


def load_all() -> None:
    from . import (  # noqa: F401
        data_weekly,
        data_whitepaper,
        data_wp16,
        data_syq,
        data_luozhe,
        luozhe_audit,
        data_manual,
        data_weekly_summary,
        material_output,
        material_monthly_ppt,
        monthly_4plus2,
        qcc_company,
    )

    # RPA 模块：默认不加载，设 ZQC_ENABLE_RPA=1 可启用
    if os.environ.get("ZQC_ENABLE_RPA") == "1":
        from . import data_cdap  # noqa: F401
