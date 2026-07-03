"""Demo entry point: ``oag-answer`` -- generated data -> agent answer -> graded correctness.

End-to-end (DESIGN.md §6, issue #3 AC #5): optionally (re)generate the dataset, answer the hero
surveillance question with the deterministic semantic-baseline agent, and grade the answer against
the co-generated gold.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from oag_semantic.agent import answer_surveillance
from oag_semantic.grading import grade_against_gold_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oag-answer",
        description="Answer the production-surveillance question and grade it against gold (slice #3).",
    )
    parser.add_argument("--dataset", "-d", required=True, help="Dataset directory (from oag-generate).")
    parser.add_argument("--field", "-f", help="Optional: scope the question to a single Field (name/synonym).")
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate the dataset into --dataset first (uses --config/--seed, else defaults).",
    )
    parser.add_argument("--config", "-c", help="Config YAML for --generate.")
    parser.add_argument("--seed", type=int, help="Override the config seed for --generate.")
    args = parser.parse_args(argv)

    dataset = Path(args.dataset)
    if args.generate:
        from oag_generator import Config, generate_dataset, load_config

        cfg = load_config(args.config) if args.config else Config()
        if args.seed is not None:
            cfg.seed = args.seed
        m = generate_dataset(cfg, dataset)
        print(f"Generated dataset (config_hash={m.config_hash}) -> {dataset}")

    submission = answer_surveillance(dataset, field=args.field)

    print("\n== Answer ==")
    print(submission.answer)
    print("\n== Provenance ==")
    prov = submission.to_dict()["provenance"]
    print(f"  metrics:    {', '.join(prov['metrics'])}")
    print(f"  dimensions: {', '.join(prov['dimensions'])}")
    print(f"  filters:    {', '.join(prov['filters'])}")
    print(f"  entities:   {', '.join(prov['entities'])}")
    print("\n== Key values ==")
    print(json.dumps(submission.key_values, indent=2))

    # Functional correctness is only defined for the unscoped hero question: the gold answer is
    # fleet-wide, so there is nothing to grade a Field-scoped subset against.
    if args.field:
        print("\n== Grade ==")
        print("  (skipped: --field scopes the answer; the gold answer is fleet-wide)")
        return 0

    report = grade_against_gold_file(submission.to_dict(), dataset / "gold" / "surveillance.json")
    print("\n== Grade (functional correctness vs gold) ==")
    print(f"  {report.summary()}")
    return 0 if report.correct else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
