"""인증 서비스."""
from .utils import make_token, log


class BaseService:
    def audit(self, message):
        log(f"[audit] {message}")


class AuthService(BaseService):
    """사용자 인증을 담당하는 서비스."""

    def login(self, user, password):
        """사용자 로그인을 처리하고 토큰을 발급한다."""
        if not self._check(user, password):
            raise PermissionError(user)
        self.audit("login " + user)
        return make_token(user)

    def _check(self, user, password):
        return bool(user) and len(password) >= 6

    @property
    def name(self):
        return "auth"

    @name.setter
    def name(self, value):
        self._name = value
