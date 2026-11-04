"""Offline tests: parsers run against sample HTML shaped like each platform's markup.
Run: python -m pytest tests -q   (or: python tests/test_monitor.py)
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor as m

WOO = """<ul class="products">
<li class="product type-product status-publish instock"><a class="woocommerce-LoopProduct-link" href="/product/op13-booster-box/">
<h2 class="woocommerce-loop-product__title">One Piece OP13 Booster Box</h2><span class="price"><span class="amount">R2,499.00</span></span></a></li>
<li class="product type-product outofstock"><a class="woocommerce-LoopProduct-link" href="/product/op12-case/">
<h2 class="woocommerce-loop-product__title">One Piece OP12 Case</h2><span class="price"><del>R30,000.00</del><ins>R28,500.00</ins></span></a></li></ul>"""

PRESTA = """<article class="product-miniature" data-id-product="7"><h3 class="product-title"><a href="https://store.ai-fest.co.za/x/7-op17-box.html">OP17 Booster Box</a></h3>
<span class="price">R 1 899,00</span></article>
<article class="product-miniature"><div class="product-flag out_of_stock">Out-of-Stock</div><h3 class="product-title"><a href="https://store.ai-fest.co.za/x/8-eb04.html">EB04 Booster Box</a></h3>
<span class="price">R 1 100,00</span></article>"""

GENERIC = """<div class="grid"><div class="card"><a href="/product/one-piece-round1-promotion-pack-2026-japanese"><img alt="x"></a>
<a href="/product/one-piece-round1-promotion-pack-2026-japanese">ROUND1 Promotion Pack 2026 (Japanese)</a><span>R 599,00</span><button>Quick Add</button></div>
<div class="card"><a href="/product/presale-nike-set">PRESALE Nike x ONE PIECE Promo Set</a><s>R 1 100,00</s> <b>R 900,00</b><button>Reserve Now</button></div>
<div class="card"><a href="/product/sold-thing">Sold Thing Booster Box</a><span>R 100,00</span><span>Sold out</span></div></div>"""


def test_empty_env_uses_default():
    os.environ["ORDER_ITEM"] = ""  # how GitHub passes an unset secret/variable
    assert m.env("ORDER_ITEM", "x") == "x"
    del os.environ["ORDER_ITEM"]


def test_to_float():
    assert m.to_float("R 1 199,00") == 1199.0
    assert m.to_float("R3,593.00") == 3593.0
    assert m.to_float("R3,593") == 3593.0
    assert m.to_float("R599,00") == 599.0


def test_woo():
    r = m.parse_woo(WOO, "https://x.co.za/shop/")
    a, b = r["https://x.co.za/product/op13-booster-box/"], r["https://x.co.za/product/op12-case/"]
    assert a["price"] == 2499.0 and a["available"]
    assert b["price"] == 28500.0 and not b["available"]  # sale price (min), outofstock class


def test_presta():
    r = m.parse_presta(PRESTA, "https://store.ai-fest.co.za/366-x")
    a, b = list(r.values())
    assert a["price"] == 1899.0 and a["available"]
    assert b["price"] == 1100.0 and not b["available"]


def test_generic():
    r = m.parse_generic(GENERIC, "https://cghaven.co.za/shop", r"/product/")
    assert len(r) == 3
    p1 = r["https://cghaven.co.za/product/one-piece-round1-promotion-pack-2026-japanese"]
    assert p1["price"] == 599.0 and p1["available"] and "ROUND1" in p1["title"]
    assert r["https://cghaven.co.za/product/presale-nike-set"]["price"] == 900.0
    assert not r["https://cghaven.co.za/product/sold-thing"]["available"]


def test_urls():
    assert m.shopify_feed("https://a.za/collections/op-17?sort_by=x") == "https://a.za/collections/op-17/products.json"
    assert m.shopify_feed("https://a.za/products") == "https://a.za/products.json"
    assert m.page_url("https://a.za/shop/", 3, "woocommerce") == "https://a.za/shop/page/3/"
    assert m.page_url("https://a.za/c?x=1", 2, "prestashop") == "https://a.za/c?x=1&page=2"


def item(title, price, avail=True):
    return {"title": title, "price": price, "available": avail, "url": "u/" + title}


def test_diff_rules():
    old = {"box": item("OP13 Booster Box", 2500, False), "card": item("Luffy SP OP05-001", 900),
           "cheap": item("Zoro C OP05-002", 20)}
    new = {"box": item("OP13 Booster Box", 2500, True),                      # back in stock
           "card": item("Luffy SP OP05-001", 700),                           # >10% drop
           "cheap": item("Zoro C OP05-002", 10),                             # below SINGLES_MIN: ignored
           "newbox": item("EB05 Booster Box", 99, False),                    # new sealed, sold out: still tell me
           "newcard": item("Nami SEC OP09-100", 3000),                       # new valuable single
           "newjunk": item("Usopp C OP09-101", 10)}                          # new cheap single: ignored
    tags = {n["title"]: t for t, n in m.diff(old, new)}
    assert tags["OP13 Booster Box"] == "BACK IN STOCK"
    assert tags["Luffy SP OP05-001"].startswith("PRICE DROP")
    assert tags["EB05 Booster Box"] == "NEW (sold out)"
    assert tags["Nami SEC OP09-100"] == "NEW"
    assert "Zoro C OP05-002" not in tags and "Usopp C OP09-101" not in tags


def test_diff_eur_threshold():
    # Kai prices are EUR: a EUR 30 single (~R600) must clear the R500 threshold via fx=20
    new = {"c": item("Luffy SP OP05-001", 30)}
    assert m.diff({}, new, 500 / 20) and not m.diff({}, new, 500)


def test_sealed_only():
    alerts = [("NEW", item("One Piece Starter Deck 36: YELLOW Kid", 399.99)),                    # not wanted
              ("NEW", item("One Piece Card Game: Learn Together Deck Set (LT-01)", 1150)),         # not wanted
              ("NEW", item("One Piece Card Game: Double Pack Set 13 (DP-13)", 450)),
              ("NEW", item("[Pre-Order] One Piece OP-18 The Dominance Of God Booster Box", 4079.99)),
              ("NEW (sold out)", item("One Piece EB-05 Booster Box", 4079.99, False)),     # can't buy
              ("NEW", item("Luffy SP OP05-001", 900)),                                     # single
              ("NEW", item("One Piece Rotating Power Counters Black and White (5) pack", 189)),  # accessory
              ("BACK IN STOCK", item("One Piece Card Game: Premium Card Collection - Vol. 7", 999)),
              ("NEW", item("Nami (006) (EB03-006) - Extra Booster: One Piece Heroines Edition Foil", 10)),
              ("NEW", item("Usopp [Extra Booster: Anime 25th Collection]", 10)),
              ("NEW", item("Bell-mere — Extra Booster: One Piece Heroines Edition Vol. 2 — Common", 8)),
              ("NEW", item("Kayou One Piece Pen – Series 2 (Blind Box)", 95))]
    tags = [t for t, _ in m.sealed_only(alerts)]
    assert tags == ["BOOSTER BOX PREORDER NEW", "NEW", "BACK IN STOCK"]  # box, double pack, premium


def test_kai_draft():
    os.environ.update(ORDER_NAME="A Name", ORDER_ADDRESS="1 Street, Johannesburg", ORDER_PHONE="+27 00")
    n = {"title": "One Piece Card Game - [EB-05] - Booster Box", "price": 199.95, "available": True, "url": "u"}
    subj, body = m.order_draft("EB05", [n])
    assert subj == "Preorder EB05 Case (12 boxes)"
    assert body == ("Hi Kai,\n\nI'd like to preorder 1 x EB05 Case (12 boxes).\n"
                    "Please send me a shipping quote and payment details.\n\n"
                    "A Name\n1 Street, Johannesburg\nWhatsApp: +27 00\n\nThank you!")
    for k in ("ORDER_NAME", "ORDER_ADDRESS", "ORDER_PHONE"):
        del os.environ[k]


def test_kai_orders():
    eb05 = "One Piece Card Game - EXTRA BOOSTER -ONE PIECE HEROINES EDITION Vol.2- [EB-05] - Booster Box -Preorder-"
    items = {"eb05": item(eb05, 199.95),
             "op17": item("One Piece Card Game - [OP-17] - Booster Box -Preorder-", 99.95, False),  # sold out
             "fb11": item("Dragon Ball Super Fusion World - Brightness Of Hope- [FB11] Booster Box -Preorder-", 150),
             "deck": item("One Piece Card Game - Starter Deck [ST-29] -Preorder-", 14.95),          # not a box
             "lm09": item("[LM-09] -Preorder-", 29.95)}
    assert list(m.kai_orders(items, {})) == ["EB05"]
    assert m.kai_orders(items, {"EB05": {}}) == {}   # one alert per set, ever


def test_notify_order_section():
    os.environ.pop("SMTP_HOST", None)
    n = item("One Piece [EB-05] Booster Box", 199.95)
    assert m.notify({}, [], {"EB05": [n]}) is False   # no SMTP configured -> printed only, not "sent"


def test_discord_messages():
    sent = []

    class R:
        status_code = 200
        def raise_for_status(self): pass

    real_post, real_sleep = m.requests.post, m.time.sleep
    m.requests.post = lambda url, json, timeout: sent.append(json["content"]) or R()
    m.time.sleep = lambda s: None
    os.environ["DISCORD_WEBHOOK"] = "https://discord.example/hook"
    try:
        n = item("One Piece [EB-05] Booster Box", 165.25)
        site = {"name": "Kai of Cards - preorder", "cur": "EUR "}
        assert m.notify({"k": (site, [("NEW", n)])}, [], {"EB05": [n]}) is True
    finally:
        m.requests.post, m.time.sleep = real_post, real_sleep
        del os.environ["DISCORD_WEBHOOK"]
    assert "ORDER NOW: Kai EB05" in sent[0] and "wa.me/" in sent[0] and "1 x EB05 Case" in sent[0]
    assert all(len(c) <= 2000 for c in sent)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
