import json
import logging
import re
import time
from collections import Counter
from urllib.parse import urljoin

import requests

from scrapers.base import BaseScraper, clean_text, parse_number


LOGGER = logging.getLogger(__name__)


class HeimstadenScraper(BaseScraper):
    district_guid_map = {
        "Stor-Stockholm": "0b24f94a-1fa6-49fe-9a69-2f68b15cb519",
        "Stor-Malmö": "7bf6e9c3-38cc-4399-b370-95b888889ef5",
        "Övriga landet": "fb3f9d3b-0f3b-4730-b111-28d409e8f46d",
    }
    district_raw_fields = (
        "district",
        "districtName",
        "District",
        "DistrictName",
        "region",
        "regionName",
        "Region",
        "RegionName",
        "market",
        "marketName",
        "Market",
        "MarketName",
    )

    candidate_api_paths = (
        "/rentalobject/Listapartment/published?sortOrder=&timestamp={timestamp}",
        "/api/objects",
        "/api/availableobjects",
        "/api/v1/objects",
        "/api/v1/availableobjects",
        "/umbraco/api/availableobjects/get",
    )

    def fetch_listings(self):
        self._api_network_error = False
        listings = self._fetch_from_candidate_apis()
        if listings:
            LOGGER.info("Heimstaden: totalt hämtade från API: %s", len(listings))
            self.log_district_summary(listings)
            return listings

        if self._api_network_error:
            LOGGER.warning(
                "Heimstaden: API kunde inte nås på grund av nätverks-/DNS-fel. "
                "Hoppar över Playwright-fallback eftersom samma nätverksfel sannolikt skulle stoppa sidan."
            )
            return []

        try:
            listings = self._fetch_from_browser_json()
            if listings:
                LOGGER.info("Heimstaden: hittade %s annonser via JSON-anrop i browser", len(listings))
                return listings
        except Exception as exc:
            LOGGER.warning("Heimstaden: kunde inte samla JSON-anrop med Playwright: %s", exc)

        LOGGER.info("Heimstaden: provar renderad HTML med Playwright")
        soup = self.render_page()
        listings = self._parse_rendered_html(soup)
        LOGGER.info("Heimstaden: hittade %s annonser i renderad HTML", len(listings))
        return listings

    def _fetch_from_candidate_apis(self):
        listings = []
        network_errors = 0
        session = requests.Session()
        session.headers.update({"User-Agent": self.user_agent, "Accept": "application/json"})
        for path in self.candidate_api_paths:
            api_url = urljoin(self.url, path.format(timestamp=int(time.time() * 1000)))
            try:
                response = session.get(api_url, timeout=20)
                if response.status_code >= 400 or "json" not in response.headers.get("content-type", ""):
                    continue
                extracted = self._extract_listings_from_json(response.json(), api_url)
                listings.extend(extracted)
            except requests.exceptions.RequestException as exc:
                network_errors += 1
                LOGGER.debug("Heimstaden kandidat-API nätverksfel %s: %s", api_url, exc)
            except Exception as exc:
                LOGGER.debug("Heimstaden kandidat-API misslyckades %s: %s", api_url, exc)
        self._api_network_error = network_errors == len(self.candidate_api_paths)
        return dedupe_by_url_or_id(listings)

    def log_district_summary(self, listings):
        counts = Counter((listing.get("district") or "Saknar district") for listing in listings)
        LOGGER.info("Heimstaden: antal per district: %s", dict(sorted(counts.items())))

        missing = [listing for listing in listings if not listing.get("district")]
        if missing:
            LOGGER.warning(
                "Heimstaden: %s annonser saknar district. Visar raw_data-sample för upp till 3 annonser.",
                len(missing),
            )
            for listing in missing[:3]:
                LOGGER.warning(
                    "Heimstaden raw_data sample: %s",
                    json.dumps(relevant_raw_fields(listing.get("raw_data") or {}), ensure_ascii=False, sort_keys=True),
                )

    def _fetch_from_browser_json(self):
        listings = []
        for payload in self.collect_json_responses():
            extracted = self._extract_listings_from_json(payload["data"], payload["url"])
            if extracted:
                LOGGER.info("Heimstaden: använder publikt JSON-anrop %s", payload["url"])
                listings.extend(extracted)
        return dedupe_by_url_or_id(listings)

    def _extract_listings_from_json(self, data, source_url):
        candidates = []
        self._walk_json(data, candidates)
        listings = []
        for item in candidates:
            listing = self._listing_from_json_item(item, source_url)
            if listing:
                listings.append(listing)
        return listings

    def _walk_json(self, node, candidates):
        if isinstance(node, dict):
            if isinstance(node.get("data"), str):
                try:
                    self._walk_json(json.loads(node["data"]), candidates)
                except json.JSONDecodeError:
                    pass
            keys = {key.lower() for key in node}
            if keys & {
                "rent",
                "hyra",
                "monthlyrent",
                "rooms",
                "roomcount",
                "noofrooms",
                "area",
                "areaname",
                "address",
                "adress1",
                "street",
                "cost",
            }:
                candidates.append(node)
            for value in node.values():
                self._walk_json(value, candidates)
        elif isinstance(node, list):
            for value in node:
                self._walk_json(value, candidates)

    def _listing_from_json_item(self, item, source_url):
        title = first_value(item, "title", "name", "heading", "objectName", "propertyName", "ObjectTypeName")
        address = first_value(item, "address", "street", "streetAddress", "visitingAddress", "Adress1")
        area = first_value(item, "area", "AreaName", "city", "Adress3", "district", "municipality", "location")
        city = first_value(item, "city", "City", "municipality", "Municipality", "Adress3")
        rent = first_value(item, "rent", "hyra", "monthlyRent", "price", "Cost", "TotalCost")
        rooms = first_value(item, "rooms", "roomCount", "numberOfRooms", "NoOfRooms")
        size = first_value(item, "size", "livingArea", "areaSize", "sqm", "Size")
        href = first_value(item, "url", "href", "link", "DetailsUrl", "ExternalUrl")
        external_id = first_value(item, "id", "objectId", "apartmentId", "reference", "Guid", "ExternalId")
        district = self._district_from_item(item)

        if not any([title, address, rent, rooms, size, href, external_id]):
            return None

        return {
            "title": title or address or area,
            "area": area,
            "city": city,
            "rent": format_rent(rent),
            "rooms": format_rooms(rooms),
            "size": format_size(size),
            "address": address,
            "url": urljoin(source_url, str(href)) if href else self.url,
            "external_id": external_id,
            "district": district,
            "raw_data": item,
        }

    def normalize_listing(self, listing):
        normalized = super().normalize_listing(listing)
        normalized["district"] = clean_text(listing.get("district"))
        normalized["city"] = clean_text(listing.get("city"))
        return normalized

    def matches_filters(self, listing):
        return self.filter_reason(listing) is None

    def filter_reason(self, listing):
        district = self._listing_district(listing)
        LOGGER.debug(
            "Heimstaden-annons %s har distrikt: %s",
            listing.get("external_id") or listing.get("title") or listing.get("address"),
            district or "-",
        )

        districts = self.filters.get("districts") or []
        if districts:
            if not self._district_matches(listing, districts):
                LOGGER.debug(
                    "Heimstaden filtrerar bort: title=%r address=%r district=%r area=%r city=%r rent=%r rooms=%r reason=district_mismatch",
                    listing.get("title"),
                    listing.get("address"),
                    district,
                    listing.get("area"),
                    self._listing_city(listing),
                    listing.get("rent"),
                    listing.get("rooms"),
                )
                return "district_mismatch"
            return self._numeric_filter_reason(listing)

        numeric_reason = self._numeric_filter_reason(listing)
        if numeric_reason:
            return numeric_reason

        areas = self.filters.get("areas") or []
        if areas:
            haystack = " ".join(
                str(listing.get(field) or "") for field in ("area", "address", "title", "district")
            ).lower()
            for area in areas:
                needle = str(area).lower()
                if needle in haystack:
                    return None
                if needle == "stockholm" and district == "Stor-Stockholm":
                    return None
            return "area_mismatch"

        return None

    def _numeric_filter_reason(self, listing):
        max_rent = self.filters.get("max_rent")
        min_rooms = self.filters.get("min_rooms")
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

        return None

    def _listing_district(self, listing):
        return listing.get("district") or self._district_from_item(listing.get("raw_data") or {})

    def _listing_city(self, listing):
        return listing.get("city") or first_value(listing.get("raw_data") or {}, "city", "City", "Adress3")

    def _district_matches(self, listing, districts):
        wanted = {normalize_match_value(district) for district in districts}
        candidates = []
        if listing.get("district"):
            candidates.append(listing["district"])

        raw_data = listing.get("raw_data") or {}
        for field in self.district_raw_fields:
            value = first_value(raw_data, field)
            if value:
                candidates.append(value)

        derived = self._district_from_item(raw_data)
        if derived:
            candidates.append(derived)

        normalized_candidates = {normalize_match_value(candidate) for candidate in candidates}
        return bool(wanted & normalized_candidates)

    def _district_from_item(self, item):
        explicit = first_value(item, *self.district_raw_fields)
        if explicit:
            return clean_text(explicit)

        parent_area_guids = item.get("ParentAreaGuids") or item.get("parentAreaGuids") or []
        for district, guid in self.district_guid_map.items():
            if guid in parent_area_guids:
                return district
        return ""

    def _parse_rendered_html(self, soup):
        listings = []
        for link in soup.find_all("a", href=True):
            text = clean_text(link.get_text(" ", strip=True))
            lower = text.lower()
            if not text or not any(word in lower for word in ("kr", "hyra", "rum", "rok", "m²", "kvm")):
                continue
            listings.append(
                {
                    "title": text[:120],
                    "url": link["href"],
                    "raw_data": {"text": text},
                }
            )
        return dedupe_by_url_or_id(listings)


