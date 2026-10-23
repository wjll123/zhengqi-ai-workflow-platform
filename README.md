# 政企存量数据处理平台

一个把「周报 / 月报 / 白皮书」的**取数算数做表**流程自动化的内部工具。

**16 个数据处理任务 · 离线派单工作台**

本仓库只发布程序代码和文档。客户级源表、模板、成品、日志与运行记录均被 `.gitignore` 排除，不应提交到公开仓库。

Web 界面新增「派单工作台」：创建双线质差直销客户级或政企用户号码级派单方案、核对必做动作、下载操作卡，并登记智慧中台实际执行结果。使用范围与联调前提见 [派单落地说明](派单落地说明.md)。

---

## 快速开始

```bash
# 1. 建环境（只需一次）
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 2. 看有哪些任务
./.venv/bin/python3 -m app.cli list

# 3. 跑全部任务
./.venv/bin/python3 -m app.cli run-all

# 4. 成品在 data/outputs/
```

### 发布到 GitHub / Render

公开仓库：<https://github.com/wjll123/zhengqi-ai-workflow-platform>

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/wjll123/zhengqi-ai-workflow-platform)

仓库带有 `render.yaml`，在 Render 中选择 **New → Blueprint** 并连接本仓库即可创建 Web 服务。Render 会使用 `gunicorn` 启动 Flask 应用，健康检查地址为 `/healthz`。也可以在 GitHub 仓库的 **Settings → Pages** 之外使用 Render，因为本项目需要运行 Python 后端。

公开部署使用临时工作目录（`ZQC_DATA_DIR=/tmp/zqc-data`），重启或实例回收后上传的文件会消失。公开链接上的文件列表和计算接口是共享的，不能上传客户身份证号、收入明细等敏感数据；需要多人正式使用时，应在 Render 前增加登录认证，并为每个用户分配独立工作目录。

月报任务 `monthly_4plus2` 使用 LibreOffice headless + openpyxl 进行离线公式计算，不依赖 `@oai/artifact-tool` 或网络。离线交付包会同时带上 Python wheel 缓存和 LibreOffice 运行时；安装后可在无网络环境运行。公开 Render 服务仍可运行其余 Python 任务；在线运行月报需要部署方提供 LibreOffice 运行时。

本地启动：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
ZQC_HOST=127.0.0.1 ./.venv/bin/python3 -m app.web_app
```

### Git 发布与离线交付

Git 仓库只包含程序代码、测试和文档。客户源表、生成结果、日志、运行状态、虚拟环境和本机运行时均被排除，不应提交到仓库。

```bash
git status --short
git add .
git commit -m "release: offline-ready platform"
git push origin main
```

Docker / Render 使用 `Dockerfile` 或 `render.yaml` 启动。上线时必须设置 `ZQC_AUTH_USER` 和 `ZQC_AUTH_PASS`，并为 `ZQC_DATA_DIR` 配置持久化磁盘。企查查查询需要平台与已登录浏览器运行在同一台 Mac：优先支持 Chrome/Edge 调试模式，也可使用 Safari。云端服务不能接管用户电脑上的浏览器。

Chrome 调试模式：在 Mac 终端执行 `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$HOME/.zqc-chrome-debug"`。Edge 将程序路径改为 `/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge`、端口改为 `9223`、配置目录改为 `$HOME/.zqc-edge-debug`。在新打开的调试窗口登录企查查、打开 `https://www.qcc.com/`，回到平台点击“检测浏览器连接”。调试配置目录独立于日常浏览器，可能需要重新登录；日常窗口无需关闭。

构建 macOS 离线包（需要本机已有 `runtime/LibreOfficeDev.app` 和 `政企ai工作流平台/wheels/`）：

```bash
./scripts/build_offline_package.sh
```

生成的 ZIP 不含客户数据；目标 Mac 解压后运行 `./install_offline.sh`，再运行 `./run_offline.sh`。安装过程使用包内 wheel，不访问网络。

