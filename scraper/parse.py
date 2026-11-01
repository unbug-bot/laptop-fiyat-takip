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


# ---------------------------------------------------------------- Ürün sayfasından fiyat (yedek yöntem)
CORE_PRICE_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .a-price.priceToPay .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen",
    "#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen",
    "#corePrice_desktop .a-price:not(.a-text-price) .a-offscreen",
    "#apex_desktop .a-price:not(.a-text-price) .a-offscreen",
    "#price_inside_buybox",
]
OUT_OF_STOCK_WORDS = ["şu anda mevcut değil", "stokta yok", "currently unavailable", "geçici olarak stokta"]
RE_OLP_NEW = re.compile(r"yeni\s*\(\s*\d+\s*\)[^0-9]{0,40}(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)\s*(?:tl|₺)")


def parse_product_price(html):
    """Ürün sayfasından yalnızca yeni ürün fiyatlarını çıkarır.
    Kaynaklar: ana fiyat kutusu (satış kutusu yeni ürünse) ve 'Yeni (N) ... TL' diğer satıcılar özeti.
    Fiyat da stok dışı işareti de bulunamazsa None (bilinmiyor)."""
    soup = _soup(html)
    if not soup.select_one("#productTitle"):
        return None
    prices = []
    buybox = soup.select_one("#buybox, #desktop_buybox, #rightCol")
    buybox_text = norm(_text(buybox))
    new_row = soup.select_one("#newAccordionRow, #newAccordionRow_0")
    if new_row:
        el = new_row.select_one(".a-price:not(.a-text-price) .a-offscreen")
        p = parse_price_tl(_text(el))
        if p:
            prices.append(p)
    elif not any(w in buybox_text for w in EXCLUDED_CONDITIONS):
        for sel in CORE_PRICE_SELECTORS:
            p = parse_price_tl(_text(soup.select_one(sel)))
            if p:
                prices.append(p)
                break
    olp = norm(" ".join(_text(e) for e in soup.select(
        "#olpLinkWidget_feature_div, #aod-ingress-link, #buybox-see-all-buying-choices, "
        "#mbc, #dynamic-aod-ingress-box, #moreBuyingChoices_feature_div")))
    for m in RE_OLP_NEW.finditer(olp):
        p = parse_price_tl(m.group(1))
        if p:
            prices.append(p)
    if prices:
        return {"new_prices": prices, "source": "urun"}
    avail = norm(_text(soup.select_one("#availability, #outOfStock")))
    if soup.select_one("#outOfStock") or any(w in avail for w in OUT_OF_STOCK_WORDS):
        return {"new_prices": [], "source": "urun"}
    return None


# ---------------------------------------------------------------- "Alışverişe devam et" ara sayfası
def parse_captcha_form(html):
    """Amazon'un doğrulama formunu okur. Kodun gizli alanda hazır geldiği
    'Alışverişe Devam Et' sayfasında {'action', 'params'} döner. Resimli CAPTCHA
    (kodun elle yazılması gereken) veya tanınmayan sayfada None döner."""
    soup = _soup(html)
    form = soup.find("form", action=re.compile(r"validatecaptcha", re.I))
    if not form:
        return None
    params, typed_field = {}, False
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        if (inp.get("type") or "text").lower() != "hidden":
            typed_field = True          # kullanıcının yazması gereken alan var: resimli CAPTCHA
            continue
        params[name] = inp.get("value", "")
    if typed_field or not params.get("field-keywords") or form.find("img"):
        return None
    return {"action": form["action"], "params": params}
