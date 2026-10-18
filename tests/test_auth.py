"""身份、令牌与部门隔离测试。

这一组测的不是「登录接口能不能用」，而是「权限到底有没有落到检索层」——
后者才是内部系统真正会出事的地方。
"""

import pytest
from fastapi import HTTPException

from backend.app import auth
from backend.app.agent.rag import knowledge_base

SALES = auth.CurrentUser(2, "sales", "陈曦", "销售", "employee")
SUPPORT = auth.CurrentUser(3, "support", "林悦", "客服", "employee")
ADMIN = auth.CurrentUser(1, "admin", "管理员", "全员", "admin")


def test_password_hash_roundtrip():
    stored = auth.hash_password("sales12345")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("sales12345", stored)
    assert not auth.verify_password("wrong-password", stored)
    # 同一个密码两次哈希结果不同（盐不同），但都能验证通过
    assert stored != auth.hash_password("sales12345")


def test_password_rejects_garbage():
    assert not auth.verify_password("x", "")
    assert not auth.verify_password("x", "not-a-hash")
    with pytest.raises(ValueError):
        auth.hash_password("123")


def test_token_roundtrip_carries_department():
    user_model = type("U", (), {"id": 2, "username": "sales", "name": "陈曦", "department": "销售", "role": "employee"})
    token = auth.create_token(user_model)
    payload = auth.decode_token(token)
    assert payload["username"] == "sales"
    assert payload["department"] == "销售"


def test_tampered_token_is_rejected():
    token = auth.create_token(type("U", (), {"id": 2, "username": "sales", "name": "x", "department": "销售", "role": "employee"}))
    bad = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(Exception):
        auth.decode_token(bad)


def test_admin_sees_everything():
    assert ADMIN.allowed_audiences() is None


def test_employee_scope_is_department_plus_universal():
    assert SALES.allowed_audiences() == {"全员", "新员工", "销售"}
    assert SUPPORT.allowed_audiences() == {"全员", "新员工", "客服"}


def test_sales_cannot_retrieve_support_docs():
    """核心断言：销售问客服口径，检索层就不该把客服文档召回。

    只在接口层拦是拦不住的——同一个 /api/ask，答案照样会被拼出来。
    """
    results = knowledge_base.search("客户投诉怎么处理，处理口径是什么", k=6, audiences=SALES.allowed_audiences())
    assert results, "应该仍能召回销售/制度类资料"
    assert all(r["source"] != "support-faq.md" for r in results)


def test_support_can_retrieve_support_docs():
    results = knowledge_base.search("客户投诉怎么处理，处理口径是什么", k=6, audiences=SUPPORT.allowed_audiences())
    assert any(r["source"] == "support-faq.md" for r in results)


def test_admin_retrieval_not_filtered():
    results = knowledge_base.search("客户投诉怎么处理，处理口径是什么", k=8, audiences=None)
    assert any(r["source"] == "support-faq.md" for r in results)


def test_require_admin_blocks_employee():
    assert auth.require_admin(ADMIN) is ADMIN
    with pytest.raises(HTTPException) as exc:
        auth.require_admin(SALES)
    assert exc.value.status_code == 403


def test_get_current_user_rejects_missing_header():
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(None)
    assert exc.value.status_code == 401
