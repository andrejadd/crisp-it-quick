from pathlib import Path

import pytest

from crisp.config import ConfigError, load_settings

ROOT = Path(__file__).resolve().parents[1]
ENV = {"OPENROUTER_API_KEY": "sk-or-test"}


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return p


def test_example_config_loads():
    s = load_settings(ROOT / "config.example.yaml", ENV)
    assert s.transcription.provider == "openrouter"
    assert s.openrouter_api_key.get_secret_value() == "sk-or-test"


def test_defaults_applied(tmp_path):
    s = load_settings(write(tmp_path, "transcription:\n  model: x/y\n"), ENV)
    assert s.transcription.model == "x/y"
    assert s.transcription.language == "de"
    assert s.ingest.record_folder == "RECORD"
    assert s.device.poll_interval_s == 30
    assert s.cleanup.enabled is True


def test_empty_file_uses_all_defaults(tmp_path):
    s = load_settings(write(tmp_path, ""), ENV)
    assert s.paths.archive_dir == Path("data/audio_to_text")


def test_invalid_value_names_field(tmp_path):
    with pytest.raises(ConfigError, match=r"transcription\.chunk_seconds"):
        load_settings(write(tmp_path, "transcription:\n  chunk_seconds: -1\n"), ENV)


def test_unknown_field_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"device\.vendr_match"):
        load_settings(write(tmp_path, "device:\n  vendr_match: [x]\n"), ENV)


def test_invalid_usb_id(tmp_path):
    with pytest.raises(ConfigError, match=r"device\.usb_id"):
        load_settings(write(tmp_path, "device:\n  usb_id: nope\n"), ENV)


def test_invalid_timezone(tmp_path):
    with pytest.raises(ConfigError, match=r"ingest\.timezone"):
        load_settings(write(tmp_path, "ingest:\n  timezone: Mars/Olympus\n"), ENV)


def test_unknown_provider_lists_supported(tmp_path):
    with pytest.raises(ConfigError, match=r"transcription\.provider.*'openrouter'"):
        load_settings(write(tmp_path, "transcription:\n  provider: ollama\n"), ENV)


def test_missing_api_key_fails(tmp_path):
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        load_settings(write(tmp_path, ""), {})


def test_secret_in_config_rejected(tmp_path):
    with pytest.raises(ConfigError, match=".env"):
        load_settings(write(tmp_path, "openrouter_api_key: abc\n"), ENV)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml", ENV)
