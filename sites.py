"""Store configuration.

platform:
  shopify      reads <collection>/products.json (no HTML parsing, fastest + most reliable)
  woocommerce  parses the product grid HTML, paginates with /page/N/
  prestashop   parses the product grid HTML, paginates with ?page=N
  html         generic parser for server-rendered custom shops (link_re = product URL pattern)

js: True  -> page is rendered with Playwright first (group "js", runs less often)
filter: True -> the URL is not One Piece only, keep just One Piece products
feed: explicit Shopify JSON feed (when the given URL is a page/search, not a collection)
cur: currency symbol used in alerts (default "R")
order: "kai" -> alert email includes a pre-filled order email/WhatsApp for Kai of Cards
"""

SITES = [
    {"name": "Knightly Gaming", "platform": "shopify",
     "url": "https://www.knightlygaming.co.za/collections/one-piece-singles"},
    {"name": "Big Bang Shop", "platform": "shopify",
     "url": "https://bigbangshop.co.za/collections/one-piece-single-cards"},
    {"name": "CG Haven", "platform": "html", "link_re": r"/product/",
     "url": "https://cghaven.co.za/shop/game/one-piece?game=one-piece&availability=in_stock&sort=newest"},
    {"name": "Blist Collectibles", "platform": "shopify", "filter": True,
     "url": "https://blistcollectibles.co.za/pages/onepiece",
     "feed": "https://blistcollectibles.co.za/collections/shop-all/products.json"},
    {"name": "Armchair Generals", "platform": "shopify",
     "url": "https://armchairgenerals.co.za/collections/shop-one-piece"},
    {"name": "Aniware Sealed", "platform": "shopify",
     "url": "https://sealed.aniware.co.za/collections/one-piece-card-game"},
    {"name": "AI Fest", "platform": "prestashop",
     "url": "https://store.ai-fest.co.za/366-one-piece-card-game"},
    {"name": "Untapped Potential", "platform": "html", "js": True, "link_re": r"/product",
     "url": "https://www.untappedpotentialtcg.co.za/category/onepiece?sort=price-desc"},
    {"name": "Tanuki Trader", "platform": "woocommerce", "filter": True,
     "url": "https://tanukitrader.co.za/shop/"},
    {"name": "Marvellous Hobbies", "platform": "woocommerce",
     "url": "https://marvelloushobbies.com/universe/sports-cards/one-piece/"},
    {"name": "Card Cache (OP17)", "platform": "shopify",
     "url": "https://cardcache.co.za/collections/op-17"},
    {"name": "Kai of Cards - singles", "platform": "shopify", "cur": "EUR ",
     "url": "https://kaiofcards.com/collections/one-piece-singles"},
    {"name": "Kai of Cards - sealed", "platform": "shopify", "cur": "EUR ", "order": "kai",
     "url": "https://kaiofcards.com/collections/one-piece-sealed"},
    {"name": "Kai of Cards - preorder", "platform": "shopify", "cur": "EUR ", "order": "kai", "filter": True,
     "url": "https://kaiofcards.com/collections/preorder"},
    {"name": "Kai of Cards - new", "platform": "shopify", "cur": "EUR ", "order": "kai", "filter": True,
     "url": "https://kaiofcards.com/collections/new-online"},
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
