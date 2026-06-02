"""Configuration loader."""
from pathlib import Path
import yaml
import os
from dotenv import load_dotenv


load_dotenv()


def load_yaml(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(name: str, config_dir: str | Path = None) -> dict:
    if config_dir is None:
        config_dir = Path(__file__).parent.parent.parent / "configs"
    return load_yaml(Path(config_dir) / f"{name}.yaml")


def get_env(key: str, default: str = None) -> str:
    val = os.getenv(key, default)
    if val is None:
        raise ValueError(f"Required environment variable '{key}' is not set.")
    return val
