"""按库启用的用户、角色和表授权；口令与 SQL 展示链分离。"""

import hashlib
import hmac
import os

from sql_compiler.ast_nodes import ControlStmt, CreateTableStmt, InsertStmt, UpdateStmt, DeleteStmt
from sql_compiler.catalog import _valid_name
from sql_compiler.errors import ExecuteError


def fail():
    raise ExecuteError("[AUTH] 身份失效或没有操作权限")


def password_record(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 1024:
        raise ExecuteError("密码长度必须为 8 至 1024 个字符")
    salt = os.urandom(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
    return {"salt": salt.hex(), "digest": value.hex(), "iterations": 600000, "version": 1}


def authenticate(session, username, password):
    security = session.storage.meta["security"]
    if security is None:
        session.username = None
        return
    key = username.lower() if isinstance(username, str) else ""
    user = security["users"].get(key)
    record = user["password"] if user else {"salt": "00" * 16, "iterations": 600000, "digest": "00" * 32}
    if not isinstance(password, str) or len(password) > 1024:
        fail()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(record["salt"]), record["iterations"])
    valid = hmac.compare_digest(digest.hex(), record["digest"])
    if not valid or user is None:
        fail()
    session.username = key
    session.credential_version = record["version"]


def validate_identity(session):
    security = session.storage.meta["security"]
    if security is None:
        return
    user = security["users"].get(session.username)
    if user is None or user["password"]["version"] != session.credential_version:
        fail()


def administrator(session):
    security = session.storage.meta["security"]
    return security is None or bool(security["users"].get(session.username, {}).get("admin"))


def allowed(session, table, permission):
    if administrator(session):
        return True
    security = session.storage.meta["security"]
    entry = session.storage.meta["tables"].get(table.lower()) if table else None
    if entry and entry["owner"] == session.username:
        return True
    user = security["users"][session.username]
    return any([role, table.lower() if table else "*", permission] in security["grants"]
               for role in user["roles"])


def authorize(session, stmt):
    validate_identity(session)
    if isinstance(stmt, ControlStmt):
        if stmt.action == "Explain":
            return authorize(session, stmt.statement)
        if stmt.action in {"Begin", "Commit", "Rollback"}:
            return
        if stmt.action in {"CreateIndex", "DropIndex", "Analyze"}:
            table = stmt.table
            if stmt.action == "DropIndex":
                spec = session.storage.meta["indexes"].get(stmt.name.lower())
                table = spec["table"] if spec else None
            if not administrator(session) and (table is None or session.storage.meta["tables"].get(table.lower(), {}).get("owner") != session.username):
                fail()
            return
        if not administrator(session):
            fail()
        return
    if isinstance(stmt, CreateTableStmt):
        if not allowed(session, None, "CREATE"):
            fail()
        return
    permission = "INSERT" if isinstance(stmt, InsertStmt) else "UPDATE" if isinstance(stmt, UpdateStmt) else "DELETE" if isinstance(stmt, DeleteStmt) else "SELECT"
    if not allowed(session, stmt.table, permission):
        fail()
    for join in getattr(stmt, "joins", []):
        if not allowed(session, join.table, "SELECT"):
            fail()


def visible_tables(session):
    return {table for table in session.storage.meta["tables"]
            if any(allowed(session, table, p) for p in ("SELECT", "INSERT", "UPDATE", "DELETE"))}


def initialize(session, username, password):
    if session.state != "idle":
        raise ExecuteError("账号初始化必须在显式事务之外执行")
    with session.transaction():
        if session.storage.meta["security"] is not None:
            raise ExecuteError("认证已经开启")
        if not _valid_name(username):
            raise ExecuteError("用户名非法")
        record = password_record(password)
        key = username.lower()
        session.storage.meta["security"] = {"users": {key: {"admin": True, "roles": [], "password": record}},
                                            "roles": [], "grants": []}
        for table in session.storage.meta["tables"].values():
            table["owner"] = key
    session.username, session.credential_version = key, record["version"]


def manage_user(session, action, username, password=None):
    if session.state != "idle":
        raise ExecuteError("账号管理必须在显式事务之外执行")
    with session.transaction():
        security = session.storage.meta["security"]
        if security is None or not administrator(session):
            fail()
        if not _valid_name(username):
            raise ExecuteError("用户名非法")
        key = username.lower()
        users = security["users"]
        if action == "create":
            if key in users:
                raise ExecuteError("用户已存在")
            users[key] = {"admin": False, "roles": [], "password": password_record(password)}
        elif action == "password":
            if key not in users:
                raise ExecuteError("用户不存在")
            record = password_record(password)
            record["version"] = users[key]["password"]["version"] + 1
            users[key]["password"] = record
        elif action == "drop":
            if key not in users or users[key]["admin"]:
                raise ExecuteError("用户不存在或为管理员")
            del users[key]
        else:
            raise ExecuteError("不支持的用户管理操作")
    if action == "password" and key == session.username:
        session.credential_version = record["version"]


def role_command(plan, session):
    security = session.storage.meta["security"]
    if security is None:
        raise ExecuteError("请先通过管理工具初始化账号权限")
    if not administrator(session):
        fail()
    action = plan["op"]
    name = (plan.get("name") or "").lower()
    subject = (plan.get("subject") or "").lower()
    if action == "CreateRole":
        if not _valid_name(name) or name in security["roles"]:
            raise ExecuteError("角色名称非法或已存在")
        security["roles"].append(name)
    elif action == "DropRole":
        if name not in security["roles"]:
            raise ExecuteError("角色不存在")
        security["roles"].remove(name)
        security["grants"] = [g for g in security["grants"] if g[0] != name]
        for user in security["users"].values():
            user["roles"] = [r for r in user["roles"] if r != name]
    elif not plan["permissions"]:
        if name not in security["roles"] or subject not in security["users"]:
            raise ExecuteError("角色或用户不存在")
        roles = security["users"][subject]["roles"]
        if action == "Grant" and name not in roles:
            roles.append(name)
        elif action == "Revoke" and name in roles:
            roles.remove(name)
    else:
        if subject not in security["roles"]:
            raise ExecuteError("目标角色不存在")
        table = plan["table"].lower() if plan["table"] else "*"
        for permission in plan["permissions"]:
            if (table == "*") != (permission == "CREATE"):
                raise ExecuteError("数据库仅支持 CREATE 权限，表支持 SELECT/INSERT/UPDATE/DELETE")
            grant = [subject, table, permission]
            if action == "Grant" and grant not in security["grants"]:
                security["grants"].append(grant)
            elif action == "Revoke" and grant in security["grants"]:
                security["grants"].remove(grant)
