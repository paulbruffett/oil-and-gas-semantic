"""Read a named `snow` CLI connection from the standard Snowflake config locations (stdlib only).

Lookup order follows the Snowflake CLI: $SNOWFLAKE_HOME, then ~/.snowflake, then the platform
default (~/Library/Application Support/snowflake on macOS, $XDG_CONFIG_HOME or ~/.config/snowflake
elsewhere). A connection may live in config.toml under [connections.<name>] or in connections.toml
under [<name>].
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path


def _config_dir() -> Path:
    if os.environ.get("SNOWFLAKE_HOME"):
        return Path(os.environ["SNOWFLAKE_HOME"]).expanduser()
    home_dir = Path.home() / ".snowflake"
    if home_dir.is_dir():
        return home_dir
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "snowflake"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "snowflake"


def connection(name: str) -> dict:
    """The parameters of connection `name`; raises KeyError if it is not configured."""
    d = _config_dir()
    for path, section in ((d / "connections.toml", None), (d / "config.toml", "connections")):
        if path.is_file():
            data = tomllib.loads(path.read_text())
            data = data.get(section, {}) if section else data
            if name in data:
                return dict(data[name])
    raise KeyError(f"Snowflake connection {name!r} not found in {d}/connections.toml or config.toml")
