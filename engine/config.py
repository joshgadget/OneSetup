"""Configuration: secrets from environment, parameters from config/params.v1.yaml.

Every parameter in the YAML has a named default taken from the specification, or is
marked [GAP] where the specification is silent. The parameter file is versioned and
hashed; the version and hash travel with every stored analysis row so results can
always be traced to the exact parameter set that produced them.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARAMS_PATH = REPO_ROOT / "config" / "params.v1.yaml"


class Settings(BaseSettings):
    """Environment-driven settings. Secrets live only here, never in code."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    fxcm_access_token: str = ""
    fxcm_host: str = "api-demo.fxcm.com"
    fxcm_price_side: str = "mid"
    fxcm_login_id: str = ""
    fxcm_password: str = ""
    fxcm_iam_host: str = "endpoints-demo.fxcm.com"

    deriv_app_id: str = ""

    # Which provider serves FX and gold. Switching broker feed = one env var.
    forex_venue: str = "deriv"
    gold_venue: str = "deriv"

    crypto_exchange: str = "deribit"

    db_path: str = "data/trading.sqlite3"
    params_path: str = str(DEFAULT_PARAMS_PATH)

    # Browser origins allowed to call the API, comma separated. The deployed site sets
    # this to its own domain; "*" allows any origin (fine while nothing is authenticated,
    # but set the real domain before this stops being a demo).
    allowed_origins: str = ""

    @property
    def cors_origins(self) -> list[str]:
        origins = [part.strip() for part in self.allowed_origins.split(",") if part.strip()]
        return origins or ["http://localhost:5173", "http://127.0.0.1:5173"]


class Params(Mapping):
    """Immutable-ish nested mapping with attribute access for reading parameters."""

    def __init__(self, data: Mapping[str, Any], source_path: Path, file_hash: str) -> None:
        self._data = _freeze(data)
        self.source_path = source_path
        self.file_hash = file_hash

    def __getattr__(self, item: str) -> Any:
        try:
            return self._data[item]
        except KeyError as exc:  # pragma: no cover - programming error
            raise AttributeError(item) from exc

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    @property
    def version(self) -> str:
        return str(self._data["version"])

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if isinstance(node, Mapping) and part in node:
                node = node[part]
            else:
                return default
        return node


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _Frozen({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


class _Frozen(dict):
    """dict that also allows attribute access, used for nested parameter groups."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


@lru_cache(maxsize=4)
def load_params(path: str | None = None) -> Params:
    p = Path(path) if path else DEFAULT_PARAMS_PATH
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    data = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"parameter file {p} did not parse to a mapping")
    return Params(data, p, digest)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()