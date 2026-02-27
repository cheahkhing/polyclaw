"""Configuration loader for Polyclaw."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env if present
load_dotenv()

DEFAULT_CONFIG_FILENAME = "polyclaw.config.json"


@dataclass
class MockConfig:
    starting_balance: float = 1000.0
    slippage_bps: int = 10


@dataclass
class PolymarketConfig:
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    private_key_env: str = "POLYCLAW_PRIVATE_KEY"
    funder_env: str = "POLYCLAW_FUNDER_ADDRESS"
    # 0 = EOA (self-custody), 1 = POLY_PROXY (standard Polymarket account)
    # Use 1 when funder_address is a Polymarket proxy wallet.
    signature_type: int = 1

    @property
    def private_key(self) -> str | None:
        return os.environ.get(self.private_key_env)

    @property
    def funder_address(self) -> str | None:
        return os.environ.get(self.funder_env)


@dataclass
class StreamingConfig:
    enabled: bool = True
    channels: list[str] = field(default_factory=lambda: ["market", "rtds"])
    auto_subscribe_top_n: int = 50
    reconnect_delay_ms: int = 1000
    reconnect_max_delay_ms: int = 30000
    rtds_crypto_symbols: list[str] = field(
        default_factory=lambda: ["btcusdt", "ethusdt", "solusdt"]
    )


@dataclass
class RiskConfig:
    max_position_size: float = 50.0
    max_open_positions: int = 10
    max_daily_trades: int = 20
    min_confidence: float = 0.6


@dataclass
class FiltersConfig:
    min_volume_24hr: float = 1000
    min_liquidity: float = 5000
    tags_include: list[str] = field(default_factory=list)
    tags_exclude: list[str] = field(default_factory=list)


@dataclass
class DatabaseConfig:
    path: str = "./data/polyclaw.db"


@dataclass
class SimConfig:
    """Simulation-specific configuration."""
    default_tick_interval_seconds: int = 30
    default_duration_minutes: int = 240
    snapshot_every_n_ticks: int = 10
    record_prices: bool = True
    price_db_path: str = "./data/price_history.db"


@dataclass
class DashboardConfig:
    """Web dashboard configuration."""
    host: str = "127.0.0.1"
    port: int = 8420
    auto_open_browser: bool = True


@dataclass
class PolyclawConfig:
    """Root configuration object."""

    mode: str = "mock"
    mock: MockConfig = field(default_factory=MockConfig)
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    simulation: SimConfig = field(default_factory=SimConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    strategies: dict = field(default_factory=dict)
    log_level: str = "INFO"


def _apply_dict(obj: Any, data: dict) -> None:
    """Recursively apply a dict of values onto a dataclass instance."""
    for key, value in data.items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if isinstance(current, (MockConfig, PolymarketConfig, StreamingConfig,
                                RiskConfig, FiltersConfig,
                                DatabaseConfig, SimConfig, DashboardConfig)):
            if isinstance(value, dict):
                _apply_dict(current, value)
        else:
            setattr(obj, key, value)


def load_config(config_path: str | Path | None = None) -> PolyclawConfig:
    """Load configuration from a JSON file, falling back to defaults.

    Resolution order:
    1. Explicit *config_path* argument
    2. ``POLYCLAW_CONFIG`` environment variable
    3. ``polyclaw.config.json`` in the current working directory
    4. Pure defaults
    """
    if config_path is None:
        config_path = os.environ.get("POLYCLAW_CONFIG")
    if config_path is None:
        candidate = Path.cwd() / DEFAULT_CONFIG_FILENAME
        if candidate.exists():
            config_path = candidate

    cfg = PolyclawConfig()

    if config_path is not None:
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            _apply_dict(cfg, raw)

    # Environment variable overrides
    env_mode = os.environ.get("POLYCLAW_MODE")
    if env_mode:
        cfg.mode = env_mode

    return cfg
