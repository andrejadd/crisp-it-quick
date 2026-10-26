"""Configuration: non-secret settings from config.yaml, secrets from the environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

SUPPORTED_PROVIDERS = ("openrouter",)


class ConfigError(Exception):
    """Raised when configuration is missing, invalid, or incomplete."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceConfig(_Section):
    vendor_match: list[str] = ["Actions Semiconductor"]
    model_match: list[str] = ["D-Wave", "AK1025"]
    usb_id: str | None = Field(default=None, description="vendor_id:product_id, e.g. '10d6:1101'")
    poll_interval_s: float = Field(default=30, gt=0)
    debounce_s: float = Field(default=2, ge=0)
    mount_root: Path = Path("/mnt/recorder")
    mount_path_override: Path | None = None

    @field_validator("usb_id")
    @classmethod
    def _check_usb_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = v.lower().split(":")
        if len(parts) != 2 or not all(len(p) == 4 for p in parts):
            raise ValueError("must look like 'vvvv:pppp' (hex vendor and product id)")
        try:
            [int(p, 16) for p in parts]
        except ValueError as e:
            raise ValueError("must be hexadecimal 'vvvv:pppp'") from e
        return ":".join(parts)


class IngestConfig(_Section):
    record_folder: str = "RECORD"
    timezone: str = "Europe/Berlin"

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v


class PathsConfig(_Section):
    data_dir: Path = Path("data")
    owner_uid: int | None = None
    owner_gid: int | None = None

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "audio_to_text"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"


class OpenRouterConfig(_Section):
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_s: float = Field(default=120, gt=0)
    request_retries: int = Field(default=5, ge=0)


class TranscriptionConfig(_Section):
    provider: Literal["openrouter"] = "openrouter"
    model: str = "openai/whisper-large-v3"
    language: str | None = "de"
    chunk_seconds: float = Field(default=120, gt=10)
    chunk_overlap_s: float = Field(default=1, ge=0)
    max_attempts: int = Field(default=3, ge=1)


class CleanupConfig(_Section):
    enabled: bool = True
    provider: Literal["openrouter"] = "openrouter"
    model: str = "deepseek/deepseek-v4-flash-0731"
    prompt: str | None = None
    max_chunk_chars: int = Field(default=6000, ge=500)


class SyncConfig(_Section):
    interval_s: float = Field(default=300, ge=0, description="0 disables periodic sync")


class LoggingConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class Settings(_Section):
    device: DeviceConfig = DeviceConfig()
    ingest: IngestConfig = IngestConfig()
    paths: PathsConfig = PathsConfig()
    openrouter: OpenRouterConfig = OpenRouterConfig()
    transcription: TranscriptionConfig = TranscriptionConfig()
    cleanup: CleanupConfig = CleanupConfig()
    sync: SyncConfig = SyncConfig()
    logging: LoggingConfig = LoggingConfig()

    # Secrets: never read from config.yaml, only from the environment.
    openrouter_api_key: SecretStr | None = Field(default=None, exclude=True)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.ingest.timezone)

    def uses_openrouter(self) -> bool:
        return self.transcription.provider == "openrouter" or (
            self.cleanup.enabled and self.cleanup.provider == "openrouter"
        )

    def secrets(self) -> list[str]:
        key = self.openrouter_api_key
        return [key.get_secret_value()] if key and key.get_secret_value() else []


def _format_validation_error(err: ValidationError) -> str:
    lines = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "<root>"
        lines.append(f"  {loc}: {e['msg']}")
    return "invalid configuration:\n" + "\n".join(lines)


def load_settings(
    path: Path | str, env: dict[str, str] | None = None, *, require_secrets: bool = True
) -> Settings:
    """Load and validate config.yaml plus secrets from ``env`` (defaults to os.environ)."""
    env = os.environ if env is None else env
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"config file {path} is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must contain a mapping at the top level")
    if "openrouter_api_key" in raw:
        raise ConfigError("openrouter_api_key must not be set in config.yaml; put it in .env")

    try:
        settings = Settings.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(_format_validation_error(e)) from e

    key = env.get("OPENROUTER_API_KEY", "").strip()
    settings.openrouter_api_key = SecretStr(key) if key else None
    if require_secrets and settings.uses_openrouter() and settings.openrouter_api_key is None:
        raise ConfigError(
            "missing environment variable OPENROUTER_API_KEY (required by provider 'openrouter')"
        )
    return settings
