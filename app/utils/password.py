"""密码哈希工具（全项目通用）

用 bcrypt 做单向哈希：数据库里永远只存哈希，不存明文，也不存可逆加密。

为什么所有函数都要管长度？
    bcrypt 只处理前 72 字节，超过会直接抛 ValueError。用户模块的数据模型会先用
    字节长度校验挡住超长密码，这里的兜底是防止其它调用方绕过模型直接调用：
        * hash_password 超长 -> 抛 ValueError（明确是调用方的参数问题）
        * verify_password 超长 / 库里存的不是合法 bcrypt 哈希 -> 返回 False，
          把「密码错误」变成 401，而不是抛异常变成 500
"""

import bcrypt

# bcrypt 的硬限制：只取前 72 字节
MAX_PASSWORD_BYTES = 72


def is_password_too_long(password: str) -> bool:
    """密码是否超出 bcrypt 的 72 字节限制

    注意是「字节」不是「字符」：中文密码 24 个字符就已经 72 字节了。
    """
    return len(password.encode("utf-8")) > MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    """把明文密码哈希成可入库的字符串（bcrypt 每次加盐结果都不同）"""
    if is_password_too_long(password):
        raise ValueError(f"密码不能超过 {MAX_PASSWORD_BYTES} 字节")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    """校验明文密码与库里的哈希是否匹配

    任何非法输入（空密码、超长密码、非法哈希）一律返回 False，绝不抛异常。
    """
    if not password or not hashed_password or is_password_too_long(password):
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        return False


def is_same_password(new_password: str, hashed_password: str) -> bool:
    """判断新密码是否与原密码相同

    不能比较哈希字符串（bcrypt 每次加盐都不同），必须用明文重新校验。
    """
    return verify_password(new_password, hashed_password)
