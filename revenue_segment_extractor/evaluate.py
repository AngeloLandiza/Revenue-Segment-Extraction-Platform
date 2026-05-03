from __future__ import annotations

import argparse
import glob
from decimal import Decimal
from pathlib import Path

from revenue_segment_extractor.evaluation import (
    MatchThresholds,
    evaluate_rows,
    load_gold_files,
    load_prediction_files,
    write_reports,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate revenue-segment extraction exports against a labeled gold set."
    )
    parser.add_argument(
        "--gold",
        nargs="+",
        required=True,
        help="Gold CSV/JSON file paths or glob patterns, for example data/gold/*.csv.",
    )
    parser.add_argument(
        "--pred",
        nargs="+",
        required=True,
        help=(
            "Prediction CSV/JSON files, export document directories, or glob patterns. "
            "audit_export.json is preferred when both JSON and CSV are present."
        ),
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=Path("reports"),
        help="Directory where evaluation_summary.md, evaluation_results.csv, and failure_analysis.md are written.",
    )
    parser.add_argument(
        "--segment-threshold",
        type=float,
        default=0.68,
        help="Minimum normalized segment-name similarity for row matching.",
    )
    parser.add_argument(
        "--match-threshold",
        type=float,
        default=0.62,
        help="Minimum weighted match score after value, period, and page evidence.",
    )
    parser.add_argument(
        "--value-relative-tolerance",
        type=Decimal,
        default=Decimal("0.005"),
        help="Relative tolerance for normalized revenue value equality.",
    )
    parser.add_argument(
        "--value-absolute-tolerance",
        type=Decimal,
        default=Decimal("1"),
        help="Absolute tolerance for normalized revenue value equality.",
    )
    args = parser.parse_args()

    gold_paths = _expand_paths(args.gold)
    prediction_paths = _expand_paths(args.pred)
    thresholds = MatchThresholds(
        segment_similarity=args.segment_threshold,
        match_score=args.match_threshold,
        value_relative_tolerance=args.value_relative_tolerance,
        value_absolute_tolerance=args.value_absolute_tolerance,
    )

    gold_rows = load_gold_files(gold_paths)
    prediction_input = load_prediction_files(prediction_paths)
    report = evaluate_rows(
        gold_rows,
        list(prediction_input.rows),
        document_contexts=prediction_input.document_contexts,
        thresholds=thresholds,
    )
    write_reports(report, args.reports_dir)
    print(f"Wrote evaluation reports to {args.reports_dir}")
    print(
        "precision={:.3f} recall={:.3f} f1={:.3f}".format(
            report.metrics.precision,
            report.metrics.recall,
            report.metrics.f1,
        )
    )


def _expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            paths.extend(Path(match) for match in sorted(matches))
        else:
            paths.append(Path(pattern))
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise SystemExit(f"Missing input path(s): {', '.join(missing)}")
    return paths


if __name__ == "__main__":
    main()
