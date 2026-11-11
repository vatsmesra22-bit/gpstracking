"""Process WhatsApp exports into the CSP GPS issue workbook and dashboard data.

    python process.py                                  # real data: input/ -> output/
    python process.py --start 2026-09-22 --end 2026-10-15
    python process.py --synthetic                      # labelled test data: samples/synthetic_input -> output_synthetic/
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "src"))

from csp_gps.config import load_settings  # noqa: E402
from csp_gps.pipeline import run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", help="folder with WhatsApp .txt/.zip exports, .jsonl captures and images")
    ap.add_argument("--output", help="output folder")
    ap.add_argument("--start", help="first date to include, YYYY-MM-DD")
    ap.add_argument("--end", help="last date to include, YYYY-MM-DD (default: latest message date)")
    ap.add_argument("--config", help="path to the settings file (default: config/settings.yaml)")
    ap.add_argument("--no-ocr", action="store_true", help="skip OCR even if Tesseract is installed")
    ap.add_argument("--synthetic", action="store_true", help="process the SYNTHETIC test data set")
    ap.add_argument("--auto-import", action="store_true",
                    help="first copy new WhatsApp export zips from the watch folder (CSP_WATCH_FOLDER) into input/")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.output) if args.output else (PROJECT / "output_synthetic" if args.synthetic else None)
    log_dir = PROJECT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(log_dir / ("process_synthetic.log" if args.synthetic else "process.log"),
                                      encoding="utf-8")],
    )
    log = logging.getLogger("process")

    overrides = {
        "input_dir": args.input or ("samples/synthetic_input" if args.synthetic else None),
        "output_dir": str(out_dir) if out_dir else None,
        "state_db": "data/state_synthetic.sqlite" if args.synthetic else None,
        "report_name": "SYNTHETIC_TEST_CSP_GPS_Issue_Report.xlsx" if args.synthetic else None,
        "start": args.start, "end": args.end, "no_ocr": args.no_ocr, "synthetic": args.synthetic,
    }
    try:
        settings = load_settings(PROJECT, Path(args.config) if args.config else None, **overrides)
    except ValueError as exc:
        log.error("Invalid configuration: %s", exc)
        return 2

    if args.auto_import and not args.synthetic:
        from csp_gps.autoimport import import_new_exports
        if settings.watch_folder:
            new = import_new_exports(settings.watch_folder, settings.import_patterns, settings.input_dir)
            log.info("Auto-import: %d new export(s)%s", len(new), f": {', '.join(new)}" if new else "")
            reports = import_new_exports(settings.watch_folder, settings.visit_report_patterns,
                                         settings.input_dir / "visit_reports", suffixes=(".xlsx", ".xls", ".csv"))
            log.info("Auto-import: %d new visit report(s)%s", len(reports), f": {', '.join(reports)}" if reports else "")
        else:
            log.warning("Auto-import requested but no watch folder is configured")

    data_exts = {".txt", ".zip", ".jsonl", ".jpg", ".jpeg", ".png", ".webp"}
    has_data = settings.input_dir.exists() and any(
        p.is_file() and p.suffix.lower() in data_exts for p in settings.input_dir.rglob("*"))
    if not has_data:
        if args.synthetic:
            log.error("No synthetic data found. Run: python scripts/generate_synthetic_data.py")
        else:
            log.error("Input folder %s is empty. Put WhatsApp exports (.zip/.txt + media) there. "
                      "Nothing was processed and no report was produced.", settings.input_dir)
        return 1

    summary = run(settings)
    print("\n=== Processing summary ===")
    for key in ("data_source", "period_start", "period_end", "messages_parsed", "messages_in_range",
                "images_processed", "attachments_missing", "issues_identified", "needs_verification",
                "possible_duplicates", "unlinked_images", "ocr_status", "manual_edits_preserved"):
        print(f"{key:24}: {summary.get(key)}")
    print(f"{'files_parsed':24}: {len(summary['files_parsed'])}")
    print(f"{'files_failed':24}: {json.dumps(summary['files_failed'], ensure_ascii=False)}")
    print(f"{'report':24}: {settings.report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
