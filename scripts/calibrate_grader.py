"""Run with python -m scripts.calibrate_grader; live mode incurs provider API usage."""

import argparse
import json
from pathlib import Path

from backend.calibration import CalibrationDataset, calibrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", help="Human-reviewed JSON dataset")
    parser.add_argument("--output", default="artifacts/grader-calibration.json")
    parser.add_argument("--schema", action="store_true", help="Print the input JSON schema without API calls")
    args = parser.parse_args()
    if args.schema:
        print(json.dumps(CalibrationDataset.model_json_schema(), indent=2))
        return
    if not args.dataset:
        parser.error("A human-reviewed dataset is required")
    report = calibrate(Path(args.dataset).read_bytes())
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "passed": report["passed"], "metrics": report["metrics"]}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
