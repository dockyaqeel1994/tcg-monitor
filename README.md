# TCG monitor

Watches ~20 South African (and Kai of Cards) One Piece TCG store pages and emails you when something
is **new**, **back in stock**, or has a **price drop**.

- **Sealed only:** alerts are for sealed One Piece product you can buy now (booster boxes, packs,
  double packs, premium collections...). Singles, starter decks/deck sets, non-English (Japanese,
  Chinese...) product, accessories and sold-out listings are ignored.
  Booster boxes are listed first and preorders are labelled `PREORDER`.
- **Price comparison:** each sealed alert lists the same product's price at the other tracked
  shops (in stock first, then sold out), marking the cheapest in stock with ✅.
- **Kai of Cards (priority):** only the preorder page is watched, every 30 minutes, by its own
  workflow, and it only posts order alerts (no other Kai noise). When a One Piece **booster box** is available, you get an "ORDER NOW" email
  with a ready-to-send order to Kai (email + WhatsApp), e.g. "I'd like to preorder 1 x EB05 Case
  (12 boxes)". Change the wording with the `ORDER_ITEM` variable. One alert
  per set, ever (tracked in `state/_kai_orders.json`; delete a set's entry to be alerted again).
  Nothing is sent to Kai automatically - you tap send.
- A store that fails 3 runs in a row triggers a "check the parser" alert. A failing store never
  overwrites its saved state, so one bad run can't cause a flood of fake "new" alerts.
- The first run of each store only saves a baseline (no alerts).

## Setup (GitHub Actions, free)
1. Push this folder to a **private** repo.
2. Settings -> Secrets and variables -> Actions -> add secrets: `SMTP_HOST` (smtp.gmail.com),
   `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS` (Gmail app password), `MAIL_TO`,
   `ORDER_NAME`, `ORDER_PHONE`, `ORDER_ADDRESS`. Optional variable: `SINGLES_MIN`.
   **Discord instead of / as well as email:** in a Discord channel, Edit Channel -> Integrations ->
   Webhooks -> New Webhook -> Copy Webhook URL, and save it as the secret `DISCORD_WEBHOOK`.
   To check alerts arrive: Actions -> "TCG monitor (Kai preorders)" -> Run workflow -> tick
   "Only send a sample alert".
3. Actions tab -> run each workflow once (baseline), then again (should say "no alerts").

Schedules (private repo, fits 2000 free Actions min/month): Kai every 30 min, other stores every
6 h, browser stores daily. A public repo has unlimited minutes, so these can be much faster.

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
- AI Fest, Tanuki Trader and Marvellous Hobbies block GitHub's servers (Cloudflare/403) and are
  commented out in `sites.py`.
- HTML parsers (WooCommerce, PrestaShop, CG Haven, Untapped Potential, Mirage) were tested
  against sample markup only, not live pages. Check each with `--dry-run` first.
- Mirage Gaming has no One Piece category URL in the config, so it scans `/shop` and filters by name.
- Card Cache is pinned to the OP17 collection; new sets need a new entry.
- Stock status on HTML stores is inferred from "sold out"/"out of stock" text.
