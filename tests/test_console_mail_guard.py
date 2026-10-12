"""L-8: cloud + APP_ENV=prod + e-mail `console` nie startuje bez jawnej zgody; w prod link nie trafia do logu."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.control.mailer import ConsoleMailer, OutgoingMail, build_mailer
from app.core.config import Settings
from app.core.startup import validate_startup
from tests.support import assert_private_file

SECRET = "s" * 40


def cloud(**kw) -> Settings:
    base = {"daas_mode": "cloud", "daas_secret_key": SECRET, "app_env": "prod", "daas_email_backend": "console"}
    return Settings(_env_file=None, **{**base, **kw})


def test_prod_cloud_console_mail_is_refused():
    with pytest.raises(RuntimeError, match="DAAS_ALLOW_CONSOLE_MAIL"):
        validate_startup(cloud())


def test_refusal_message_names_the_smtp_alternative():
    with pytest.raises(RuntimeError, match="DAAS_EMAIL_BACKEND=smtp"):
        validate_startup(cloud())


def test_explicit_opt_in_allows_console_mail_in_prod():
    validate_startup(cloud(daas_allow_console_mail=True))


def test_smtp_in_prod_needs_no_opt_in():
    validate_startup(cloud(daas_email_backend="smtp", daas_smtp_host="smtp.example.test"))


@pytest.mark.parametrize("env", ["dev", "test", "staging"])
def test_non_prod_env_keeps_console_mail(env: str):
    validate_startup(cloud(app_env=env))


def test_env_value_is_normalised():
    with pytest.raises(RuntimeError):
        validate_startup(cloud(app_env=" PROD "))


def test_selfhost_is_not_affected():
    validate_startup(Settings(_env_file=None, daas_mode="selfhost", app_env="prod", daas_email_backend="console",
                              daas_api_key="k" * 20))


def test_opt_in_defaults_to_false_and_reads_the_env(monkeypatch: pytest.MonkeyPatch):
    assert Settings(_env_file=None).daas_allow_console_mail is False
    monkeypatch.setenv("DAAS_ALLOW_CONSOLE_MAIL", "true")
    assert Settings(_env_file=None).daas_allow_console_mail is True


def test_create_app_fails_closed_without_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.main import create_app

    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "runtime" / "daas.duckdb"))
    with pytest.raises(RuntimeError, match="DAAS_ALLOW_CONSOLE_MAIL"):
        create_app(cloud())


# ---------------------------------------------------------------- link w logu
MAIL = OutgoingMail(to="jan@example.test", subject="s", body="b", link="https://cockpit.example.test/login/verify?token=SECRETTOKEN")


def test_prod_console_mailer_writes_outbox_but_does_not_log_the_link(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    s = cloud(daas_allow_console_mail=True)
    mailer = build_mailer(s)
    assert isinstance(mailer, ConsoleMailer)
    mailer.outbox_dir = tmp_path / "ob"
    with caplog.at_level(logging.DEBUG):
        mailer.send(MAIL)
    (f,) = (tmp_path / "ob").glob("*.json")
    assert "SECRETTOKEN" in f.read_text()  # outbox zawiera link
    assert "SECRETTOKEN" not in caplog.text and "token=" not in caplog.text
    assert "jan@example.test" not in caplog.text
    assert_private_file(f)


def test_dev_console_mailer_still_logs_the_link(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    mailer = build_mailer(cloud(app_env="dev"))
    mailer.outbox_dir = tmp_path / "ob"
    with caplog.at_level(logging.INFO):
        mailer.send(MAIL)
    assert "SECRETTOKEN" in caplog.text


def test_direct_console_mailer_defaults_to_logging(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.INFO):
        ConsoleMailer(tmp_path / "ob").send(MAIL)
    assert "SECRETTOKEN" in caplog.text
