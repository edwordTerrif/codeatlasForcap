"""공용 도구 함수."""
import hashlib


def make_token(user):
    """로그인 토큰을 만든다."""
    return hash_text(user)[:16]


def hash_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def log(message):
    print(message)
