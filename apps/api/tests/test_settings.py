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
