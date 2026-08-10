"""A percent-encoded database password crashed Alembic before it opened a connection.

Found 2026-08-10 pointing the stack at a real managed Postgres whose generated password
contained `@`. A `@` in a password MUST be written `%40` in a URL — unencoded it
terminates the userinfo section and the host is parsed wrong — so the working DSN
necessarily contains a `%`.

`migrations/env.py` hands that DSN to `config.set_main_option`, which writes into a
ConfigParser, and ConfigParser reads a lone `%` as interpolation syntax:

    ValueError: invalid interpolation syntax in 'postgresql+psycopg://...%40...'

The severity is in *where* it fires. The API image runs `alembic upgrade head` before
uvicorn, so this is not a developer inconvenience — the container exits on boot, on the
first deploy against any provider that generates a password with a punctuation character.
Roughly half of generated passwords contain one.

Escaping to `%%` is the documented way to put a literal percent through ConfigParser, and
the value read back out is the original.
"""

import pytest
from alembic.config import Config


def _url(password: str) -> str:
    return f"postgresql+psycopg://user:{password}@host.example.com:5432/db"


def test_a_percent_encoded_password_survives_alembic_config():
    """The regression: the escaped value must round-trip to the ORIGINAL url."""
    url = _url("pa%40ssword")  # a password containing '@', correctly encoded
    config = Config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url


def test_the_unescaped_form_is_what_actually_broke():
    """Proves the escaping is load-bearing rather than decorative — without it, this is
    the exact ValueError seen against the managed database."""
    config = Config()
    with pytest.raises(ValueError, match="interpolation"):
        config.set_main_option("sqlalchemy.url", _url("pa%40ssword"))


@pytest.mark.parametrize(
    "password",
    [
        "pa%40ssword",    # @  — terminates userinfo if left raw
        "pa%2Fssword",    # /  — starts the path if left raw
        "pa%3Assword",    # :  — splits password from host if left raw
        "pa%23ssword",    # #  — starts a fragment if left raw
        "%40%2F%3A%23",   # all of them
        "plain-password",  # and the ordinary case still works
    ],
)
def test_every_character_a_provider_might_generate(password):
    url = _url(password)
    config = Config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url


def test_the_migration_env_actually_applies_the_escaping():
    """Guards the fix at its real site: reading env.py rather than re-implementing it
    here, so deleting the `.replace` fails this test instead of passing quietly."""
    import pathlib

    env = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "env.py"
    source = env.read_text(encoding="utf-8")
    assert 'set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))' in source, (
        "migrations/env.py no longer escapes '%' — a password containing an encoded "
        "character will crash `alembic upgrade head`, which the API image runs on boot"
    )