GitHub 发布前请确认没有把 `data/`、客户文件或本机配置加入暂存区：

```bash
git status --short
git check-ignore -v data/inbox/任意文件.xlsx
```

或直接双击 **`一键运行.command`**（macOS）。

---

## 使用流程

```
① 源数据放 data/inbox/（或从界面上传）
② 点「执行」
③ 成品在 data/outputs/
```

浏览器界面：

```bash
./start.sh          # 或双击 启动平台.command
# 访问 http://127.0.0.1:8848
```

---

## 三个概念必须先搞清

### 1. 参照物 ≠ 数据源

| 类别 | 位置 | 作用 |
|---|---|---|
| **参照物 / 样例** | `data/reference/` | 只用于理解规则，**不是**要处理的数据 |
| **待处理数据源** | `data/inbox/` | **每次要处理的新数据** |

**平台本身不带数据。** 每次生成新成果，都要放入**本批次的新数据源**。

### 2. 账期（批次）

平台有「当前账期」概念（界面顶部可设，如 `202608`）。

需要按文件名核对账期的任务，源数据**文件名需含该账期**，才会被识别为当前批次。**落责数据处理**任务不要求文件名带账期；它会在执行时检查工作表、表头、地市、分级和必需数值，统计期需由使用者确认。

| 文件名 | 是否被认 |
|---|---|
| `841营销活动管控表.xlsx` | ✗ 不含账期 |
| `841营销活动管控表-202608.xlsx` | ✓ 认 |

### 3. 三道关卡

| 关卡 | 拦什么 |
|---|---|
| **源预检**（执行前）| 文件不存在 / 不是本批次 |
| **格式层** | GBK、BIFF .xls、尾部制表符（I/O 层统一处理）|
| **内容层**（加工时）| 空模板、字段缺失、匹配率低 |

**宁可报错，也不生成静默错误的表。**

---

## 任务清单（16 个）

### 客户落责月度工作流对比

进入「数据分析 → 客户落责月度工作流对比」，上传并选择两份月度来源表、落责材料数据处理工作簿和三页客户落责月报。平台按 7 步核对来源搬运、三块 PPT 材料区及月报表格，生成可下载的逐格差异报告。四份文件的统计期需由使用者确认；平台不会修改原文件或自动改写月报页首总结。详情见 [客户落责月度工作流](docs/客户落责月度工作流.md)。

### 政企存量 4+2 收客保月报

进入「数据分析 → 政企存量 4+2 收客保月报」，设置账期（如 `202609`），选择同账期四份 CDAP 原样导出，以及《收保率对标分析表_模板》和《指标跟踪_模板表》两份计算模板。平台已预置两份计算模板，也可上传新版。指标跟踪成表从平台保存的上月完整历史表续写，保留前几年及前几个月的数据和格式；平台预置的历史底稿截至 2026 年 8 月，无需手动提供。缺少连续月份历史底稿时任务报错，不生成只有当月的简易表。五项地市环比从上月同地市数据计算。收保率对标表和通报表按当前源数据及模板生成，不要求同名上月成表。成果为三张工作簿和一份通报文案，可在 `data/outputs/` 下载。任务会核对全省及 21 地市、纯净收入公式、五项环比和通报表与指标跟踪表的数据一致性。

本任务用 LibreOffice 重算两张计算模板，再用 openpyxl 续写历史成果。若环境中的 `soffice` 不在 PATH，可设置 `ZQC_SOFFICE` 指向可执行文件。按地市纯净收入的剔除项沿用计算模板中现有数值，正式使用前须核对该数值是否属于本账期。

### 系统导出 · 直接加工（9 个）

