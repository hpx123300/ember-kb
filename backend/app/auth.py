"""身份与权限：账号、密码哈希、JWT、部门可见范围。

为什么部门要在检索层做硬过滤，而不是在路由层判断：

路由层只能回答「这个人能不能调这个接口」，回答不了「这次检索里有没有他
不该看的段落」。同一个 /api/ask，销售同事问到「客服的处理口径」，如果只
靠接口鉴权放行，检索照样会把客服文档的片段召回并写进回答——这是越权，
而且是看不出来的越权，因为回答本身完全正常。

所以权限要下沉到「候选片段」这一层：先按部门砍掉不该看的块，再打分。
"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header, HTTPException

from .config import (
    DEFAULT_ADMIN,
    DEFAULT_ADMIN_PASSWORD,
    JWT_ALGORITHM,
    JWT_SECRET,
    TOKEN_TTL_HOURS,
)
from .db import User, get_session

PBKDF2_ROUNDS = 210_000

# 面向所有人的资料：入职指南对每个新人都成立，所以「新员工」等同全员可见。
UNIVERSAL_AUDIENCES = {"全员", "新员工"}


class AuthError(HTTPException):
    def __init__(self, detail: str = "登录已失效，请重新登录"):
        super().__init__(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256，自带盐与轮数，格式自描述，换参数也能校验老密码。"""
    if not password or len(password) < 6:
        raise ValueError("密码至少 6 位")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_b64, digest_b64 = (stored or "").split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
    except (ValueError, TypeError):
        return False
    # 定长比较，避免按字节比较泄露时序信息
    return hmac.compare_digest(actual, expected)


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "name": user.name,
        "department": user.department,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


class CurrentUser:
    """请求上下文里的用户。检索、学情、审计都用它，不直接信前端传的身份。"""

    def __init__(self, id: int, username: str, name: str, department: str, role: str):
        self.id = id
        self.username = username
        self.name = name
        self.department = department
        self.role = role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def employee_key(self) -> str:
        """写进 learn_records.employee 的键：用 username 而不是姓名，重名不会串数据。"""
        return self.username

    def allowed_audiences(self) -> set[str] | None:
        """能看的资料受众范围。None 表示不过滤（管理员）。"""
        if self.is_admin:
            return None
        return UNIVERSAL_AUDIENCES | {self.department}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "department": self.department,
            "role": self.role,
            "audiences": sorted(self.allowed_audiences() or ["*"]),
        }


def load_user(user_id: int) -> CurrentUser | None:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            return None
        return CurrentUser(user.id, user.username, user.name, user.department, user.role)


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    """从 Authorization: Bearer <token> 解析当前用户。

    只信签名里的内容，不读请求体里的 user_id——前端传什么都无所谓。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("缺少登录凭证")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("凭证无效") from exc
    try:
        user = load_user(int(payload.get("sub", 0)))
    except (TypeError, ValueError) as exc:
        raise AuthError("凭证无效") from exc
    if user is None:
        raise AuthError("账号不存在或已停用")
    return user


def require_admin(user: CurrentUser) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


DEMO_USERS = [
    # (username, password, name, department, role)
    (DEFAULT_ADMIN, DEFAULT_ADMIN_PASSWORD, "系统管理员", "全员", "admin"),
    ("sales", "sales12345", "陈曦", "销售", "employee"),
    ("support", "support12345", "林悦", "客服", "employee"),
    ("tech", "tech12345", "周航", "技术", "employee"),
]


def seed_users() -> None:
    """首次启动创建演示账号。已存在同名用户则跳过，不会覆盖改过的密码。"""
    with get_session() as session:
        existing = {u.username for u in session.query(User).all()}
        for username, password, name, department, role in DEMO_USERS:
            if username in existing:
                continue
            session.add(
                User(
                    username=username,
                    name=name,
                    department=department,
                    role=role,
                    password_hash=hash_password(password),
                )
            )
        session.commit()
