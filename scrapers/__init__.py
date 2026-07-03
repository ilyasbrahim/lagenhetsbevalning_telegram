from scrapers.generic import GenericScraper
from scrapers.heimstaden import HeimstadenScraper
from scrapers.wallenstam import WallenstamScraper


SCRAPERS = {
    "generic": GenericScraper,
    "heimstaden": HeimstadenScraper,
    "wallenstam": WallenstamScraper,
}


def get_scraper(name):
    try:
        return SCRAPERS[name]
    except KeyError as exc:
        available = ", ".join(sorted(SCRAPERS))
        raise ValueError(f"Unknown scraper '{name}'. Available scrapers: {available}") from exc