| 任务 | 产出 |
|---|---|
| 841营销活动管控表加工 | `1-接单情况模板.xlsx` → sheet「841营销活动管控表」|
| 智慧中台调度工单查询加工 | 同上 → sheet「智慧中台调度工单查询」|
| 智慧中台活动评估搬运 | 同上 → sheet「智慧中台活动评估」|
| 智慧中台接触工单查询加工 | 同上 → sheet「智慧中台接触工单查询」|
| **周报加工（四张表一键）** | 上述 4 张表一次生成 |
| **政企存量 4+2 收客保月报** | 三张月报工作簿 + 一份通报文案 |
| 白皮书 2.11 重点业务续约率 | `政企码号台阶白皮书202608.xlsx` → sheet「2.11」|
| 生成模板（人工供数用）| `模板-*.xlsx` |
| 大政企月度经营通报 PPT P11–14 | PPT 四页表格更新 |

### 需跑 SQL · 结果上传（4 个）

| 任务 | 产出 |
|---|---|
| 白皮书 1.6 单位证件收保率 | `政企码号台阶白皮书202608.xlsx` → sheet「1.6」|
| 商企续约数据处理 | `月报-商企续约数据.xlsx` |
| **落责数据处理** | `月报-客户落责数据处理.xlsx`（含 3 张 PPT 材料表）|
| 通报文案生成 | `通报文案-X月.txt` |

### 人工填报 · 模板上传（2 个）

| 任务 | 产出 |
|---|---|
| 每周活动汇总 | `2-每周活动汇总.xlsx` |
| 3.5 潜客转化 | 白皮书 sheet「3.5」|

---

## 命令

```bash
./.venv/bin/python3 -m app.cli list       # 列出任务
./.venv/bin/python3 -m app.cli run <key>  # 执行单个
./.venv/bin/python3 -m app.cli run-all    # 执行全部
./.venv/bin/python3 -m app.cli runs       # 运行记录
./.venv/bin/python3 -m app.cli health     # 平台自检
```

自检比对工具（拿人工成品逐格验证）：

```bash
./.venv/bin/python3 -m app.tools.verify --ref <人工成品路径>
```

---

## 目录结构

```
app/
├── core/               基础设施
│   ├── config.py       配置（路径/端口/地市清单/账期）
│   ├── logger.py       日志
│   ├── tables.py       表格 I/O（xlsx / GBK csv / BIFF xls）
│   ├── rules.py        业务规则（地市抽取/排序/比率公式）
│   ├── runner.py       任务注册表 + 运行记录 + 源预检
│   ├── health.py       健康自检
│   └── browser.py      CDP 浏览器接管（默认不加载）
├── modules/            业务任务
├── tools/              工具（verify / probe_cdap）
├── web/templates/      5 个页面
├── web_app.py          Web 入口
└── cli.py              命令行入口

data/
├── inbox/       ← 放源数据
├── outputs/     ← 取成品
├── templates/   ← 空白模板
├── reference/   ← 参照样例
├── logs/        ← 日志
├── state/       ← 状态（账期）
└── config/      ← 运行配置
```

---

## 技术栈

```
Flask 3.1.3        Web
openpyxl 3.1.5     .xlsx 读写
xlrd 2.0.2         老式 .xls（BIFF）
python-pptx 1.0.2  PPT
```

**刻意不用**：pandas（体积大、内网装麻烦）、Selenium/Playwright（要下浏览器内核）。

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ZQC_HOST` | 127.0.0.1 | 监听地址（多人访问设 `0.0.0.0`）|
| `ZQC_PORT` | 8848 | 端口 |
| `ZQC_LOG_LEVEL` | INFO | 日志级别 |
| `ZQC_ENABLE_RPA` | 0 | 设为 1 启用 CDAP 自动导出 |
| `ZQC_SOFFICE` | LibreOffice `soffice` 路径 | 4+2 月报的模板计算运行时 |

---

## 已知限制

| 限制 | 说明 |
|---|---|
| **需人工准备数据** | 内网取数需登录（短信验证码），一期不做 RPA |
| **部分源是空模板** | 业务方给的"源文件"可能只有表头 → 有空模板检测告警 |
| **RPA 选择器需校准** | CDAP 页面结构未知，代码保留但默认关闭 |
