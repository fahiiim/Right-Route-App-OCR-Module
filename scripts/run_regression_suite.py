import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import (
    classify_route_quality,
    extract_route_information_deterministic,
    extract_route_information_with_diagnostics,
)


def load_dataset(dataset_path):
    path = Path(dataset_path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {dataset_path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Dataset must be a JSON array of sample objects.")
    return payload


def run_sample(sample, pipeline_mode):
    sample_id = sample.get("id", "unknown")
    ocr_text = sample.get("ocr_text", "")

    if pipeline_mode == "deterministic":
        route_info = extract_route_information_deterministic(ocr_text)
        diagnostics = {
            "final_source": "deterministic-fallback",
            "quality_classification": classify_route_quality(route_info),
        }
    else:
        route_info, diagnostics = extract_route_information_with_diagnostics(ocr_text)

    quality = classify_route_quality(route_info)
    route_segments = route_info.get("route_segments", []) or []
    start_location = route_info.get("start_location")
    end_location = route_info.get("end_location")

    result = {
        "id": sample_id,
        "quality": quality,
        "segment_count": len(route_segments),
        "has_start": bool(start_location),
        "has_end": bool(end_location),
        "final_source": diagnostics.get("final_source"),
        "diagnostics": diagnostics,
        "route_information": route_info,
        "errors": [],
    }

    expected = sample.get("expected", {}) or {}
    min_segments = int(expected.get("min_route_segments", 0))
    require_start_end = bool(expected.get("require_start_end", False))
    expected_quality = expected.get("quality")

    if result["segment_count"] < min_segments:
        result["errors"].append(
            f"segment_count {result['segment_count']} < min_route_segments {min_segments}"
        )

    if require_start_end and not (result["has_start"] and result["has_end"]):
        result["errors"].append("missing start/end location")

    if expected_quality and result["quality"] != expected_quality:
        result["errors"].append(
            f"quality {result['quality']} != expected {expected_quality}"
        )

    return result


def summarize_results(results):
    total = len(results)
    if total == 0:
        return {
            "total": 0,
            "complete_route_rate": 0.0,
            "start_end_only_rate": 0.0,
            "empty_segment_rate": 0.0,
            "failed_samples": 0,
        }

    complete_count = sum(1 for item in results if item["quality"] == "complete-route")
    start_end_only_count = sum(1 for item in results if item["quality"] == "start-end-only")
    empty_segments_count = sum(1 for item in results if item["quality"] == "empty-segments")
    failed_samples = sum(1 for item in results if item["errors"])

    return {
        "total": total,
        "complete_route_rate": round(complete_count / float(total), 4),
        "start_end_only_rate": round(start_end_only_count / float(total), 4),
        "empty_segment_rate": round(empty_segments_count / float(total), 4),
        "failed_samples": failed_samples,
    }


def enforce_thresholds(summary, args):
    failures = []

    if summary["complete_route_rate"] < args.min_complete_rate:
        failures.append(
            f"complete_route_rate {summary['complete_route_rate']} is below {args.min_complete_rate}"
        )

    if summary["start_end_only_rate"] > args.max_start_end_only_rate:
        failures.append(
            f"start_end_only_rate {summary['start_end_only_rate']} exceeds {args.max_start_end_only_rate}"
        )

    if summary["empty_segment_rate"] > args.max_empty_segment_rate:
        failures.append(
            f"empty_segment_rate {summary['empty_segment_rate']} exceeds {args.max_empty_segment_rate}"
        )

    if summary["failed_samples"] > 0:
        failures.append(f"{summary['failed_samples']} sample(s) violated explicit expectations")

    return failures


def main():
    parser = argparse.ArgumentParser(description="Run permit extraction regression checks.")
    parser.add_argument(
        "--dataset",
        default="benchmarks/permit_samples.json",
        help="Path to benchmark dataset JSON",
    )
    parser.add_argument(
        "--pipeline-mode",
        choices=["deterministic", "enhanced"],
        default="deterministic",
        help="Which extraction path to benchmark",
    )
    parser.add_argument("--strict", action="store_true", help="Fail on threshold or sample expectation violations")
    parser.add_argument("--min-complete-rate", type=float, default=0.66)
    parser.add_argument("--max-start-end-only-rate", type=float, default=0.34)
    parser.add_argument("--max-empty-segment-rate", type=float, default=0.34)
    args = parser.parse_args()

    try:
        dataset = load_dataset(args.dataset)
    except Exception as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 2

    results = [run_sample(sample, args.pipeline_mode) for sample in dataset]
    summary = summarize_results(results)

    report = {
        "dataset": args.dataset,
        "pipeline_mode": args.pipeline_mode,
        "summary": summary,
        "samples": [
            {
                "id": result["id"],
                "quality": result["quality"],
                "segment_count": result["segment_count"],
                "has_start": result["has_start"],
                "has_end": result["has_end"],
                "final_source": result["final_source"],
                "errors": result["errors"],
            }
            for result in results
        ],
    }

    print(json.dumps(report, indent=2))

    if not args.strict:
        return 0

    threshold_failures = enforce_thresholds(summary, args)
    if threshold_failures:
        print("\nRegression gate failed:")
        for failure in threshold_failures:
            print(f"- {failure}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
