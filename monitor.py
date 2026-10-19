#!/usr/bin/env python3
"""Multi-store One Piece TCG stock monitor.

Alerts on: NEW listings, BACK IN STOCK, PRICE DROP. Sealed product (boxes, cases, packs,
decks...) always alerts; singles only alert at or above SINGLES_MIN (ZAR) so you don't get
flooded with R20 commons.

Usage:
  python monitor.py                 # all stores
  python monitor.py --group http    # stores that need no browser
  python monitor.py --group js      # stores that need Playwright
  python monitor.py --site "Geek Home" --dry-run   # test one store, no email, no state change
  python monitor.py --list

Env: SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS MAIL_TO
     SINGLES_MIN (default 500)  DROP_PCT (default 10)
     ORDER_QTY (default 2) ORDER_NAME ORDER_PHONE ORDER_ADDRESS   (Kai order draft)
"""
import argparse, html as htmllib, json, os, re, smtplib, sys, time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from sites import SITES

STATE_DIR = Path(os.getenv("STATE_DIR", "state"))
SINGLES_MIN = float(os.getenv("SINGLES_MIN", "500"))
DROP_PCT = float(os.getenv("DROP_PCT", "10")) / 100
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
            return r.text if r else None
        if not self.browser:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch()
            self.ctx = self.browser.new_context(user_agent=UA, locale="en-ZA")
        page = self.ctx.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(1500)
            return page.content()
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
def is_sealed(title):
    return bool(SEALED_RE.search(title))


def diff(old, new):
    alerts = []
    for k, n in new.items():
        o = old.get(k)
        sealed = is_sealed(n["title"])
        if not sealed and max(n["price"] or 0, (o or {}).get("price") or 0) < SINGLES_MIN:
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


# ---------- Kai order draft ----------
CODE_RE = re.compile(r"\b(OP|EB|PRB|ST)[- ]?(\d{2})\b", re.I)


def order_draft(alerts):
    orderable = {n["url"]: n for t, n in alerts if n["available"] and not t.startswith("PRICE")}.values()
    qty = os.getenv("ORDER_QTY", "2")
    name = os.getenv("ORDER_NAME", "[YOUR NAME]")
    phone = os.getenv("ORDER_PHONE", "[YOUR WHATSAPP]")
    addr = os.getenv("ORDER_ADDRESS", "[YOUR ADDRESS, SOUTH AFRICA]")
    pool = list(orderable) or [n for _, n in alerts]
    codes = []
    for n in pool:
        m = CODE_RE.search(n["title"])
        if m and (m.group(1) + m.group(2)).upper() not in codes:
            codes.append((m.group(1) + m.group(2)).upper())
    tag = "Preorder " + " / ".join(codes) if codes else "Preorder"
    subject = pool[0]["title"] if len(pool) == 1 else f"{tag} - {len(pool)} One Piece items"
    if orderable:
        lines = "\n".join(f"- {qty}x {n['title']} {n['url']}" for n in orderable)
        ask = "Please send me a shipping quote and payment details."
        intro = "I'd like to order:"
    else:
        lines = "\n".join(f"- {n['title']} {n['url']}" for n in pool)
        ask = "They show as sold out - can I reserve or preorder? Please include a shipping quote."
        intro = "I saw these on your site:"
    body = (f"{tag}\n\nHi Kai,\n\n{intro}\n{lines}\n\n{ask}\n\n"
            f"{name}\n{addr}\nWhatsApp: {phone}\n\nThank you!")
    return subject, body


# ---------- notification ----------
def notify(by_site, failing):
    lines, rows = [], []
    for site, alerts in by_site.values():
        cur = site.get("cur", "R")
        lines.append(f"== {site['name']} ==")
        rows.append(f"<h3>{htmllib.escape(site['name'])}</h3><ul>")
        for tag, n in alerts:
            price = f"{cur}{n['price']:g}" if n["price"] is not None else "?"
            lines.append(f"[{tag}] {n['title']} - {price}\n  {n['url']}")
            rows.append(f"<li><b>{htmllib.escape(tag)}</b>: <a href='{n['url']}'>"
                        f"{htmllib.escape(n['title'])}</a> - {price}</li>")
        rows.append("</ul>")
        if site.get("order") == "kai":
            subject, body = order_draft(alerts)
            mailto = f"mailto:{KAI_EMAIL}?subject={quote(subject)}&body={quote(body)}"
            wa = f"https://wa.me/{KAI_WHATSAPP}?text={quote(body)}"
            lines += [f"Order email to Kai: {mailto}", f"WhatsApp Kai: {wa}"]
            rows.append(f"<p><a href='{mailto}'>Email order to Kai</a> | <a href='{wa}'>WhatsApp Kai</a></p>")
    for name in failing:
        lines.append(f"!! {name} has failed 3 runs in a row - check the parser")
        rows.append(f"<p><b>!! {htmllib.escape(name)}</b> has failed 3 runs in a row - check the parser</p>")
    text, html = "\n".join(lines), "".join(rows)
    print(text)
    if not os.getenv("SMTP_HOST"):
        return
    n_alerts = sum(len(a) for _, a in by_site.values())
    msg = EmailMessage()
    msg["Subject"] = (f"TCG monitor: {n_alerts} update(s) - " + ", ".join(by_site)
                      if by_site else "TCG monitor: a store is failing")
    msg["From"], msg["To"] = os.environ["SMTP_USER"], os.environ["MAIL_TO"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["http", "js", "all"], default="all")
    ap.add_argument("--site", help="run a single store by name")
    ap.add_argument("--dry-run", action="store_true", help="no email, don't touch state")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for s in SITES:
            print(f"{s['name']:28} {s['platform']:12} {'js' if s.get('js') else 'http'}")
        return
    sites = [s for s in SITES if (not a.site or s["name"].lower() == a.site.lower())
             and (a.group == "all" or bool(s.get("js")) == (a.group == "js"))]
    STATE_DIR.mkdir(exist_ok=True)
    health_path = STATE_DIR / "_health.json"
    health = json.loads(health_path.read_text()) if health_path.exists() else {}
    by_site, failing, f = {}, [], Fetcher()
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
            if path.exists():
                alerts = diff(json.loads(path.read_text()), new)
                print(f"[{site['name']}] {len(new)} products, {len(alerts)} alert(s)")
                if alerts:
                    by_site[site["name"]] = (site, alerts)
            else:
                print(f"[{site['name']}] first run: saved baseline of {len(new)} products")
            if not a.dry_run:
                path.write_text(json.dumps(new, indent=1))
            time.sleep(1)
    finally:
        f.close()
    if not a.dry_run:
        health_path.write_text(json.dumps(health, indent=1))
    if by_site or failing:
        if a.dry_run:
            os.environ.pop("SMTP_HOST", None)
        notify(by_site, failing)


if __name__ == "__main__":
    main()
