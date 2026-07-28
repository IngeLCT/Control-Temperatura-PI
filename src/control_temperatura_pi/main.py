from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .factory import build_controller
from .ui import run_ui


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control PID de temperatura")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Ruta del archivo TOML de configuración",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    controller = build_controller(config)
    run_ui(controller, config)


if __name__ == "__main__":
    main()
