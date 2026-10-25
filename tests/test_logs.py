import io
import logging

from pydantic import SecretStr

from crisp.config import Settings
from crisp.logs import log_startup_config, setup_logging

KEY = "sk-or-v1-supersecret"


def test_api_key_never_logged():
    s = Settings()
    s.openrouter_api_key = SecretStr(KEY)
    buf = io.StringIO()
    setup_logging("DEBUG", s.secrets(), stream=buf)
    log = logging.getLogger("crisp.test")
    log_startup_config(s)
    log.info("calling provider with Authorization: Bearer %s", KEY)
    try:
        raise RuntimeError(f"boom {KEY}")
    except RuntimeError:
        log.exception("request failed")
    out = buf.getvalue()
    assert KEY not in out
    assert "***" in out
    assert "starting with configuration" in out
