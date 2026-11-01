"""Amazon.com.tr HTML ayrıştırma. Seçiciler canlı sayfada değişebilir; ayrıştırma
başarısız olursa sonuç None döner ve ürünün durumu o turda değiştirilmez."""
import re
from bs4 import BeautifulSoup

from .specs import norm

BLOCK_MARKERS = ["validatecaptcha", "captchacharacters", "robot olmadığınızı",
                 "not a robot", "api-services-support@amazon.com", "/errors/validatecaptcha"]


def is_blocked(html):
    low = html.lower()
    return any(m in low for m in BLOCK_MARKERS)


def _soup(html):
    return BeautifulSoup(html, "lxml")


def _text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def parse_price_tl(s):
    """'84.999,00 TL' -> 8499900 (kuruş). Biçim tanınmazsa None."""
    if not s:
        return None
    s = s.replace("\xa0", " ").replace("TL", "").replace("₺", "").strip()
    m = re.search(r"\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?", s)
    if not m:
        return None
    num = m.group(0).replace(".", "")
    if "," in num:
        lira, kurus = num.split(",")
        kurus = (kurus + "0")[:2]
    else:
        lira, kurus = num, "00"
    return int(lira) * 100 + int(kurus)


# ---------------------------------------------------------------- Arama
def parse_search(html):
    """[(asin, başlık)] döndürür. Sayfa tanınmazsa None."""
    soup = _soup(html)
    items = soup.select('div[data-component-type="s-search-result"][data-asin]')
    if not items and not soup.select_one("#search, .s-main-slot"):
        return None
    out = []
    for it in items:
        asin = (it.get("data-asin") or "").strip()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            continue
        h2 = it.select_one("h2")
        title = _text(h2)
        if title:
            out.append((asin, title))
    return out


# ---------------------------------------------------------------- Ürün sayfası
TABLE_ROW_SELECTORS = [
    "#productDetails_techSpec_section_1 tr",
    "#productDetails_techSpec_section_2 tr",
    "#productDetails_detailBullets_sections1 tr",
    "#prodDetails table tr",
    "table.prodDetTable tr",
    "#productOverview_feature_div tr",
    "#poExpander tr",
]


def parse_product(html):
    """Özellik verisi. #productTitle yoksa None."""
    soup = _soup(html)
    title_el = soup.select_one("#productTitle")
    if not title_el:
        return None
    table, seen = [], set()
    for sel in TABLE_ROW_SELECTORS:
        for tr in soup.select(sel):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                k, v = _text(cells[0]), _text(cells[-1])
                if k and v and (k, v) not in seen:
                    seen.add((k, v))
                    table.append((k, v))
    for li in soup.select("#detailBullets_feature_div li"):
        t = _text(li)
        if ":" in t:
            k, v = t.split(":", 1)
            k, v = k.strip(" \u200e\u200f"), v.strip(" \u200e\u200f")
            if k and v and (k, v) not in seen:
                seen.add((k, v))
                table.append((k, v))
    bullets = [_text(li) for li in soup.select("#feature-bullets li, #featurebullets_feature_div li")]
    bullets = [b for b in dict.fromkeys(bullets) if b]
    return {
        "title": _text(title_el),
        "table": table,
        "bullets": bullets,
        "breadcrumb": _text(soup.select_one("#wayfinding-breadcrumbs_feature_div")),
    }


# ---------------------------------------------------------------- Teklif listesi (AOD)
EXCLUDED_CONDITIONS = ["kullanılmış", "yenilenmiş", "yenilendi", "ikinci el",
                       "used", "renewed", "refurbished", "koleksiyon"]


def offer_is_new(heading):
    h = norm(heading)
    if not h:
        return False                      # durum belirlenemiyorsa sayma
    if any(x in h for x in EXCLUDED_CONDITIONS):
        return False                      # "yenilenmiş" de "yeni" ile başlar; önce bu kontrol
    return h.startswith("yeni") or h == "new"


def parse_offers(html):
    """Yalnızca 'Yeni' durumdaki tekliflerin fiyatlarını (kuruş) döndürür.
    Teklif paneli tanınmazsa None (stok durumu bilinmiyor demek)."""
    soup = _soup(html)
    blocks = soup.select("#aod-pinned-offer, #aod-offer")
    container = soup.select_one("#aod-container, #all-offers-display, #aod-offer-list, #aod-pinned-offer")
    if not blocks and not container:
        return None
    prices, conditions = [], []
    for b in blocks:
        heading = _text(b.select_one("#aod-offer-heading"))
        conditions.append(heading)
        if not offer_is_new(heading):
            continue
        price_el = b.select_one("#aod-offer-price .a-price:not(.a-text-price) .a-offscreen") or \
            b.select_one(".a-price:not(.a-text-price) .a-offscreen")
        p = parse_price_tl(_text(price_el))
        if p:
            prices.append(p)
    if blocks and not any(conditions):
        return None                       # durum başlıkları okunamadı: markup değişmiş olabilir
    return {"new_prices": prices, "conditions": conditions}
