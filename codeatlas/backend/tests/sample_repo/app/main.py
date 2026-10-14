"""앱 실행 진입점."""
from app.service import AuthService
from app import utils


class App:
    """서비스를 묶어서 실행하는 애플리케이션 객체."""

    def __init__(self):
        self.auth = AuthService()

    def run(self, user: str, password: str):
        return self.auth.login(user, password)


def main():
    """로그인 흐름을 한 번 실행한다."""
    svc = AuthService()
    token = svc.login("alice", "secret")
    utils.log(f"token={token}")
    App().run("bob", "password123")


if __name__ == "__main__":
    main()
