# TCG monitor

Watches ~20 South African (and Kai of Cards) One Piece TCG store pages and emails you when something
is **new**, **back in stock**, or has a **price drop**.

- **Sealed** (boxes, cases, packs, decks) always alerts.
- **Singles** only alert at or above `SINGLES_MIN` (default R500), so cheap commons don't flood you.
- Kai of Cards alerts include a pre-filled order email + WhatsApp link (quantity defaults to 2).
- A store that fails 3 runs in a row triggers a "check the parser" alert. A failing store never
  overwrites its saved state, so one bad run can't cause a flood of fake "new" alerts.
- The first run of each store only saves a baseline (no alerts).

## Setup (GitHub Actions, free)
1. Push this folder to a **private** repo.
2. Settings -> Secrets and variables -> Actions -> add secrets: `SMTP_HOST` (smtp.gmail.com),
   `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS` (Gmail app password), `MAIL_TO`,
   `ORDER_NAME`, `ORDER_PHONE`, `ORDER_ADDRESS`. Optional variable: `SINGLES_MIN`.
3. Actions tab -> run both workflows once (baseline), then again (should say "no alerts").

## Run locally
```
pip install -r requirements.txt
python monitor.py --list
python monitor.py --site "Geek Home" --dry-run     # test one store, nothing saved or emailed
python tests/test_monitor.py                        # offline parser tests
```
JS stores (Untapped Potential, Mirage) need `pip install -r requirements-js.txt && playwright install chromium`.

## Adding / fixing a store
Edit `sites.py`. Shopify stores just need the collection URL. If a store's alerts look wrong,
run it with `--dry-run` and compare against the site; the HTML parsers are in `monitor.py`
(`parse_woo`, `parse_presta`, `parse_generic`).

## Known weak spots
- HTML parsers (WooCommerce, PrestaShop, CG Haven, Untapped Potential, Mirage) were tested
  against sample markup only, not live pages. Check each with `--dry-run` first.
- Mirage Gaming has no One Piece category URL in the config, so it scans `/shop` and filters by name.
- Card Cache is pinned to the OP17 collection; new sets need a new entry.
- Stock status on HTML stores is inferred from "sold out"/"out of stock" text.
