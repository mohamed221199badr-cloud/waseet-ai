"""Settings guards: unsafe configuration must fail at startup, not at runtime."""

import pytest
from pydantic import ValidationError

from app.config import Settings

PROD = {"app_env": "production", "jwt_secret": "x" * 40}


def make(**kw):
    return Settings(_env_file=None, **kw)


def test_open_mode_allowed_in_dev():
    assert make(app_env="dev", llm_mode="open").llm_mode == "open"


@pytest.mark.parametrize("env", ["production", "staging", "prod", ""])
def test_open_mode_rejected_outside_dev(env):
    with pytest.raises(ValidationError, match="LLM_MODE=open"):
        make(app_env=env, jwt_secret="x" * 40, llm_mode="open")


@pytest.mark.parametrize("mode", ["off", "assist", "chat"])
def test_other_modes_allowed_in_production(mode):
    assert make(**PROD, llm_mode=mode).llm_mode == mode


def test_unknown_mode_rejected():
    with pytest.raises(ValidationError, match="LLM_MODE must be one of"):
        make(llm_mode="OPEN ")


def test_default_is_off():
    assert make().llm_mode == "off"
