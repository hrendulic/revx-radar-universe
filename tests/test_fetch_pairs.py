import json
import tempfile
import unittest
from pathlib import Path

from fetch_pairs import SnapshotError, build_snapshot, write_atomic


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.pairs = {
            "BTC/USD": {"base": "BTC", "quote": "USD", "status": "active"},
            "ETH/EUR": {"base": "ETH", "quote": "EUR", "status": "paused"},
            "OLD/USD": {"base": "OLD", "quote": "USD", "status": "inactive"},
        }
        self.currencies = {
            symbol: {"symbol": symbol, "asset_type": "crypto" if symbol in ("BTC", "ETH", "OLD") else "fiat", "status": "active"}
            for symbol in ("BTC", "ETH", "OLD", "USD", "EUR")
        }

    def snapshot(self, pairs=None, previous=None):
        return build_snapshot(pairs if pairs is not None else self.pairs, self.currencies, previous, min_pairs=1, min_assets=1)

    def test_only_active_crypto_bases_enter_universe_and_statuses_survive(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["assets"], ["BTC"])
        self.assertEqual(snapshot["active_pairs"], ["BTC/USD"])
        self.assertEqual(snapshot["pair_count"], 3)
        self.assertEqual(snapshot["pairs"]["ETH/EUR"]["status"], "paused")

    def test_malformed_or_suddenly_empty_feed_cannot_replace_last_good_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pairs.json"
            original = self.snapshot()
            write_atomic(path, original)
            before = path.read_bytes()
            with self.assertRaises(SnapshotError):
                replacement = self.snapshot({}, original)
                write_atomic(path, replacement)
            self.assertEqual(path.read_bytes(), before)

    def test_large_drop_is_quarantined(self):
        previous = self.snapshot()
        previous["pair_count"] = 30
        with self.assertRaisesRegex(SnapshotError, "Suspicious pair_count drop"):
            self.snapshot(previous=previous)

    def test_pair_key_must_match_base_and_quote(self):
        malformed = {"BTC/USD": {"base": "ETH", "quote": "USD", "status": "active"}}
        with self.assertRaisesRegex(SnapshotError, "Invalid pair schema"):
            self.snapshot(malformed)


if __name__ == "__main__":
    unittest.main()
