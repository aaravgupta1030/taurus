#!/usr/bin/env python3
import argparse
import logging
import sys

from src.output_writer import print_top_creators, write_outputs
from src.pipeline import run_pipeline
from src.utils import log_error


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def main() -> int:
    _setup_logging()
    p = argparse.ArgumentParser(description="Creator Sourcing Agent")
    p.add_argument("query", help='Brand or niche, e.g. "dog wellness creators"')
    args = p.parse_args()
    user_query = (args.query or "").strip()
    if not user_query:
        log_error("Empty query")
        return 2

    try:
        creators = run_pipeline(user_query)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        log_error(f"Pipeline crashed: {e}")
        logging.exception("Fatal")
        return 1

    paths = write_outputs(creators)
    print(f"\nWrote {paths['json']} and {paths['csv']}")
    print_top_creators(creators, 10)
    if len(creators) < 10:
        print(
            f"\nWarning: only {len(creators)} creators after enrichment. "
            "Add SERPAPI_KEY, YOUTUBE_API_KEY, and APIFY_API_TOKEN for full results."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
