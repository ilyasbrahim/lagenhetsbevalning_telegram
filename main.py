import argparse
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from db import connect, init_db, is_seen, reset_db, upsert_listing
from notifier import TelegramNotifier
from scrapers import get_scraper


CONFIG_PATH = Path("config.json")
LOGGER = logging.getLogger(__name__)


def setup_logging(debug=False):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if debug:
        logging.getLogger("urllib3").setLevel(logging.INFO)


def load_config():
    with CONFIG_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def parse_args():
    parser = argparse.ArgumentParser(description="Bevaka offentliga bostadsannonser.")
    parser.add_argument("--dry-run", action="store_true", help="Hämta och utvärdera annonser utan DB-skrivning eller Telegram.")
    parser.add_argument("--reset-db", action="store_true", help="Rensa data/listings.db innan körning.")
    return parser.parse_args()


def normalize_all(scraper):
    return [scraper.normalize_listing(item) for item in scraper.fetch_listings()]


def run_check(connection, notifier, check, settings, dry_run=False):
    source_name = check["name"]
    logging.info("Kontrollerar %s", source_name)
    scraper_class = get_scraper(check.get("scraper", "generic"))
    scraper = scraper_class(check, settings)
    found_listings = normalize_all(scraper)
    debug_filtering = settings.get("debug_filtering", False)

    if not found_listings:
        logging.info("%s: inga annonser hittades", source_name)
        return

    seen_this_run = set()
    matched_count = 0
    would_send_count = 0
    sent_count = 0
    not_sent_reasons = {}

    for listing in found_listings:
        filter_reason = scraper.filter_reason(listing)
        if filter_reason:
            log_listing_decision(source_name, listing, False, filter_reason, debug_filtering)
            not_sent_reasons[filter_reason] = not_sent_reasons.get(filter_reason, 0) + 1
            continue

        matched_count += 1
        run_key = listing_key(listing)
        if run_key in seen_this_run:
            reason = "duplicate_in_same_run"
            log_listing_decision(source_name, listing, False, reason, debug_filtering)
            not_sent_reasons[reason] = not_sent_reasons.get(reason, 0) + 1
            continue
        seen_this_run.add(run_key)

        if not is_valid_listing_url(listing):
            reason = "invalid_url"
            log_listing_decision(source_name, listing, False, reason, debug_filtering)
            not_sent_reasons[reason] = not_sent_reasons.get(reason, 0) + 1
            continue

        if connection is not None and is_seen(connection, source_name, listing):
            reason = "already_seen_in_database"
            log_listing_decision(source_name, listing, False, reason, debug_filtering)
            not_sent_reasons[reason] = not_sent_reasons.get(reason, 0) + 1
            continue

        would_send_count += 1
        if dry_run:
            log_listing_decision(source_name, listing, True, "dry_run_would_send", debug_filtering)
            continue

        if notifier.send_listing(source_name, listing):
            upsert_listing(connection, source_name, listing)
            sent_count += 1
            log_listing_decision(source_name, listing, True, "sent", debug_filtering)
        else:
            reason = "telegram_failed"
            log_listing_decision(source_name, listing, False, reason, debug_filtering)
            not_sent_reasons[reason] = not_sent_reasons.get(reason, 0) + 1

    if dry_run:
        logging.info(
            "%s dry-run: %s hittade, %s matchade filter, %s skulle skickas, reasons=%s",
            source_name,
            len(found_listings),
            matched_count,
            would_send_count,
            not_sent_reasons,
        )
        return

    logging.info(
        "%s: %s hittade, %s matchade filter, %s skickade, reasons=%s",
        source_name,
        len(found_listings),
        matched_count,
        sent_count,
        not_sent_reasons,
    )


def listing_key(listing):
    return listing.get("external_id") or listing.get("url") or "|".join(
        str(listing.get(field) or "") for field in ("title", "area", "rent", "rooms", "size", "address")
    )


def is_valid_listing_url(listing):
    url = listing.get("url")
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def log_listing_decision(source_name, listing, sent, reason, debug_filtering):
    if not debug_filtering:
        return
    LOGGER.debug(
        "listing_decision source=%r title=%r address=%r area=%r district=%r rent=%r rooms=%r url=%r sent=%s reason=%s",
        source_name,
        listing.get("title"),
        listing.get("address"),
        listing.get("area"),
        listing.get("district"),
        listing.get("rent"),
        listing.get("rooms"),
        listing.get("url"),
        sent,
        reason,
    )


def main():
    args = parse_args()
    config = load_config()
    settings = config.get("settings") or {}
    setup_logging(debug=settings.get("debug_filtering", False))
    notifier = TelegramNotifier()

    with connect() as connection:
        init_db(connection)
        if args.reset_db:
            reset_db(connection)
            logging.info("Databasen är rensad.")

        if settings.get("send_test_message_on_start") and not args.dry_run:
            notifier.send_test_message()

        enabled_checks = [check for check in config.get("checks", []) if check.get("enabled", True)]
        for index, check in enumerate(enabled_checks):
            try:
                run_check(connection, notifier, check, settings, dry_run=args.dry_run)
            except Exception as exc:
                logging.error("%s misslyckades, fortsätter med nästa källa: %s", check.get("name"), exc)
                if settings.get("debug_tracebacks", False):
                    LOGGER.debug("Traceback för %s", check.get("name"), exc_info=True)

            if index < len(enabled_checks) - 1:
                delay = int(settings.get("delay_seconds_between_checks", 5))
                logging.info("Väntar %s sekunder innan nästa kontroll", delay)
                time.sleep(delay)


if __name__ == "__main__":
    main()
