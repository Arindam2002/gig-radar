"""Config/profile loading with environment-based secrets.

API keys NEVER live in tracked files: they come from real environment
variables or a gitignored `.env` file (simple KEY=VALUE lines) and are
overlaid onto the `keys:` section of config.yaml at load time.

Paths are overridable for Docker (everything user-owned lives in one
mounted volume):
  JOBSCOUT_CONFIG  dir containing config.yaml / profile.yaml   (default: repo root)
  JOBSCOUT_DB      sqlite file path
  JOBSCOUT_BRIEFS  briefs dir        JOBSCOUT_STUDY  study dir
  JOBSCOUT_LOGS    logs dir
"""
import os
import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

ENV_KEYS = {
    "gemini_api_key": "GEMINI_API_KEY",
    "hunter_api_key": "HUNTER_API_KEY",
    "jsearch_rapidapi_key": "JSEARCH_RAPIDAPI_KEY",
    "adzuna_app_id": "ADZUNA_APP_ID",
    "adzuna_app_key": "ADZUNA_APP_KEY",
}


def config_dir() -> Path:
    return Path(os.environ.get("JOBSCOUT_CONFIG") or ROOT)


def briefs_dir() -> Path:
    return Path(os.environ.get("JOBSCOUT_BRIEFS") or ROOT / "briefs")


def study_dir() -> Path:
    return Path(os.environ.get("JOBSCOUT_STUDY") or ROOT / "study")


def logs_dir() -> Path:
    return Path(os.environ.get("JOBSCOUT_LOGS") or ROOT / "logs")


def load_dotenv():
    """Load KEY=VALUE lines from .env (repo root and config dir) into the
    environment without overriding variables that are already set."""
    for env_file in {ROOT / ".env", config_dir() / ".env"}:
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v


def _read_yaml(name: str) -> dict:
    """Read <name>.yaml from the config dir, falling back to the tracked
    <name>.example.yaml so a fresh clone works before any setup."""
    for candidate in (config_dir() / f"{name}.yaml", ROOT / f"{name}.yaml",
                      ROOT / f"{name}.example.yaml"):
        if candidate.exists():
            return yaml.safe_load(candidate.read_text())
    raise FileNotFoundError(f"neither {name}.yaml nor {name}.example.yaml found")


def load_configs() -> tuple[dict, dict]:
    load_dotenv()
    cfg = _read_yaml("config")
    profile = _read_yaml("profile")
    cfg.setdefault("keys", {})
    for cfg_key, env_key in ENV_KEYS.items():
        if os.environ.get(env_key):
            cfg["keys"][cfg_key] = os.environ[env_key]
        cfg["keys"].setdefault(cfg_key, "")
    return cfg, profile


def firecrawl_cmd() -> list[str]:
    """Command used to invoke the Firecrawl CLI.

    Default suits a dev machine (npx fetches/caches the CLI). Docker installs
    the CLI globally and sets JOBSCOUT_FIRECRAWL_CMD=firecrawl. When
    FIRECRAWL_API_KEY is set it is passed explicitly, so a browser-logged-in
    CLI keeps working without one.
    """
    load_dotenv()
    cmd = shlex.split(os.environ.get("JOBSCOUT_FIRECRAWL_CMD")
                      or "npx -y firecrawl-cli@latest")
    if os.environ.get("FIRECRAWL_API_KEY"):
        cmd += ["-k", os.environ["FIRECRAWL_API_KEY"]]
    return cmd
