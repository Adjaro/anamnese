"""Configuration de l'application, lue dans l'environnement puis dans `.env`.

Seul module autorise a lire l'environnement (CLAUDE.md §6). Chaque variable est
documentee dans `.env.example` ; une valeur vide y vaut « non renseignee ».
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parametres de l'API, du moteur et de l'evaluation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    mistral_api_key: SecretStr | None = Field(default=None, validation_alias="MISTRAL_API_KEY")
    mistral_model: str = Field(
        default="codestral-latest", min_length=1, validation_alias="MISTRAL_MODEL"
    )
    duckdb_path: Path = Field(
        default=Path("data/warehouse/anamnese.duckdb"), validation_alias="ANAMNESE_DUCKDB_PATH"
    )
    dbt_target_path: Path = Field(
        default=Path("transform/target"), validation_alias="ANAMNESE_DBT_TARGET_PATH"
    )
    max_rows: int = Field(default=1000, gt=0, validation_alias="ANAMNESE_MAX_ROWS")
    query_timeout_seconds: float = Field(
        default=10.0, gt=0, validation_alias="ANAMNESE_QUERY_TIMEOUT_SECONDS"
    )
    api_url: str = Field(default="http://localhost:8000", validation_alias="ANAMNESE_API_URL")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", validation_alias="ANAMNESE_LOG_LEVEL"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuration du processus, lue une seule fois."""
    return Settings()
