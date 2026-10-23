class QueryError(ValueError):
    """输入或企业数据不满足查询要求。"""


class BrowserActionRequired(QueryError):
    """需要用户在浏览器中处理登录、验证码或访问限制。"""


class PageNotReady(QueryError):
    """当前导航还没有加载到目标页面。"""


class CompanyNotFound(QueryError):
    """名称查询没有精确匹配，可使用已提供的信用代码再次核对。"""


class QueryBusy(QueryError):
    """已有任务使用同一浏览器查询队列。"""


class QueryPaused(QueryError):
    """等待下次访问期间收到暂停，不再导航。"""
