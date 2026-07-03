import logging

from scrapers.base import BaseScraper, clean_text


LOGGER = logging.getLogger(__name__)


class GenericScraper(BaseScraper):
    def fetch_listings(self):
        soup = self.get_soup()
        listings = []
        for link in soup.find_all("a", href=True):
            text = clean_text(link.get_text(" ", strip=True))
            if not text:
                continue
            if any(word in text.lower() for word in ("rok", "rum", "kr/mån", "kvm", "m²")):
                listings.append(
                    {
                        "title": text[:120],
                        "url": link["href"],
                        "raw_data": {"text": text},
                    }
                )
        LOGGER.info("Generic scraper hittade %s möjliga annonser", len(listings))
        return listings
