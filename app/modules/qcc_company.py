"""平台任务目录中的企查查后台查询入口。"""
from ..core.runner import TaskResult, registry
from ..qcc.errors import QueryError
from ..qcc.jobs import jobs


@registry.register(
    key="qcc_company", name="企查查 · 企业批量查询", module="企业信息工具",
    description="最多 100 家企业，复用 Safari、Chrome 或 Edge 登录，逐家保存股东与主要人员资料。",
    params=[{"name": "source_name", "label": "公司名称名单", "required": True},
            {"name": "browser", "label": "查询浏览器", "required": False,
             "default": "chrome", "choices": ["chrome", "edge", "safari"]},
            {"name": "debug_port", "label": "浏览器调试端口", "required": False,
             "default": 9222}],
    source_type="manual", source_note="上传公司全称名单，先在本机浏览器登录企查查；Chrome/Edge 需调试模式。验证中断后可继续查询。",
    outputs_desc="一个 Excel，包含详情页股东字段、主要人物字段、分期出资明细和逐家查询状态。",
)
def qcc_company(source_name="", resume_from="", identity_source="", max_new_queries=100,
                delay_seconds=60, browser="chrome", debug_port=9222):
    try:
        job = jobs.start(dict(source_name=source_name, resume_from=resume_from,
                              identity_source=identity_source, max_new_queries=max_new_queries,
                              delay_seconds=delay_seconds, browser=browser, debug_port=debug_port))
    except QueryError as exc:
        return TaskResult(ok=False, message=str(exc))
    return TaskResult(ok=True, message="后台查询已启动，请在企查查任务页查看进度并下载结果。",
                      metrics={"批次编号": job["id"], "名单企业": job["counts"]["total"]})
