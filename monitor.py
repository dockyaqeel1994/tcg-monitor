#!/usr/bin/env python3
"""Multi-store One Piece TCG stock monitor.

Alerts on: NEW listings, BACK IN STOCK, PRICE DROP. Stores with "sealed": True (all of them by
default) only report sealed product that can be bought, booster boxes and preorders labelled.
Without it: sealed product (boxes, cases, packs,
decks...) always alerts; singles only alert at or above SINGLES_MIN (ZAR) so you don't get
flooded with R20 commons.

Usage:
  python monitor.py                 # all stores
  python monitor.py --group http    # stores that need no browser
  python monitor.py --group js      # stores that need Playwright
  python monitor.py --site "Geek Home" --dry-run   # test one store, no email, no state change
  python monitor.py --list

Env: SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS MAIL_TO   (email alerts)
     DISCORD_WEBHOOK                                (Discord alerts)
     SINGLES_MIN (default 500)  DROP_PCT (default 10)
     ORDER_ITEM (default "1 x {set} Case (12 boxes)") ORDER_NAME ORDER_PHONE ORDER_ADDRESS (Kai order)
"""
import argparse, html as htmllib, json, os, re, smtplib, sys, time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from sites import SITES


def env(name, default):
    """os.getenv, but treats an empty value as unset (GitHub passes unset vars/secrets as "")."""
    return (os.getenv(name) or "").strip() or default


STATE_DIR = Path(env("STATE_DIR", "state"))
SINGLES_MIN = float(env("SINGLES_MIN", "500"))
DROP_PCT = float(env("DROP_PCT", "10")) / 100
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
KAI_EMAIL, KAI_WHATSAPP = "info@kaiofcards.com", "31612147379"

OP_RE = re.compile(r"one\s*piece|\b(?:OP|EB|PRB|ST)[- ]?\d{2}\b|\bP-\d{3}\b", re.I)
SEALED_RE = re.compile(
    r"\b(booster|box|boxes|case|display|starter deck|tin|bundle|sealed|prerelease|pre-release|"
    r"premium pack|promotion pack|pack|deck set|collection)\b", re.I)
PRICE_RE = re.compile(r"(?<![A-Za-z])(?:R|ZAR)\s?\d(?:[\d\s .,]*\d)?")
NUM_RE = re.compile(r"\d(?:[\d\s .,]*\d)?")
NEG_RE = re.compile(r"sold out|out of stock|out-of-stock|notify me|unavailable", re.I)

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-ZA,en;q=0.9"})


# ---------- helpers ----------
def to_float(s):
    s = re.sub(r"(?i)^(?:ZAR|R)", "", s.strip())
    s = re.sub(r"[\s ]", "", s).strip(".,")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{2}$", s) else s.replace(",", "")
    return float(s)


def min_price(text):
    vals = []
    for m in PRICE_RE.findall(text or ""):
        try:
            vals.append(to_float(m))
        except ValueError:
            pass
    return min(vals) if vals else None


def keep(site, text):
    return not site.get("filter") or bool(OP_RE.search(text))


