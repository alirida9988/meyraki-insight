"""Review finding #4 (2026-08-06): .env parser must handle dotenv syntaxes."""

import os

from app.settings import load_env_file


def test_env_parser_quotes_export_and_precedence(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'export MEYRAKI_T1="quoted value"\n'
        "MEYRAKI_T2='single'\n"
        "MEYRAKI_T3=plain=with=equals\n"
        "# comment line\n"
        "MEYRAKI_T4=  spaced  \n"
    )
    os.environ["MEYRAKI_T4"] = "real-env-wins"
    try:
        load_env_file(env)
        assert os.environ["MEYRAKI_T1"] == "quoted value"
        assert os.environ["MEYRAKI_T2"] == "single"
        assert os.environ["MEYRAKI_T3"] == "plain=with=equals"
        assert os.environ["MEYRAKI_T4"] == "real-env-wins"
    finally:
        for k in ("MEYRAKI_T1", "MEYRAKI_T2", "MEYRAKI_T3", "MEYRAKI_T4"):
            os.environ.pop(k, None)


def test_a_cors_wildcard_is_refused_at_startup():
    """Sessions are cookie-based, so CORS runs with allow_credentials=True — a wildcard
    origin lets any site read a signed-in studio's analyses and report PDFs.

    Reloads the settings module, so it restores the original environment and reloads
    again itself: leaving WEB_ORIGINS rewritten would quietly change what every later
    test in the session considers an allowed origin.
    """
    import importlib
    import os

    import pytest

    from app import settings as settings_mod

    original = os.environ.get("MEYRAKI_WEB_ORIGINS")
    try:
        os.environ["MEYRAKI_WEB_ORIGINS"] = "https://app.meyraki.com,*"
        with pytest.raises(RuntimeError, match="wildcard origin"):
            importlib.reload(settings_mod)

        os.environ["MEYRAKI_WEB_ORIGINS"] = "https://app.meyraki.com"
        importlib.reload(settings_mod)
        assert settings_mod.WEB_ORIGINS == ["https://app.meyraki.com"]
    finally:
        if original is None:
            os.environ.pop("MEYRAKI_WEB_ORIGINS", None)
        else:
            os.environ["MEYRAKI_WEB_ORIGINS"] = original
        importlib.reload(settings_mod)
