import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scrapers.base import content_hash


DB_PATH = Path("data/listings.db")


def connect(db_path=DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            title TEXT,
            district TEXT,
            area TEXT,
            rent TEXT,
            rooms TEXT,
            size TEXT,
            address TEXT,
            url TEXT,
            external_id TEXT,
            content_hash TEXT NOT NULL,
            identity_key TEXT NOT NULL UNIQUE,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            raw_data TEXT
        )
        """
    )
    ensure_column(connection, "listings", "district", "TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source_name)")
    connection.commit()


def ensure_column(connection, table, column, definition):
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def reset_db(connection):
    init_db(connection)
    connection.execute("DELETE FROM listings")
    connection.commit()


def is_seen(connection, source_name, listing):
    item_hash = content_hash(listing)
    identity_key = make_identity_key(source_name, listing, item_hash)
    existing = connection.execute(
        "SELECT id FROM listings WHERE identity_key = ?",
        (identity_key,),
    ).fetchone()
    return existing is not None


def upsert_listing(connection, source_name, listing):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    item_hash = content_hash(listing)
    identity_key = make_identity_key(source_name, listing, item_hash)

    existing = connection.execute(
        "SELECT id FROM listings WHERE identity_key = ?",
        (identity_key,),
    ).fetchone()

    raw_data = json.dumps(listing.get("raw_data") or {}, ensure_ascii=False, sort_keys=True)
    if existing:
        connection.execute(
            """
            UPDATE listings
            SET title = ?, district = ?, area = ?, rent = ?, rooms = ?, size = ?, address = ?,
                url = ?, external_id = ?, content_hash = ?, last_seen_at = ?, raw_data = ?
            WHERE identity_key = ?
            """,
            (
                listing.get("title"),
                listing.get("district"),
                listing.get("area"),
                listing.get("rent"),
                listing.get("rooms"),
                listing.get("size"),
                listing.get("address"),
                listing.get("url"),
                listing.get("external_id"),
                item_hash,
                now,
                raw_data,
                identity_key,
            ),
        )
        connection.commit()
        return False

    connection.execute(
        """
        INSERT INTO listings (
            source_name, title, district, area, rent, rooms, size, address, url, external_id,
            content_hash, identity_key, first_seen_at, last_seen_at, raw_data
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_name,
            listing.get("title"),
            listing.get("district"),
            listing.get("area"),
            listing.get("rent"),
            listing.get("rooms"),
            listing.get("size"),
            listing.get("address"),
            listing.get("url"),
            listing.get("external_id"),
            item_hash,
            identity_key,
            now,
            now,
            raw_data,
        ),
    )
    connection.commit()
    return True


def make_identity_key(source_name, listing, item_hash):
    if listing.get("external_id"):
        return f"{source_name}:external:{listing['external_id']}"
    if listing.get("url"):
        return f"{source_name}:url:{listing['url']}"
    return f"{source_name}:hash:{item_hash}"