def origin(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get(url, **kw):
    """GET with retry/backoff. Returns None on 404/410, raises when it keeps failing."""
    status = None
    for attempt in range(4):
        try:
            r = S.get(url, timeout=30, **kw)
            status = r.status_code
            if status == 200:
                return r
            if status in (404, 410):
                return None
        except requests.RequestException as e:
            status = type(e).__name__
        time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed ({status})")


class Fetcher:
    """HTML fetcher; launches Playwright lazily for JS-rendered stores."""
    def __init__(self):
        self.pw = self.browser = self.ctx = None

    def html(self, url, js=False):
        if not js:
            r = get(url)
            if r is None:
                return None
            if "charset" not in r.headers.get("content-type", "").lower():
                r.encoding = r.apparent_encoding  # else requests assumes latin-1 and mangles "–"
            return r.text
        if not self.browser:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch()
            self.ctx = self.browser.new_context(user_agent=UA, locale="en-ZA")
        page = self.ctx.new_page()
        try:
            # "networkidle" never fires on pages with chat/analytics scripts; wait for the DOM, then
            # give client-side rendering a few seconds
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            html = page.content()
            if re.search(r"Just a moment|cf-challenge|challenge-platform|Attention Required", html[:20000]):
                raise RuntimeError("blocked by a Cloudflare bot check")
            return html
        finally:
            page.close()

    def close(self):
        if self.browser:
            self.browser.close()
            self.pw.stop()


def page_url(url, n, platform):
    if n == 1:
        return url
    p = urlparse(url)
    if platform == "woocommerce":
        path = re.sub(r"/page/\d+/?$", "", p.path).rstrip("/") + f"/page/{n}/"
        return urlunparse(p._replace(path=path))
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q["page"] = str(n)
    return urlunparse(p._replace(query=urlencode(q)))


# ---------- parsers (HTML -> {id: item}) ----------
def parse_generic(html, base, link_re=r"/products?/"):
    soup = BeautifulSoup(html, "lxml")
    is_prod = lambda a: re.search(link_re, urljoin(base, a["href"]).split("?")[0])
    out = {}
    for a in soup.find_all("a", href=True):
        if not is_prod(a):
            continue
        href = urljoin(base, a["href"]).split("#")[0].split("?")[0]
        if href in out:
            continue
        best, node = None, a
        for _ in range(7):
            if node is None or node.name in ("body", "html"):
                break
            hrefs = {urljoin(base, x["href"]).split("?")[0]
                     for x in node.find_all("a", href=True) if is_prod(x)}
            if len(hrefs) > 1:
                break
            best = node
            if PRICE_RE.search(node.get_text(" ", strip=True)):
                break
            node = node.parent
        if best is None:
            continue
        texts = [x.get_text(" ", strip=True) for x in best.find_all("a", href=True)
                 if urljoin(base, x["href"]).split("?")[0] == href]
        title = max(texts, key=len, default="")
        if not title:
            img = best.find("img", alt=True)
            title = img["alt"] if img else ""
        if not title:
            continue
        txt = best.get_text(" ", strip=True).replace(title, " ")
        out[href] = {"title": title, "price": min_price(txt),
                     "available": not NEG_RE.search(txt), "url": href}
    return out


def parse_woo(html, base):
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for li in soup.select("li.product, div.product.type-product"):
        a = li.select_one("a.woocommerce-LoopProduct-link") or li.select_one("a[href]")
        t = li.select_one(".woocommerce-loop-product__title, h2, h3")
        if not a or not t:
            continue
        url = urljoin(base, a["href"]).split("?")[0]
        price_el = li.select_one(".price")
        txt = li.get_text(" ", strip=True)
        out[url] = {"title": t.get_text(" ", strip=True),
                    "price": min_price(price_el.get_text(" ", strip=True)) if price_el else None,
                    "available": "outofstock" not in (li.get("class") or []) and not NEG_RE.search(txt),
                    "url": url}
    return out


def parse_presta(html, base):
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for art in soup.select("article.product-miniature"):
        a = art.select_one(".product-title a, h2 a, h3 a")
        if not a:
            continue
        url = urljoin(base, a["href"]).split("?")[0]
        price_el = art.select_one(".price")
        ptxt = price_el.get_text(" ", strip=True) if price_el else ""
        price = min_price(ptxt)
        if price is None and NUM_RE.search(ptxt):
            price = to_float(NUM_RE.search(ptxt).group())
        out[url] = {"title": a.get_text(" ", strip=True), "price": price,
                    "available": not (art.select_one(".out_of_stock, .product-unavailable")
                                      or NEG_RE.search(art.get_text(" ", strip=True))),
                    "url": url}
    return out


# ---------- adapters (site -> {id: item}) ----------
def shopify_feed(url):
    m = re.match(r"(/collections/[^/]+)", urlparse(url).path)
    return origin(url) + (m.group(1) if m else "") + "/products.json"


def shopify(site, f):
    feed = site.get("feed") or shopify_feed(site["url"])
    base, out = origin(site["url"]), {}
    for page in range(1, 41):
        r = get(feed, params={"limit": 250, "page": page})
        if r is None:
            break
        prods = r.json().get("products", [])
        for p in prods:
            hay = " ".join([p["title"], p.get("vendor") or "", p.get("product_type") or "",
                            " ".join(p.get("tags") or [])])
            if not keep(site, hay):
                continue
            vs = p.get("variants", [])
            prices = [float(v["price"]) for v in vs if v.get("price")]
            out[p["handle"]] = {"title": p["title"], "price": min(prices) if prices else None,
                                "available": any(v.get("available") for v in vs),
                                "url": f"{base}/products/{p['handle']}"}
        if len(prods) < 250:
            break
        time.sleep(0.5)
    return out


def paged(site, f, parse):
    out = {}
    for n in range(1, site.get("max_pages", 30) + 1):
        url = page_url(site["url"], n, site["platform"])
        html = f.html(url, site.get("js"))
        if html is None:
            break
        found = parse(html, url)
        if n == 1 and not found and site["platform"] in ("woocommerce", "prestashop"):
            found = parse_generic(html, url, site.get("link_re", r"/products?/"))
        new = {k: v for k, v in found.items() if k not in out and keep(site, v["title"])}
        if not found or not set(found) - set(out):
            break
        out.update(new)
        time.sleep(1)
    return out


ADAPTERS = {
    "shopify": shopify,
    "woocommerce": lambda s, f: paged(s, f, parse_woo),
    "prestashop": lambda s, f: paged(s, f, parse_presta),
    "html": lambda s, f: paged(s, f, lambda h, u: parse_generic(h, u, s.get("link_re", r"/products?/"))),
}


def snapshot(site, f):
    items = ADAPTERS[site["platform"]](site, f)
    if not items:
        raise RuntimeError("no products found (page layout changed or blocked?)")
    return items


# ---------- diff / alerts ----------
ACCESSORY_RE = re.compile(r"sleeve|playmat|play mat|counter|deck ?box|binder|holder|toploader|top loader|"
                          r"protector|storage|dice|card case|portfolio|figure|banpresto|plush|keychain", re.I)
SKIP_RE = re.compile(r"starter deck|deck set|learn together", re.I)  # sealed, but not wanted
NON_ENGLISH_RE = re.compile(r"japanese|chinese|korean|french|german|\b(?:JP|JPN|JAP|CN|CHN|KR|KOR)\b|"
                            r"\bOPC-\d|simplified|traditional", re.I)  # English product only
PREORDER_RE = re.compile(r"pre-?order|presale|pre-sale", re.I)


# singles often name the set they come from ("Nami (EB03-006) [Extra Booster: ...] Foil")
SINGLE_RE = re.compile(r"\b[A-Z]{1,3}\d{0,2}-\d{3}\b|\b(?:common|uncommon|rare|alternate art|alt art|full art|"
                       r"foil|parallel|SP|SEC|DON!!)\b", re.I)


def is_sealed(title):
    # ignore text in brackets/parentheses: a set name there means a single card or a "(Blind Box)" toy
    core = re.sub(r"\[(?!pre-?order)[^\]]*\]|\([^)]*\)", " ", title, flags=re.I)
    return bool(SEALED_RE.search(core)) and not ACCESSORY_RE.search(title) and not SINGLE_RE.search(title)


def diff(old, new, singles_min=SINGLES_MIN):
    alerts = []
    for k, n in new.items():
        o = old.get(k)
        sealed = is_sealed(n["title"])
        if not sealed and max(n["price"] or 0, (o or {}).get("price") or 0) < singles_min:
            continue
        if o is None:
            if sealed:
                alerts.append(("NEW" if n["available"] else "NEW (sold out)", n))
            elif n["available"]:
                alerts.append(("NEW", n))
        elif n["available"] and not o["available"]:
            alerts.append(("BACK IN STOCK", n))
        elif n["available"] and o["price"] and n["price"] and n["price"] <= o["price"] * (1 - DROP_PCT):
            alerts.append((f"PRICE DROP {o['price']:g} -> {n['price']:g}", n))
    return alerts


# ---------- price comparison ----------
KINDS = [("case", re.compile(r"\bcase\b", re.I)), ("booster box", re.compile(r"booster\s*box|\bdisplay\b", re.I)),
         ("double pack", re.compile(r"double pack", re.I)), ("pack", re.compile(r"\bpack\b", re.I))]
PRODUCT_CODE_RE = re.compile(r"\b(OP|EB|PRB|DP|IB)[- ]?(\d{2})\b", re.I)
FILLER_RE = re.compile(r"\b(?:one piece|card game|tcg|english|version|pre-?order|official|the|edition|"
                       r"vol)\b|[^a-z0-9 ]", re.I)


def product_key(title):
    """Same product at different stores -> same key, e.g. 'booster box OP17' or
    'other premium card collection live action'."""
    kind = next((k for k, r in KINDS if r.search(title)), "other")
    m = PRODUCT_CODE_RE.search(title)
    if m:
        return f"{kind} {m.group(1).upper()}{m.group(2)}"
    return f"{kind} {' '.join(FILLER_RE.sub(' ', title.lower()).split())}"


def catalog():
    """English sealed listings across all stores' saved state: (store, url, key, ZAR price, in stock)."""
    out = []
    for site in SITES:
        path = STATE_DIR / f"{slug(site['name'])}.json"
        if not path.exists():
            continue
        for n in json.loads(path.read_text()).values():
            if n["price"] and is_sealed(n["title"]) and not NON_ENGLISH_RE.search(n["title"]):
                out.append((site["name"], n["url"], product_key(n["title"]), n["price"] * site.get("fx", 1),
                            n["available"]))
    return out


def compare(n, site, cat):
    """One-line price comparison of an alert against the same product at the other stores."""
    if not n["price"]:
        return ""
    key, mine = product_key(n["title"]), n["price"] * site.get("fx", 1)
    same = [(p, s, avail) for s, url, k, p, avail in cat if k == key and url != n["url"]]
    others = sorted((p, s) for p, s, avail in same if avail)
    if not others:
        sold = sorted((p, s) for p, s, avail in same if not avail)
        if not sold:
            return "no other tracked store lists this"
        p, s = sold[0]
        extra = " + shipping" if s.startswith("Kai") else ""
        flag = "⚠️ " if mine > p * 1.25 else ""
        return f"{flag}only store in stock; sold out elsewhere at R{p:,.0f}{extra} ({s})"
    p, s = others[0]
    extra = " + shipping" if s.startswith("Kai") else ""
    if mine <= p:
        return f"✅ cheapest of {len(others) + 1} stores (next: R{p:,.0f}{extra} at {s})"
    return f"⚠️ cheaper at {s}: R{p:,.0f}{extra} ({(mine - p) / mine:.0%} less)"


# ---------- Kai order draft ----------
CODE_RE = re.compile(r"\b(OP|EB|PRB|ST)[- ]?(\d{2})\b", re.I)


BOX_RE = re.compile(r"booster\s*box|box\s*case|\bdisplay\b", re.I)


def set_code(n):
    m = CODE_RE.search(n["title"])
    return (m.group(1) + m.group(2)).upper() if m else n["url"]


def sealed_only(alerts):
    """Keep sealed products that can be bought now; label preorders and booster boxes."""
    out = []
    for t, n in alerts:
        if (not (n["available"] and is_sealed(n["title"])) or SKIP_RE.search(n["title"])
                or NON_ENGLISH_RE.search(n["title"])):
            continue
        if PREORDER_RE.search(n["title"]):
            t = "PREORDER " + t
        if BOX_RE.search(n["title"]):
            t = "BOOSTER BOX " + t
        out.append((t, n))
    return sorted(out, key=lambda a: not a[0].startswith("BOOSTER BOX"))  # boxes first


def kai_orders(items, done):
    """Available One Piece booster boxes whose set has not had an order alert yet -> {set: [items]}."""
    out = {}
    for n in items.values():
        if (n["available"] and BOX_RE.search(n["title"]) and OP_RE.search(n["title"])
                and not NON_ENGLISH_RE.search(n["title"]) and set_code(n) not in done):
            out.setdefault(set_code(n), []).append(n)
    return out


def order_draft(code, items):
    """Order email for one set, in the format used for the EB05 order with Kai."""
    label = items[0]["title"] if code.startswith("http") else code  # no set code -> use the title
    what = env("ORDER_ITEM", "1 x {set} Case (12 boxes)").format(set=label)
    name = env("ORDER_NAME", "[YOUR NAME]")
    addr = env("ORDER_ADDRESS", "[YOUR ADDRESS, SOUTH AFRICA]")
    phone = env("ORDER_PHONE", "[YOUR WHATSAPP]")
    subject = f"Preorder {what.split(' x ', 1)[-1]}"
    body = (f"Hi Kai,\n\nI'd like to preorder {what}.\n"
            f"Please send me a shipping quote and payment details.\n\n"
            f"{name}\n{addr}\nWhatsApp: {phone}\n\nThank you!")
    return subject, body


# ---------- notification ----------
def notify(by_site, failing, orders=None):
    """Print the alerts and send them to every configured channel (email, Discord).
    Returns True if at least one channel delivered them."""
    lines, rows = [], []
    for code, items in (orders or {}).items():
        subject, body = order_draft(code, items)
        mailto = f"mailto:{KAI_EMAIL}?subject={quote(subject)}&body={quote(body)}"
        wa = f"https://wa.me/{KAI_WHATSAPP}?text={quote(body)}"
        names = ", ".join(f"{n['title']} (EUR {n['price']:g})" for n in items)
        lines += [f"*** KAI: {code} BOOSTER BOX AVAILABLE - {names}",
                  *[f"  {n['url']}" for n in items],
                  f"  Email order to Kai: {mailto}", f"  WhatsApp Kai: {wa}"]
        rows.append(
            f"<div style='border:2px solid #1a7f37;padding:12px;margin:0 0 16px'>"
            f"<h2 style='margin:0 0 8px'>Kai: {htmllib.escape(code)} booster box available</h2>"
            + "".join(f"<p><a href='{n['url']}'>{htmllib.escape(n['title'])}</a> - EUR {n['price']:g}</p>"
                      for n in items)
            + f"<p style='font-size:18px'><a href='{mailto}'><b>Email order to Kai</b></a> &nbsp;|&nbsp; "
              f"<a href='{wa}'><b>WhatsApp Kai</b></a></p>"
              f"<pre style='white-space:pre-wrap'>{htmllib.escape(body)}</pre></div>")
    for site, alerts in by_site.values():
        cur = site.get("cur", "R")
        lines.append(f"== {site['name']} ==")
        rows.append(f"<h3>{htmllib.escape(site['name'])}</h3><ul>")
        for tag, n in alerts:
            price = f"{cur}{n['price']:g}" if n["price"] is not None else "?"
            cmp = f" | {n['cmp']}" if n.get("cmp") else ""
            lines.append(f"[{tag}] {n['title']} - {price}{cmp}\n  {n['url']}")
            rows.append(f"<li><b>{htmllib.escape(tag)}</b>: <a href='{n['url']}'>"
                        f"{htmllib.escape(n['title'])}</a> - {price}{htmllib.escape(cmp)}</li>")
        rows.append("</ul>")
    for name in failing:
        lines.append(f"!! {name} has failed 3 runs in a row - check the parser")
        rows.append(f"<p><b>!! {htmllib.escape(name)}</b> has failed 3 runs in a row - check the parser</p>")
    text, html = "\n".join(lines), "".join(rows)
    print(text)
    sent = False
    for name, send, enabled in (("Discord", lambda: send_discord(by_site, failing, orders), "DISCORD_WEBHOOK"),
                                ("email", lambda: send_email(by_site, orders, text, html), "SMTP_HOST")):
        if not os.getenv(enabled):
            continue
        try:  # one channel failing must not stop the other
            send()
            sent = True
        except Exception as e:
            print(f"!! sending to {name} failed: {e}", file=sys.stderr)
    return sent


def send_discord(by_site, failing, orders):
    msgs = []
    for code, items in (orders or {}).items():
        subject, body = order_draft(code, items)
        wa = f"https://wa.me/{KAI_WHATSAPP}?text={quote(body)}"
        msgs.append(f"🟢 **ORDER NOW: Kai {code} booster box available**\n"
                    + "".join(f"[{n['title']}](<{n['url']}>) · EUR {n['price']:g}\n" for n in items)
                    + f"\n👉 **[WhatsApp Kai](<{wa}>)**\n"
                      f"Email to {KAI_EMAIL}, subject `{subject}`:\n```\n{body}\n```")
    for site, alerts in by_site.values():
        cur = site.get("cur", "R")
        block = f"**{site['name']}**\n"
        for tag, n in alerts:
            price = f"{cur}{n['price']:g}" if n["price"] is not None else "?"
            cmp = f"\n  ↳ {n['cmp']}" if n.get("cmp") else ""
            line = f"`{tag}` [{n['title']}](<{n['url']}>) · {price}{cmp}\n"
            if len(block) + len(line) > 1900:  # Discord's 2000-character message limit
                msgs.append(block)
                block = f"**{site['name']}** (cont.)\n"
            block += line
        msgs.append(block)
    msgs += [f"⚠️ **{name}** has failed 3 runs in a row - check the parser" for name in failing]
    for content in msgs:
        r = requests.post(os.environ["DISCORD_WEBHOOK"], json={"content": content}, timeout=30)
        if r.status_code == 429:  # rate limited: wait as told, then retry once
            time.sleep(float(r.json().get("retry_after", 2)))
            r = requests.post(os.environ["DISCORD_WEBHOOK"], json={"content": content}, timeout=30)
        r.raise_for_status()
        time.sleep(0.5)


def send_email(by_site, orders, text, html):
    n_alerts = sum(len(a) for _, a in by_site.values())
    msg = EmailMessage()
    if orders:
        msg["Subject"] = f"ORDER NOW: Kai {' / '.join(orders)} booster box available - tap to order"
    elif by_site:
        msg["Subject"] = f"TCG monitor: {n_alerts} update(s) - " + ", ".join(by_site)
    else:
        msg["Subject"] = "TCG monitor: a store is failing"
    msg["From"], msg["To"] = os.environ["SMTP_USER"], os.environ["MAIL_TO"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(env("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


# ---------- main ----------
def group_of(site):
    """kai: priority pages on the fast 30-min workflow, js: needs a browser, http: everything else."""
    return "kai" if site.get("order") == "kai" or site.get("fast") else "js" if site.get("js") else "http"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["kai", "http", "js", "all"], default="all")
    ap.add_argument("--site", help="run a single store by name")
    ap.add_argument("--dry-run", action="store_true", help="no email, don't touch state")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--test-alert", action="store_true", help="send a sample Kai order alert and exit")
    a = ap.parse_args()
    if a.test_alert:
        n = {"title": "TEST - One Piece Card Game - [OP-99] - Booster Box -Preorder-", "price": 165.25,
             "available": True, "url": "https://kaiofcards.com/collections/preorder"}
        if not notify({}, [], {"OP99": [n]}):
            sys.exit("test alert was not delivered: no channel configured or sending failed (see above)")
        return
    if a.list:
        for s in SITES:
            print(f"{s['name']:28} {s['platform']:12} {group_of(s)}")
        return
    sites = [s for s in SITES if (not a.site or s["name"].lower() == a.site.lower())
             and a.group in ("all", group_of(s))]
    STATE_DIR.mkdir(exist_ok=True)
    # one health file per group so the two workflows never edit the same file (git conflicts)
    health_path = STATE_DIR / f"_health_{a.group}.json"
    health = json.loads(health_path.read_text()) if health_path.exists() else {}
    orders_path = STATE_DIR / "_kai_orders.json"
    done = json.loads(orders_path.read_text()) if orders_path.exists() else {}
    by_site, failing, orders, f = {}, [], {}, Fetcher()
    try:
        for site in sites:
            path = STATE_DIR / f"{slug(site['name'])}.json"
            try:
                new = snapshot(site, f)
            except Exception as e:
                health[site["name"]] = health.get(site["name"], 0) + 1
                print(f"[{site['name']}] FAILED: {e}", file=sys.stderr)
                if health[site["name"]] == 3:
                    failing.append(site["name"])
                continue
            health[site["name"]] = 0
            if site.get("order") == "kai":
                for code, items in kai_orders(new, done).items():
                    orders.setdefault(code, [])
                    orders[code] += [n for n in items if n["url"] not in {x["url"] for x in orders[code]}]
            if path.exists():
                alerts = diff(json.loads(path.read_text()), new, SINGLES_MIN / site.get("fx", 1))
            elif site.get("sealed"):  # first run: report what is in stock right now
                alerts = [("IN STOCK", n) for n in new.values()]
            else:
                alerts = None
                print(f"[{site['name']}] first run: saved baseline of {len(new)} products")
            if alerts is not None:
                if site.get("sealed"):
                    alerts = sealed_only(alerts)
                print(f"[{site['name']}] {len(new)} products, {len(alerts)} alert(s)")
                if alerts and not site.get("orders_only"):
                    by_site[site["name"]] = (site, alerts)
            if not a.dry_run:
                path.write_text(json.dumps(new, indent=1))
            time.sleep(1)
    finally:
        f.close()
    if not a.dry_run:
        health_path.write_text(json.dumps(health, indent=1))
    if by_site:
        cat = catalog()
        for site, alerts in by_site.values():
            alerts[:] = [(t, {**n, "cmp": compare(n, site, cat)}) if site.get("sealed") else (t, n)
                         for t, n in alerts]
    if by_site or failing or orders:
        if a.dry_run:
            os.environ.pop("SMTP_HOST", None)
            os.environ.pop("DISCORD_WEBHOOK", None)
        # remember a set only once its alert email actually went out, so it is never lost
        if notify(by_site, failing, orders) and orders and not a.dry_run:
            stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
            for code, items in orders.items():
                done[code] = {"alerted": stamp, "titles": [n["title"] for n in items]}
            orders_path.write_text(json.dumps(done, indent=1))


if __name__ == "__main__":
    main()
