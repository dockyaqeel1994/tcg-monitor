"""Store configuration.

platform:
  shopify      reads <collection>/products.json (no HTML parsing, fastest + most reliable)
  woocommerce  parses the product grid HTML, paginates with /page/N/
  prestashop   parses the product grid HTML, paginates with ?page=N
  html         generic parser for server-rendered custom shops (link_re = product URL pattern)

js: True  -> page is rendered with Playwright first (group "js", runs less often)

Commented-out stores (AI Fest, Tanuki Trader, Marvellous Hobbies) block GitHub's servers with
Cloudflare/403, even through a real browser. They can't be monitored from GitHub Actions.
filter: True -> the URL is not One Piece only, keep just One Piece products
feed: explicit Shopify JSON feed (when the given URL is a page/search, not a collection)
cur: currency symbol used in alerts (default "R")
     Kai prices are the Shopify feed price, which is ex 21% Dutch VAT: that is what an export
     order to South Africa pays (site shows incl. VAT, e.g. 199.95 vs 165.25 charged).
fx: ZAR per unit of the store's currency (for the singles threshold and price comparisons)
fast: True -> checked every 30 min with the Kai preorder page (Shopify stores only: quick)
sealed: True -> only alert for sealed product that can be bought (no singles/accessories), booster
              boxes and preorders labelled; first run reports what's in stock. Default for every store.
orders_only: True -> post only the order alerts, not every new/restocked/cheaper item
order: "kai" -> checked by the fast Kai workflow; when a One Piece booster box is available, the
              alert includes a ready-to-send order email/WhatsApp to Kai (once per set)
"""

SITES = [
    {"name": "Knightly Gaming - sealed", "platform": "shopify", "fast": True,
     "url": "https://www.knightlygaming.co.za/collections/one-piece-sealed"},
    {"name": "CG Haven", "platform": "html", "link_re": r"/product/",
     "url": "https://cghaven.co.za/shop/game/one-piece?game=one-piece&availability=in_stock&sort=newest"},
    {"name": "Blist Collectibles", "platform": "shopify", "filter": True,
     "url": "https://blistcollectibles.co.za/pages/onepiece",
     "feed": "https://blistcollectibles.co.za/collections/shop-all/products.json"},
    {"name": "Armchair Generals", "platform": "shopify", "fast": True,
     "url": "https://armchairgenerals.co.za/collections/shop-one-piece"},
    {"name": "Aniware Sealed", "platform": "shopify",
     "url": "https://sealed.aniware.co.za/collections/one-piece-card-game"},
    # {"name": "AI Fest", "platform": "prestashop", "js": True,
    #  "url": "https://store.ai-fest.co.za/366-one-piece-card-game"},
    {"name": "Untapped Potential", "platform": "html", "js": True, "link_re": r"/product",
     "url": "https://www.untappedpotentialtcg.co.za/category/onepiece?sort=price-desc"},
    # {"name": "Tanuki Trader", "platform": "woocommerce", "js": True, "filter": True,
    #  "url": "https://tanukitrader.co.za/shop/"},
    # {"name": "Marvellous Hobbies", "platform": "woocommerce", "js": True,
    #  "url": "https://marvelloushobbies.com/universe/sports-cards/one-piece/"},
    {"name": "Card Cache (OP17)", "platform": "shopify",
     "url": "https://cardcache.co.za/collections/op-17"},
    {"name": "Kai of Cards - preorder", "platform": "shopify", "cur": "EUR ", "fx": 20, "order": "kai",
     "orders_only": True, "filter": True,
     "url": "https://kaiofcards.com/collections/preorder"},
    {"name": "Level Up Store", "platform": "shopify",
     "url": "https://levelupstore.co.za/collections/one-piece-in-stock"},
    {"name": "Geek Home", "platform": "woocommerce",
     "url": "https://www.geekhome.co.za/product-category/opcg/"},
    {"name": "Mirage Gaming", "platform": "html", "js": True, "filter": True, "link_re": r"/product",
     "url": "https://www.miragegaming.co.za/shop"},
    {"name": "Maximus Games", "platform": "shopify",
     "url": "https://maximusgames.co.za/collections/one-piece-tcg"},
    {"name": "Slide Gaming", "platform": "woocommerce",
     "url": "https://www.slidegaming.co.za/product-category/trading-card-games/one-piece/sealed-product-onepiece/"},
    {"name": "Rebelz Collectibles", "platform": "shopify", "filter": True,
     "url": "https://rebelzcollectibles.co.za/products"},
]

for _s in SITES:  # only sealed product for now (no singles); set "sealed": False on a store to undo
    _s.setdefault("sealed", True)
