thonimport logging
from datetime import datetime
from typing import Optional

from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

def normalize_review_date(date_str: str, default_tz: str = "UTC") -> Optional[str]:
    """
    Convert various date formats to an ISO-8601 UTC timestamp string.

    Examples of accepted inputs:
      - "2022-10-03T02:38:00.000Z"
      - "2022-10-03"
      - "03 Oct 2022"
      - "2022-10-03 02:38"
    """
    if not date_str:
        return None

    try:
        dt = date_parser.parse(date_str)
    except Exception as exc:
        logger.debug("Failed to parse date string %r: %s", date_str, exc)
        return None

    if not dt.tzinfo:
        # Assume UTC if no timezone information
        try:
            from dateutil.tz import gettz

            dt = dt.replace(tzinfo=gettz(default_tz))
        except Exception:
            # Fallback: treat as naive UTC
            pass

    try:
        dt_utc = dt.astimezone(datetime.timezone.utc)  # type: ignore[attr-defined]
    except Exception:
        # Python <3.11 compatibility
        dt_utc = dt.astimezone()

    return dt_utc.isoformat()