def first_value(item, *keys):
    lower_map = {str(key).lower(): value for key, value in item.items()}
    for key in keys:
        value = lower_map.get(key.lower())
        if value not in (None, "", []):
            return value
    return ""


def normalize_match_value(value):
    return re.sub(r"[^a-z0-9åäö]+", " ", clean_text(value).lower()).strip()


def relevant_raw_fields(item):
    fields = [
        "district",
        "districtName",
        "District",
        "DistrictName",
        "region",
        "regionName",
        "Region",
        "RegionName",
        "market",
        "marketName",
        "Market",
        "MarketName",
        "AreaName",
        "AreaGuid",
        "ParentAreaGuids",
        "Adress1",
        "Adress2",
        "Adress3",
        "Address1Flatno",
    ]
    return {field: item.get(field) for field in fields if field in item}


def format_rent(value):
    if value in (None, ""):
        return ""
    return f"{value} kr/mån" if isinstance(value, (int, float)) else value


def format_rooms(value):
    if value in (None, ""):
        return ""
    return f"{value} rum" if isinstance(value, (int, float)) else value


def format_size(value):
    if value in (None, ""):
        return ""
    return f"{value} kvm" if isinstance(value, (int, float)) else value


def dedupe_by_url_or_id(listings):
    seen = set()
    deduped = []
    for listing in listings:
        key = listing.get("external_id") or listing.get("url") or str(listing.get("raw_data"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(listing)
    return deduped
