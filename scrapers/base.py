import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger(__name__)


class BaseScraper(ABC):
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 housing-alerts/1.0"
    )

    def __init__(self, check, settings=None):
        self.check = check
        self.settings = settings or {}
        self.url = check["url"]
        self.filters = check.get("filters") or {}

    @abstractmethod
    def fetch_listings(self):
        raise NotImplementedError

    def scrape(self):
        listings = [self.normalize_listing(item) for item in self.fetch_listings()]
        listings = [item for item in listings if item and self.matches_filters(item)]
        LOGGER.info("Efter lokal filtrering: %s annonser", len(listings))
        return listings

    def get_soup(self, url=None):
        response = requests.get(
            url or self.url,
            headers={"User-Agent": self.user_agent, "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"},
            timeout=30,
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    def render_page(self, url=None, wait_for_ms=3000):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.settings.get("headless", True))
            page = browser.new_page(user_agent=self.user_agent)
            page.goto(url or self.url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(wait_for_ms)
            html = page.content()
            browser.close()
        return BeautifulSoup(html, "html.parser")

    def collect_json_responses(self, url=None, wait_for_ms=5000):
        payloads = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.settings.get("headless", True))
            page = browser.new_page(user_agent=self.user_agent)

            def on_response(response):
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type.lower():
                    return
                try:
                    payloads.append({"url": response.url, "data": response.json()})
                except Exception as exc:
                    LOGGER.debug("Kunde inte läsa JSON från %s: %s", response.url, exc)

            page.on("response", on_response)
            page.goto(url or self.url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(wait_for_ms)
            browser.close()
        return payloads

    def normalize_listing(self, listing):
        normalized = {
            "title": clean_text(listing.get("title")),
            "district": clean_text(listing.get("district")),
            "area": clean_text(listing.get("area")),
            "rent": clean_text(listing.get("rent")),
            "rooms": clean_text(listing.get("rooms")),
            "size": clean_text(listing.get("size")),
            "address": clean_text(listing.get("address")),
            "url": urljoin(self.url, listing.get("url") or "") if listing.get("url") else "",
            "external_id": clean_text(listing.get("external_id")),
            "raw_data": listing.get("raw_data") or {},
        }
        if not normalized["title"] and normalized["address"]:
            normalized["title"] = normalized["address"]
        return normalized

    def matches_filters(self, listing):
        return self.filter_reason(listing) is None

    def filter_reason(self, listing):
        max_rent = self.filters.get("max_rent")
        min_rooms = self.filters.get("min_rooms")
        areas = self.filters.get("areas") or []
        allow_missing_rent = self.settings.get("allow_missing_rent", False)
        allow_missing_rooms = self.settings.get("allow_missing_rooms", False)

        if max_rent is not None:
            rent = parse_number(listing.get("rent"))
            if rent is None:
                LOGGER.info("Annons saknar tolkningsbar hyra: %s", listing.get("title"))
                if not allow_missing_rent:
                    return "missing_rent"
            elif rent > float(max_rent):
                return "rent_too_high"

        if min_rooms is not None:
            rooms = parse_number(listing.get("rooms"))
            if rooms is None:
                LOGGER.info("Annons saknar tolkningsbart antal rum: %s", listing.get("title"))
                if not allow_missing_rooms:
                    return "missing_rooms"
            elif rooms < float(min_rooms):
                return "rooms_too_low"

        if areas:
            haystack = " ".join(
                str(listing.get(field) or "") for field in ("area", "address", "title")
            ).lower()
            if not any(str(area).lower() in haystack for area in areas):
                return "area_mismatch"

        return None


def clean_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_number(value):
    if value is None:
        return None
    text = str(value).replace("\xa0", " ")
    match = re.search(r"(\d+(?:[ ,.]\d+)?)", text)
    if not match:
        return None
    number = match.group(1).replace(" ", "").replace(",", ".")
    if number.count(".") > 1:
        number = number.replace(".", "")
    try:
        return float(number)
    except ValueError:
        return None


def content_hash(listing):
    parts = [
        listing.get("title"),
        listing.get("district"),
        listing.get("area"),
        listing.get("rent"),
        listing.get("rooms"),
        listing.get("size"),
        listing.get("address"),
    ]
    text = "|".join(clean_text(part).lower() for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
