"""Entry point ``oag-assess``: grade an implementation's submissions and emit a scorecard.

The runnable slice of the harness (issue #9), mirroring ``oag-answer``: point it at a submissions
directory + a generated dataset's gold and it computes the objective functional-correctness dimension,
optionally on a **held-out evaluation seed** (ADR 0016), and writes the per-implementation scorecard
(DESIGN.md §7). The panel and the human-run probes are contest-time infrastructure and aren't invoked
here; the scorecard leaves their slots empty in a purely functional run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from oag_harness.evalseed import EvalSeedRun, grade_on_eval_seed
from oag_harness.functional import load_submissions, score_submissions
from oag_harness.scorecard import Scorecard
from oag_harness.spec_fidelity import theme_breadth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oag-assess",
        description="Grade an implementation's answers against gold and emit a scorecard (#9).",
    )
    parser.add_argument("--submissions", "-s", required=True, help="Directory of answer-submission JSON files.")
    parser.add_argument("--dataset", "-d", required=True, help="Generated dataset dir (fork-time gold).")
    parser.add_argument("--implementation", "-i", default="submission", help="Implementation name.")
    parser.add_argument(
        "--eval-seed", type=int,
        help="Regenerate the dataset with this held-out seed and grade there (ADR 0016). "
             "Requires --config (the base config the seed is substituted into).",
    )
    parser.add_argument("--config", "-c", help="Base config YAML for --eval-seed regeneration.")
    parser.add_argument("--out", "-o", help="Write the scorecard JSON here (default: stdout only).")
    args = parser.parse_args(argv)

    from oag_generator.questions import load_catalog

    submissions = load_submissions(args.submissions)
    catalog = load_catalog()  # parsed once, shared by the theme-attempt map and the breadth report
    attempted_themes = _attempted_themes(submissions, catalog)

    if args.eval_seed is not None:
        if not args.config:
            parser.error("--eval-seed requires --config (the base config to regenerate from)")
        eval_dir = Path(args.dataset).parent / f"eval-seed-{args.eval_seed}"
        run: EvalSeedRun = grade_on_eval_seed(submissions, args.config, args.eval_seed, eval_dir)
        functional, forktime = run, None
        print(f"== Functional correctness (held-out eval seed {args.eval_seed}) ==")
    else:
        forktime = score_submissions(submissions, args.dataset)
        functional = None
        print("== Functional correctness (fork-time gold; NOT the graded number) ==")

    report = functional.score if functional else forktime
    print(f"  {report.summary()}")
    for grade in report.grades:
        print(f"  - {grade.summary()}")

    card = Scorecard(
        implementation=args.implementation,
        functional=functional,
        functional_forktime=forktime,
        theme_breadth=theme_breadth(attempted_themes, catalog),
    )
    if args.out:
        Path(args.out).write_text(json.dumps(card.to_dict(), indent=2) + "\n")
        print(f"\nScorecard -> {args.out}")

    return 0 if report.pass_rate == 1.0 else 1


def _attempted_themes(submissions: dict, catalog) -> list[str]:
    """Map answered question ids back to their theme ids for the reported theme-breadth signal."""
    return [t.id for t in catalog.themes if any(q.id in submissions for q in t.questions)]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
