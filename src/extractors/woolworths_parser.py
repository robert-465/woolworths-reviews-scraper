thonimport json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
import requests

from .utils_date import normalize_review_date

logger = logging.getLogger(__name__)

@dataclass
class Review:
    productUrl: str
    username: Optional[str]
    text: str
    createdDate: Optional[str]
    rating: Optional[int]
    source: Optional[str]
    syndicatedSource: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "productUrl": self.productUrl,
            "username": self.username,
            "text": self.text,
            "createdDate": self.createdDate,
            "rating": self.rating,
            "source": self.source,
            "syndicatedSource": self.syndicatedSource,
        }

class WoolworthsReviewsScraper:
    """
    Scraper for Woolworths product reviews (AU/NZ).

    This implementation focuses on the following strategy:

    1. Fetch the product HTML page.
    2. Look for <script type="application/ld+json"> blocks that contain "review".
    3. Parse review data from JSON-LD where available.
    4. As a fallback, inspect any embedded JSON in <script> tags that contain "review".
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        region: str = "au",
        timeout: float = 10.0,
        request_delay: float = 0.0,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.request_delay = request_delay

        region = region.lower()
        if region not in {"au", "nz"}:
            raise ValueError("Region must be 'au' or 'nz'")

        if region == "au":
            self.base_domain = "www.woolworths.com.au"
        else:
            self.base_domain = "www.woolworths.co.nz"

    def _normalize_url(self, url: str) -> str:
        url = url.strip()
        if not url:
            raise ValueError("Empty URL provided")

        if url.startswith("http://") or url.startswith("https://"):
            return url

        # Assume relative path on Woolworths domain
        normalized = f"https://{self.base_domain}{url if url.startswith('/') else '/' + url}"
        logger.debug("Normalized relative URL %s -> %s", url, normalized)
        return normalized

    def _fetch_html(self, url: str) -> str:
        normalized_url = self._normalize_url(url)
        logger.debug("Fetching URL: %s", normalized_url)

        try:
            response = self.session.get(normalized_url, timeout=self.timeout)
        except Exception as exc:
            logger.error("Request error for %s: %s", normalized_url, exc)
            raise

        if not response.ok:
            logger.error("Request to %s failed with status %s", normalized_url, response.status_code)
            response.raise_for_status()

        if self.request_delay > 0:
            time.sleep(self.request_delay)

        return response.text

    @staticmethod
    def _extract_json_from_script(script_text: str) -> List[Any]:
        """
        Attempt to extract one or more JSON blobs from a script tag text.
        This is heuristic and looks for {...} or [...] blocks containing "review".
        """
        candidates: List[Any] = []

        # First: try the entire script as JSON
        stripped = script_text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                candidates.append(parsed)
            except Exception:
                pass

        # Second: regex fallback for embedded JSON objects/arrays
        # We're conservative: only keep small-ish chunks that contain "review"
        json_like_patterns = re.findall(r"(\{.*?\"review\".*?\})", script_text, flags=re.DOTALL | re.IGNORECASE)
        for match in json_like_patterns:
            try:
                parsed = json.loads(match)
                candidates.append(parsed)
            except Exception:
                continue

        return candidates

    @staticmethod
    def _iter_reviews_from_json(json_obj: Any) -> List[Dict[str, Any]]:
        """
        Given a JSON object that may contain reviews, flatten it into a list of review dicts.
        Handles a few common JSON-LD / structured data shapes.
        """
        reviews: List[Dict[str, Any]] = []

        def _collect(obj: Any) -> None:
            nonlocal reviews
            if isinstance(obj, dict):
                # Direct review object
                if obj.get("@type") == "Review" or ("reviewRating" in obj and "reviewBody" in obj):
                    reviews.append(obj)
                # Container with 'review' list
                if "review" in obj and isinstance(obj["review"], list):
                    for r in obj["review"]:
                        _collect(r)
                # Otherwise recursively walk values
                for v in obj.values():
                    _collect(v)
            elif isinstance(obj, list):
                for item in obj:
                    _collect(item)

        _collect(json_obj)
        return reviews

    def _parse_reviews_from_html(self, html: str, product_url: str) -> List[Review]:
        soup = BeautifulSoup(html, "lxml")

        # 1) JSON-LD scripts with potential review data
        candidate_json: List[Any] = []
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            text = script.string or script.get_text()
            try:
                parsed = json.loads(text)
                candidate_json.append(parsed)
            except Exception:
                # Some sites pack multiple JSON-LD objects in one script
                # Try a list fallback
                try:
                    wrapped = f"[{text}]"
                    parsed = json.loads(wrapped)
                    candidate_json.extend(parsed)
                except Exception:
                    continue

        # 2) Fallback: other scripts that mention "review"
        if not candidate_json:
            for script in soup.find_all("script"):
                text = script.string or script.get_text()
                if not text or "review" not in text.lower():
                    continue
                candidate_json.extend(self._extract_json_from_script(text))

        raw_review_dicts: List[Dict[str, Any]] = []
        for obj in candidate_json:
            raw_review_dicts.extend(self._iter_reviews_from_json(obj))

        reviews: List[Review] = []
        for item in raw_review_dicts:
            try:
                reviews.append(self._build_review_from_json(item, product_url))
            except Exception as exc:
                logger.debug("Failed to build review from JSON item: %s", exc)

        # De-duplicate by combination of text + createdDate if present
        unique: Dict[str, Review] = {}
        for r in reviews:
            key = f"{(r.text or '').strip()}|{r.createdDate or ''}"
            if key and key not in unique:
                unique[key] = r

        return list(unique.values())

    def _build_review_from_json(self, item: Dict[str, Any], product_url: str) -> Review:
        # JSON-LD Review often has:
        # - author: { "@type": "Person", "name": "..." } or a string
        # - reviewBody
        # - reviewRating: { "ratingValue": 5 }
        # - datePublished
        author = item.get("author")
        username: Optional[str] = None
        if isinstance(author, dict):
            username = author.get("name")
        elif isinstance(author, str):
            username = author

        text = item.get("reviewBody") or item.get("description") or ""
        text = text.strip()

        rating_val: Optional[int] = None
        review_rating = item.get("reviewRating")
        if isinstance(review_rating, dict):
            value = review_rating.get("ratingValue")
            try:
                rating_val = int(value)
            except Exception:
                rating_val = None
        elif isinstance(review_rating, (int, float, str)):
            try:
                rating_val = int(review_rating)
            except Exception:
                rating_val = None

        created_raw = item.get("datePublished") or item.get("dateCreated")
        created_norm = normalize_review_date(created_raw) if created_raw else None

        # Source/syndicatedSource are site-specific; we provide reasonable defaults
        source = item.get("source") or "Woolworths/BazaarVoice"
        syndicated_source = item.get("syndicatedSource")

        return Review(
            productUrl=product_url,
            username=username,
            text=text,
            createdDate=created_norm,
            rating=rating_val,
            source=source,
            syndicatedSource=syndicated_source,
        )

    def scrape_product_reviews(self, url: str) -> List[Dict[str, Any]]:
        """
        Public high-level API: scrape all reviews for a single product URL.
        Returns a list of dicts ready to be serialized.
        """
        html = self._fetch_html(url)
        reviews = self._parse_reviews_from_html(html, self._normalize_url(url))

        logger.debug("Parsed %d reviews from %s", len(reviews), url)
        return [r.to_dict() for r in reviews]