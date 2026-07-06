"""Command-line entry point: ``oag-generate --config CONFIG --out DIR``."""

from __future__ import annotations

import argparse

from oag_generator.config import Config, load_config
from oag_generator.generator import generate_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oag-generate",
        description="Deterministic OSDU/PDM-shaped generator + gold answers (slice #2).",
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to a YAML config. Omit to use the built-in defaults "
             "(mirrored in configs/default.yaml).",
    )
    parser.add_argument(
        "--out", "-o", required=True,
        help="Output directory for canonical/, gold/, and dataset.json.",
    )
    parser.add_argument("--seed", type=int, help="Override the config seed.")
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else Config()
    if args.seed is not None:
        cfg.seed = args.seed

    manifest = generate_dataset(cfg, args.out)
    print(f"Generated dataset (config_hash={manifest.config_hash}) -> {manifest.output_dir}")
    for name, count in manifest.row_counts.items():
        print(f"  {name}: {count} rows -> {manifest.tables[name]}")
    for name, path in manifest.gold.items():
        print(f"  gold/{name}.json -> {path}")
    for name, path in manifest.osdu.items():
        print(f"  osdu/{name}.json -> {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
