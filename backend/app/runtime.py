"""请求级上下文：当前是谁、能看什么。

工具函数（tools/__init__.py）被模型调用时，拿不到 FastAPI 的 Request，
也不该为了传一个用户名把每个函数签名都改一遍。用 ContextVar 把身份带到
调用栈里，工具自己取——这样工具实现既能在 HTTP 请求里跑，也能在 MCP
或脚本里跑，不需要知道自己被谁调用。

默认值给「本地演示」而不是空：没登录的场景（命令行、MCP、评测）
仍然能跑，但拿不到任何部门专属资料——默认权限要最小，不是最大。
"""

from contextlib import contextmanager
from contextvars import ContextVar

_employee: ContextVar[str] = ContextVar("ember_employee", default="local")
_audiences: ContextVar[set[str] | None] = ContextVar("ember_audiences", default=None)


def current_employee() -> str:
    return _employee.get()


def current_audiences() -> set[str] | None:
    return _audiences.get()


@contextmanager
def user_scope(employee: str = "local", audiences: set[str] | None = None):
    token_emp = _employee.set(employee or "local")
    token_aud = _audiences.set(audiences)
    try:
        yield
    finally:
        _employee.reset(token_emp)
        _audiences.reset(token_aud)
