"""政企存量平台 · 核心配置"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent   # 项目根（platform/）
DATA_DIR = Path(os.environ.get("ZQC_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()

# 数据目录（沿用业务约定：1-源数据 / 2-输出物）
INBOX_DIR = DATA_DIR / "inbox"          # 源数据入口
OUTPUT_DIR = DATA_DIR / "outputs"       # 成品出口
TEMPLATE_DIR = DATA_DIR / "templates"   # 模板
LOG_DIR = DATA_DIR / "logs"             # 日志
CONFIG_DIR = DATA_DIR / "config"        # 运行配置
STATE_DIR = DATA_DIR / "state"          # 状态（任务、台账、记忆）
TRASH_DIR = DATA_DIR / "trash"          # 可撤销删除

for _d in (INBOX_DIR, OUTPUT_DIR, TEMPLATE_DIR, LOG_DIR, CONFIG_DIR, STATE_DIR, TRASH_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 服务
HOST = os.environ.get("ZQC_HOST", "127.0.0.1")
PORT = int(os.environ.get("ZQC_PORT", "8848"))
DEBUG = os.environ.get("ZQC_DEBUG", "0") == "1"

# 访问鉴权（HTTP Basic）
# 同时设置 ZQC_AUTH_USER 与 ZQC_AUTH_PASS 才启用；任一为空则鉴权关闭（本地开发不受影响）。
AUTH_USER = os.environ.get("ZQC_AUTH_USER", "").strip()
AUTH_PASS = os.environ.get("ZQC_AUTH_PASS", "")
AUTH_ENABLED = bool(AUTH_USER and AUTH_PASS)
# 放行路径（探针等不可被鉴权拦截）
AUTH_WHITELIST = {"/healthz"}

# 日志
LOG_LEVEL = os.environ.get("ZQC_LOG_LEVEL", "INFO")
LOG_FILE = LOG_DIR / "platform.log"

# 广东 21 地市（用于派单「地市抽取」派生列）
CITIES = [
    "深圳", "广州", "珠海", "东莞", "佛山", "惠州", "江门", "中山",
    "肇庆", "汕头", "阳江", "湛江", "揭阳", "河源", "云浮", "梅州",
    "韶关", "茂名", "清远", "潮州", "汕尾",
]

# 地市特殊排序：茂名需排在湛江上面（业务规则）
CITY_ORDER_OVERRIDE = {"茂名": "湛江"}

# 样例/参照物目录（资料包提供的规则参照文件，**不是**待处理的数据源）
REFERENCE_DIR = DATA_DIR / "reference"
REFERENCE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 账期（批次）

_BATCH_FILE = STATE_DIR / "batch.txt"


def get_batch() -> str:
    """当前账期/批次（如 202608）

    用于判断"这批源数据"是不是当前要处理的批次。
    未设置时返回上一工作日所在月份。
    """
    if _BATCH_FILE.exists():
        v = _BATCH_FILE.read_text(encoding="utf-8").strip()
        if v:
            return v
    # 默认：当前月份 YYYYMM
    from datetime import date
    today = date.today()
    return f"{today.year}{today.month:02d}"


def set_batch(value: str) -> str:
    """设置当前账期（YYYYMM 或 YYYYMMDD）"""
    value = (value or "").strip()
    _BATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BATCH_FILE.write_text(value, encoding="utf-8")
    return value
