"""密码哈希与令牌工具（标准库实现，不引第三方依赖）。

哈希用 scrypt：内存硬，抗 GPU/ASIC 暴力破解。参数与盐都编进存储串，
因此将来调参不会让旧密码失效——校验时按串里记的参数重算即可。
"""

import base64
import hashlib
import hmac
import secrets

# scrypt 参数：128 * n * r = 16 MiB 内存开销
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
# 必须显式给 maxmem：OpenSSL 默认上限（32 MiB）在部分平台会直接报错
_MAXMEM = 64 * 1024 * 1024

_ALGO = "scrypt"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(password: str) -> str:
    """返回自描述哈希串：scrypt$n$r$p$<salt_b64>$<hash_b64>。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DKLEN,
        maxmem=_MAXMEM,
    )
    return f"{_ALGO}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """按存储串里记的参数重算并定长比较；格式非法一律返回 False。"""
    try:
        algo, n_s, r_s, p_s, salt_s, hash_s = stored.split("$")
        if algo != _ALGO:
            return False
        expected = _unb64(hash_s)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_s),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    """不透明令牌明文，只在签发那一刻出现一次。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """令牌入库前一律过这里（设备 token 与登录令牌共用）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
