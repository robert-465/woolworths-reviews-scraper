thonimport argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any

import requests

# Ensure local src directory is on sys.path so namespace packages work
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from extractors.woolworths_parser import WoolworthsReviewsScraper  # type: ignore
from outputs.exporter import export_to_json  # type: ignore

def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

def load_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Settings file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_urls(input_path: Path) -> List[str]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input URLs file not found: {input_path}")
    urls: List[str] = []
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    if not urls:
        raise ValueError(f"No URLs found in {input_path}")
    return urls

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Woolworths Reviews Scraper - Collect product reviews from Woolworths AU & NZ."
    )
    default_settings = CURRENT_DIR / "config" / "settings.example.json"

    parser.add_argument(
        "--settings",
        type=str,
        default=str(default_settings),
        help=f"Path to settings JSON file (default: {default_settings})",
    )
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Override input URLs file path from settings.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Override output JSON file path from settings.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for INFO, -vv for DEBUG).",
    )
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    logger = logging.getLogger("runner")

    settings_path = Path(args.settings).resolve()
    try:
        settings = load_settings(settings_path)
    except Exception as exc:
        logger.error("Failed to load settings from %s: %s", settings_path, exc)
        sys.exit(1)

    input_file = Path(args.input) if args.input else Path(settings.get("input_file", "data/inputs.sample.txt"))
    output_file = Path(args.output) if args.output else Path(settings.get("output_file", "data/sample.json"))

    try:
        urls = load_urls(input_file)
    except Exception as exc:
        logger.error("Failed to load URLs from %s: %s", input_file, exc)
        sys.exit(1)

    logger.info("Loaded %d URL(s) from %s", len(urls), input_file)

    region = settings.get("region", "au")
    max_workers = int(settings.get("concurrency", 4))
    timeout = float(settings.get("timeout", 10.0))
    request_delay = float(settings.get("request_delay", 0.0))
    user_agent = settings.get(
        "user_agent",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    )

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    scraper = WoolworthsReviewsScraper(
        session=session,
        region=region,
        timeout=timeout,
        request_delay=request_delay,
    )

    all_reviews: List[Dict[str, Any]] = []
    errors = 0

    logger.info("Starting scrape with %d workers (region=%s)", max_workers, region)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(scraper.scrape_product_reviews, url): url for url in urls
        }

        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                reviews = future.result()
                logger.info("Fetched %d review(s) from %s", len(reviews), url)
                all_reviews.extend(reviews)
            except Exception as exc:
                logger.error("Error scraping %s: %s", url, exc, exc_info=args.verbose >= 2)
                errors += 1

    logger.info("Scraping finished. Total reviews: %d, Errors: %d", len(all_reviews), errors)

    try:
        export_to_json(all_reviews, output_file)
    except Exception as exc:
        logger.error("Failed to export reviews to %s: %s", output_file, exc)
        sys.exit(1)

    logger.info("Export complete: %s", output_file)
    print(f"Scraping complete. {len(all_reviews)} review(s) saved to {output_file}")

if __name__ == "__main__":
    main()