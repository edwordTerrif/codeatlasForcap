from app.service import AuthService


def test_login():
    assert AuthService().login("alice", "secret")
