#!/usr/bin/env python3
"""Publish a validated snapshot of Revolut X EEA spot trading pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PAIRS_URL = "https://revx.revolut.com/api/1.0/public/configuration/pairs?region=EEA"
CURRENCIES_URL = "https://revx.revolut.com/api/1.0/public/configuration/currencies?region=EEA"
SCHEMA_VERSION = 1
MIN_PAIRS = 10
MIN_ASSETS = 10
MAX_DROP_FRACTION = 0.30
MAX_RESPONSE_BYTES = 5_000_000


class SnapshotError(Exception):
    """The source or resulting snapshot cannot safely replace the last good one."""


def fetch_json(url: str) -> object:
    """Fetch one public endpoint, respecting the documented millisecond Retry-After."""
    for attempt in range(3):
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "revx-radar-universe/1.0"})
            with urlopen(request, timeout=20) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SnapshotError(f"Response too large: {url}")
            try:
                return json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SnapshotError(f"Non-JSON response: {url}") from exc
        except HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                try:
                    wait_seconds = min(max(float(exc.headers.get("Retry-After", "1000")) / 1000, 1), 30)
                except ValueError:
                    wait_seconds = 2 ** attempt
                time.sleep(wait_seconds)
                continue
            if 500 <= exc.code < 600 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise SnapshotError(f"HTTP {exc.code}: {url}") from exc
        except (TimeoutError, URLError) as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise SnapshotError(f"Network failure: {url}: {exc}") from exc
    raise SnapshotError(f"Could not fetch: {url}")


def _require_map(value: object, label: str) -> dict:
    if not isinstance(value, dict) or not value:
        raise SnapshotError(f"{label} must be a non-empty object")
    return value


def build_snapshot(
    source_pairs: object,
    source_currencies: object,
    previous: dict | None = None,
    *,
    min_pairs: int = MIN_PAIRS,
    min_assets: int = MIN_ASSETS,
) -> dict:
    """Validate both source maps before building the public, compact snapshot."""
    pair_data = _require_map(source_pairs, "Pairs")
    currency_data = _require_map(source_currencies, "Currencies")
    if len(pair_data) < min_pairs:
        raise SnapshotError(f"Only {len(pair_data)} pairs returned")

    currencies: dict[str, dict] = {}
    for symbol, entry in currency_data.items():
        if (
            not isinstance(symbol, str)
            or not isinstance(entry, dict)
            or entry.get("symbol") != symbol
            or not isinstance(entry.get("asset_type"), str)
            or not isinstance(entry.get("status"), str)
        ):
            details = (
                f"keys={sorted(entry) if isinstance(entry, dict) else type(entry).__name__}, "
                f"symbol={entry.get('symbol')!r}, asset_type={entry.get('asset_type')!r}, "
                f"status={entry.get('status')!r}"
                if isinstance(entry, dict) else type(entry).__name__
            )
            raise SnapshotError(f"Invalid currency entry: {symbol!r} ({details})")
        currencies[symbol] = entry

    pairs: dict[str, dict[str, str]] = {}
    active_pairs: list[str] = []
    assets: set[str] = set()
    for symbol, entry in pair_data.items():
        if not isinstance(symbol, str) or not isinstance(entry, dict):
            raise SnapshotError(f"Invalid pair entry: {symbol!r}")
        base, quote, status = (entry.get(field) for field in ("base", "quote", "status"))
        if (
            not all(isinstance(value, str) and value for value in (base, quote, status))
            or symbol != f"{base}/{quote}"
        ):
            raise SnapshotError(f"Invalid pair schema: {symbol!r}")
        pairs[symbol] = {"base": base, "quote": quote, "status": status}
        if status != "active":
            continue
        if base not in currencies or quote not in currencies:
            raise SnapshotError(f"Active pair has missing currency: {symbol}")
        if currencies[base]["status"] != "active" or currencies[quote]["status"] != "active":
            raise SnapshotError(f"Active pair conflicts with currency status: {symbol}")
        if currencies[base]["asset_type"] == "crypto":
            active_pairs.append(symbol)
            assets.add(base)

    if len(assets) < min_assets:
        raise SnapshotError(f"Only {len(assets)} active crypto assets returned")

    if previous is not None:
        if previous.get("schema_version") != SCHEMA_VERSION or previous.get("region") != "EEA":
            raise SnapshotError("Existing snapshot has unexpected version or region")
        for field, current in (("pair_count", len(pairs)), ("active_pair_count", len(active_pairs)), ("asset_count", len(assets))):
            old = previous.get(field)
            if not isinstance(old, int) or old < 0:
                raise SnapshotError(f"Existing snapshot has invalid {field}")
            if old and current < old * (1 - MAX_DROP_FRACTION):
                raise SnapshotError(f"Suspicious {field} drop: {old} -> {current}")

    core = {
        "region": "EEA",
        "assets": sorted(assets),
        "active_pairs": sorted(active_pairs),
        "pairs": dict(sorted(pairs.items())),
    }
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "region": "EEA",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": {"pairs": PAIRS_URL, "currencies": CURRENCIES_URL},
        "pair_count": len(pairs),
        "active_pair_count": len(active_pairs),
        "asset_count": len(assets),
        "assets": core["assets"],
        "active_pairs": core["active_pairs"],
        "pairs": core["pairs"],
        "content_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def write_atomic(path: Path, snapshot: dict) -> None:
    """Replace the snapshot only after fetching and validating both sources."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".pairs-", suffix=".tmp", delete=False) as file:
            temporary = file.name
            json.dump(snapshot, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("pairs.json"))
    args = parser.parse_args()
    try:
        previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else None
        if previous is not None and not isinstance(previous, dict):
            raise SnapshotError("Existing snapshot is not an object")
        pairs = fetch_json(PAIRS_URL)
        time.sleep(1.1)
        currencies = fetch_json(CURRENCIES_URL)
        snapshot = build_snapshot(pairs, currencies, previous)
        write_atomic(args.output, snapshot)
    except (SnapshotError, OSError, json.JSONDecodeError) as exc:
        print(f"Refresh failed; last good snapshot preserved: {exc}", file=sys.stderr)
        return 1
    print(f"Updated {args.output}: {snapshot['active_pair_count']} active crypto pairs, {snapshot['asset_count']} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
