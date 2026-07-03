import logging
import re

from scrapers.base import BaseScraper, clean_text, parse_number


LOGGER = logging.getLogger(__name__)


class WallenstamScraper(BaseScraper):
    def scrape(self):
        listings = [self.normalize_listing(item) for item in self.fetch_listings()]
        LOGGER.debug("Wallenstam: antal annonser före filtrering: %s", len(listings))
        LOGGER.debug(
            "Wallenstam: trust_url_filters är %s",
            "aktivt" if self.check.get("trust_url_filters") else "inaktivt",
        )
        filtered = [item for item in listings if item and self.matches_filters(item)]
        LOGGER.debug("Wallenstam: antal annonser efter filtrering: %s", len(filtered))
        LOGGER.info("Efter lokal filtrering: %s annonser", len(filtered))
        return filtered

    def fetch_listings(self):
        soup = self.get_soup()
        listings = self._parse_listing_links(soup)
        if listings:
            LOGGER.info("Wallenstam: hittade %s annonser med requests", len(listings))
            return listings

        LOGGER.info("Wallenstam: inga annonser i statisk HTML, provar Playwright")
        soup = self.render_page()
        listings = self._parse_listing_links(soup)
        LOGGER.info("Wallenstam: hittade %s annonser med Playwright", len(listings))
        return listings

    def _parse_listing_links(self, soup):
        listings = []
        seen_urls = set()
        for link in soup.find_all("a", href=True):
            text = clean_text(link.get_text(" ", strip=True))
            if not self._looks_like_listing(text):
                continue
            href = link["href"]
            if href in seen_urls:
                continue
            seen_urls.add(href)
            listings.append(self._listing_from_text(text, href))
        return listings

    def matches_filters(self, listing):
        return self.filter_reason(listing) is None

    def filter_reason(self, listing):
        numeric_reason = self._numeric_filter_reason(listing)
        if numeric_reason:
            return numeric_reason

        if self.check.get("trust_url_filters"):
            LOGGER.debug(
                "Wallenstam: behåller %s eftersom trust_url_filters är aktivt",
                listing.get("title") or listing.get("address") or listing.get("url"),
            )
            return None

        areas = self.filters.get("areas") or []
        if areas:
            haystack = " ".join(
                str(listing.get(field) or "") for field in ("area", "address", "title")
            ).lower()
            if not any(str(area).lower() in haystack for area in areas):
                LOGGER.debug(
                    "Wallenstam: filtrerar bort %s eftersom area/stad inte matchar %s",
                    listing.get("title") or listing.get("address") or listing.get("url"),
                    areas,
                )
                return "area_mismatch"

        return None

    def _numeric_filter_reason(self, listing):
        max_rent = self.filters.get("max_rent")
        min_rooms = self.filters.get("min_rooms")
        allow_missing_rent = self.settings.get("allow_missing_rent", False)
        allow_missing_rooms = self.settings.get("allow_missing_rooms", False)
        label = listing.get("title") or listing.get("address") or listing.get("url")

        if max_rent is not None:
            rent = parse_number(listing.get("rent"))
            if rent is None:
                LOGGER.info("Wallenstam: annons saknar tolkningsbar hyra: %s", label)
                if not allow_missing_rent:
                    LOGGER.debug("Wallenstam: filtrerar bort %s eftersom hyra saknas", label)
                    return "missing_rent"
            elif rent > float(max_rent):
                LOGGER.debug(
                    "Wallenstam: filtrerar bort %s eftersom hyra %s är högre än max_rent %s",
                    label,
                    rent,
                    max_rent,
                )
                return "rent_too_high"

        if min_rooms is not None:
            rooms = parse_number(listing.get("rooms"))
            if rooms is None:
                LOGGER.info("Wallenstam: annons saknar tolkningsbart antal rum: %s", label)
                if not allow_missing_rooms:
                    LOGGER.debug("Wallenstam: filtrerar bort %s eftersom rum saknas", label)
                    return "missing_rooms"
            elif rooms < float(min_rooms):
                LOGGER.debug(
                    "Wallenstam: filtrerar bort %s eftersom rum %s är lägre än min_rooms %s",
                    label,
                    rooms,
                    min_rooms,
                )
                return "rooms_too_low"

        return None

    def _looks_like_listing(self, text):
        lower = text.lower()
        return "kr/mån" in lower and ("rok" in lower or "rum" in lower) and ("m²" in lower or "kvm" in lower)

    def _listing_from_text(self, text, href):
        rent = find_first(r"(\d[\d\s]*\s*kr/mån)", text)
        rooms = find_first(r"(\d+(?:[,.]\d+)?\s*rok)", text)
        size = find_first(r"(\d+(?:[,.]\d+)?\s*(?:m²|kvm))", text)
        area = find_first(r"(Stockholm,\s*[^0-9]+?)(?:\s+[A-ZÅÄÖ][^\d]+?\s+\d| Inflytt|$)", text)
        address = self._extract_address(text)
        title = " ".join(part for part in [rooms, address or area] if part) or text[:100]

        return {
            "title": title,
            "area": area,
            "rent": rent,
            "rooms": rooms,
            "size": size,
            "address": address,
            "url": href,
            "external_id": find_first(r"/(?:objekt|bostad|lagenhet)/([^/?#]+)", href),
            "raw_data": {"text": text},
        }

    def _extract_address(self, text):
        before_move_in = text.split(" Inflytt ", 1)[0]
        before_move_in = re.sub(r"^(Snabb inflytt|Nyproduktion)\s+", "", before_move_in)
        before_move_in = re.sub(r"^Stockholm,\s*[^0-9]+?\s+", "", before_move_in)
        match = re.search(r"([A-ZÅÄÖ][A-Za-zÅÄÖåäöéÉ .'-]+?\s+\d+[A-Za-z]?)$", before_move_in)
        return clean_text(match.group(1)) if match else ""


def find_first(pattern, text):
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""
