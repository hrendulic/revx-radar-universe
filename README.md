# Revolut X Radar — EEA universe cache

Daily, fail-safe snapshot of public Revolut X EEA trading-pair configuration. This repository contains the **eligible universe only**. It does not provide 5-minute signals or trading advice, and it never places orders.

Source: [Revolut X public API documentation](https://developer.revolut.com/docs/api/revolut-x-crypto-exchange).

## Refresh

GitHub Actions runs daily at 03:17 UTC and can also be started manually with **Actions → Refresh Revolut X EEA universe → Run workflow**. It reads both public configuration endpoints without API keys:

- `GET /api/1.0/public/configuration/pairs?region=EEA`
- `GET /api/1.0/public/configuration/currencies?region=EEA`

The second request starts at least one second after the first. On invalid JSON, empty data, an inconsistent schema, or a drop exceeding 30% from the previous snapshot, the Action fails without modifying `pairs.json`. The first successful run requires at least 10 pairs and 10 active crypto assets; its counts should be checked against Revolut X before relying on the snapshot.

## Output

`pairs.json` is created only after a successful first run. `pairs` preserves the returned status of every pair. `active_pairs` contains only active pairs with an active crypto base and active quote currency. `assets` is the sorted list of their unique base assets. `pair_count` counts all returned pairs; `active_pair_count` counts the eligible active crypto pairs. `updated_at` changes only on successful fetches, and `content_sha256` fingerprints the normalized universe data.

This daily snapshot can become stale between updates. A trading signal must check the live pair status again before marking an asset actionable, and must treat a snapshot older than 48 hours as stale.

## Local check

```bash
python -m unittest discover -s tests -v
python fetch_pairs.py --output pairs.json
```

Python 3.12 standard library only. No API keys or account credentials are used.
