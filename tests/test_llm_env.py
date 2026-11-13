"""Claude Code finds its sign-in by user name; a bare GUI environment lacks one."""
from theodore import llm


def test_subscription_env_always_has_user_names(monkeypatch):
    for key in ("USER", "LOGNAME"):
        monkeypatch.delenv(key, raising=False)
    env = llm.subscription_env()
    assert env["USER"] and env["LOGNAME"]
