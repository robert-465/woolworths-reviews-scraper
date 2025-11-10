thonimport json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

def _ensure_parent_dir(path: Path) -> None:
    if not path.parent.exists():
        logger.debug("Creating parent directory: %s", path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)

def export_to_json(reviews: Iterable[Dict[str, Any]], output_path: Path, pretty: bool = True) -> None:
    """
    Write all reviews to a single JSON file.

    :param reviews: Iterable of review dictionaries.
    :param output_path: Destination path for the JSON file.
    :param pretty: Whether to pretty-print the JSON.
    """
    reviews_list: List[Dict[str, Any]] = list(reviews)
    logger.info("Exporting %d review(s) to %s", len(reviews_list), output_path)

    _ensure_parent_dir(output_path)

    data_to_dump: Any = reviews_list
    indent = 2 if pretty else None

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data_to_dump, f, indent=indent, ensure_ascii=False)

    logger.debug("JSON export complete: %s", output_path)