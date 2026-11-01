import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import yaml

from . import specs
from .fetch import BASE, Blocked, Fetcher, save_debug
from .parse import parse_offers, parse_product, parse_search

DB_PATH = "data/db.json"
SITE_PATH = "data/site.json"


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_db():
    with open(DB_PATH, encoding="utf-8") as f:
        db = json.load(f)
    db.setdefault("meta", {})
    db.setdefault("products", {})
    return db


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def fmt_tl(kurus):
    lira = kurus / 100
    s = f"{lira:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s.removesuffix(",00") + " TL"


# ---------------------------------------------------------------- Bildirim kuralı
def apply_observation(p, price, in_stock, ts, cfg):
    """Bir fiyat gözlemini ürüne işler. Bildirim gerekiyorsa sözlük döndürür.

    Kural: yeni fiyat <= takip süresindeki en düşük fiyat x (1 - %eşik) ise bildirim.
    - Daha önce hiç fiyatı görülmemiş ürünün ilk fiyatı referanstır, bildirim yok.
    - Stokta olmadığı dönemler en düşük fiyatı değiştirmez; stoğa dönüşte de aynı kural geçerli.
    - Fiyat sınırının üstündeki fiyat ve onaylı olmayan ürün bildirim almaz.
    """
    alert = None
    max_kurus = cfg["max_price_tl"] * 100
    drop = cfg["alert_drop_pct"]
    if in_stock and price is not None:
        ref = p.get("min_price")
        if ref is not None and price * 100 <= ref * (100 - drop) \
                and price <= max_kurus and p.get("status") == "onayli":
            alert = {"asin": p["asin"], "title": p["title"], "price": price, "ref_min": ref,
                     "pct": round((ref - price) / ref * 100, 1)}
            p.setdefault("alerts", []).append([ts, price, ref])
        cur = p.get("current_price")
        if cur is not None and cur != price:
            p["prev_price"] = cur
            p["last_change_pct"] = round((price - cur) / cur * 100, 2)
        elif cur is None and p.get("last_known_price") not in (None, price):
            prev = p["last_known_price"]
            p["prev_price"] = prev
            p["last_change_pct"] = round((price - prev) / prev * 100, 2)
        p["min_price"] = price if ref is None else min(ref, price)
        p["max_price"] = price if p.get("max_price") is None else max(p["max_price"], price)
        p["last_known_price"] = price
        p["current_price"] = price
        p["in_stock"] = True
    else:
        p["current_price"] = None
        p["in_stock"] = False
    hist = p.setdefault("history", [])
    point = price if in_stock else None
    if not hist or hist[-1][1] != point:
        hist.append([ts, point])
    p["last_checked"] = ts
    return alert


def visible(p, cfg):
    if p.get("status") != "onayli":
        return False
    ref = p["current_price"] if p.get("in_stock") else p.get("last_known_price")
    return ref is None or ref <= cfg["max_price_tl"] * 100


# ---------------------------------------------------------------- Telegram
def send_telegram(alerts):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not alerts:
        return
    if not token or not chat:
        print("Telegram secret'ları tanımlı değil; bildirim gönderilmedi.")
        return
    for a in alerts:
        text = (f"🔻 <b>%{a['pct']} indirim</b> (takip edilen en düşük fiyata göre)\n"
                f"{a['title'][:200]}\n\n"
                f"Şimdi: <b>{fmt_tl(a['price'])}</b>\n"
                f"Önceki en düşük: {fmt_tl(a['ref_min'])}\n"
                f'<a href="{BASE}/dp/{a["asin"]}">Amazon\'da aç</a>')
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": True}, timeout=20)
            print(f"Telegram: {a['asin']} -> {r.status_code}")
        except Exception as e:
            print(f"Telegram hatası: {e}")


# ---------------------------------------------------------------- Site verisi
def export_site(db, cfg):
    items, pending = [], []
    for p in db["products"].values():
        if p.get("status") == "onayli" and visible(p, cfg):
            items.append({k: p.get(k) for k in (
                "asin", "title", "current_price", "min_price", "max_price", "prev_price",
                "last_change_pct", "in_stock", "last_known_price", "history", "specs",
                "first_seen", "last_checked", "alerts", "manual")})
        elif p.get("status") == "beklemede":
            pending.append({k: p.get(k) for k in (
                "asin", "title", "reasons", "specs", "current_price", "last_known_price", "in_stock")})
    save_json(SITE_PATH, {"meta": db["meta"], "items": items, "pending": pending,
                          "config": {"max_price_tl": cfg["max_price_tl"],
                                     "alert_drop_pct": cfg["alert_drop_pct"]}})


# ---------------------------------------------------------------- Akış
def evaluate_detail(f, asin, cfg):
    html = f.product(asin)
    if html is None:
        return None
    if html == "":
        return {"status": "red", "reasons": ["ürün sayfası bulunamadı (404)"], "specs": {}, "title": None}
    data = parse_product(html)
    if data is None:
        save_debug(f"urun_{asin}.html", html)
        return None
    status, reasons, summary = specs.evaluate(
        data["title"], data["table"], data["bullets"], data["breadcrumb"], cfg)
    return {"status": status, "reasons": reasons, "specs": summary, "title": data["title"]}


def apply_overrides(db, ov):
    approve, reject = set(ov.get("approve") or []), set(ov.get("reject") or [])
    for asin, p in db["products"].items():
        auto = p.get("auto_status", p.get("status"))
        if asin in reject:
            p["status"], p["manual"] = "red", "reddedildi"
        elif asin in approve and auto != "red":
            p["status"], p["manual"] = "onayli", "onaylandı"
        elif asin in approve and auto == "red":
            # Otomatik red kesin bir filtre ihlalidir; manuel onay bunu ezmez ama görünür kılınır
            p["status"], p["manual"] = "red", "onay istendi ama filtre ihlali var"
        else:
            p["status"] = auto
            p.pop("manual", None)


def run():
    cfg = load_yaml("config.yaml")
    ov = load_yaml("overrides.yaml")
    db = load_db()
    t0 = now()
    ts = iso(t0)
    meta = db["meta"]
    meta["last_run"] = ts
    products = db["products"]
    alerts = []
    f = Fetcher(tuple(cfg["request_delay_sec"]))
    status = "tamam"
    observations = {}
    apply_overrides(db, ov)

    try:
        f.warmup()
        # 1) Keşif
        candidates = {}
        for q in cfg["search_queries"]:
            for page in range(1, cfg["search_pages"] + 1):
                html = f.search(q, page)
                res = parse_search(html) if html else None
                if not res:
                    if html:
                        save_debug(f"arama_{q.replace(' ', '_')}_{page}.html", html)
                    break
                for asin, title in res:
                    candidates.setdefault(asin, title)
                if len(res) < 10:
                    break
        print(f"Arama: {len(candidates)} aday ASIN")

        queue = []
        for asin, title in candidates.items():
            if asin in products:
                continue
            why = specs.title_prefilter(title, cfg)
            if why:
                products[asin] = {"asin": asin, "title": title, "auto_status": "red",
                                  "status": "red", "reasons": [why], "evaluated_at": ts, "first_seen": ts}
            else:
                queue.append(asin)
        recheck = timedelta(hours=cfg["pending_recheck_hours"])
        for asin, p in products.items():
            if p.get("auto_status") == "beklemede" and t0 - parse_iso(p["evaluated_at"]) >= recheck:
                queue.append(asin)

        # 2) Özellik doğrulama
        for asin in queue[: cfg["max_detail_fetch_per_run"]]:
            r = evaluate_detail(f, asin, cfg)
            if r is None:
                continue
            p = products.setdefault(asin, {"asin": asin, "first_seen": ts})
            p["title"] = r["title"] or p.get("title") or candidates.get(asin, asin)
            p["auto_status"], p["status"] = r["status"], r["status"]
            p["reasons"], p["specs"], p["evaluated_at"] = r["reasons"], r["specs"], ts
            if r["status"] == "red":
                for k in ("history", "current_price", "min_price", "max_price"):
                    p.pop(k, None)
            print(f"  {asin}: {r['status']} {'; '.join(r['reasons'])[:160]}")

        apply_overrides(db, ov)

        # 3) Fiyat takibi (onaylı + beklemedeki ürünler)
        tracked = [a for a, p in products.items() if p.get("status") in ("onayli", "beklemede")]
        for asin in tracked:
            html = f.offers(asin)
            res = parse_offers(html) if html else None
            if res is None:
                if html:
                    save_debug(f"teklif_{asin}.html", html)
                continue           # bilinmiyor: ürüne dokunma
            prices = res["new_prices"]
            observations[asin] = (min(prices) if prices else None, bool(prices))
    except Blocked as e:
        status = "engellendi"
        print(f"Amazon istekleri engelledi: {e}")

    # 4) Toplu stok kaybı koruması: ayrıştırma bozulduysa geçmişi kirletme
    before_in_stock = [a for a in observations if products[a].get("in_stock")]
    now_in_stock = [a for a, (_, s) in observations.items() if s]
    if len(before_in_stock) >= 3 and not now_in_stock:
        status = "anomali"
        print("Takip edilen tüm ürünler aynı anda stok dışı göründü; bu turun fiyatları yok sayıldı.")
        observations = {}

    for asin, (price, in_stock) in observations.items():
        a = apply_observation(products[asin], price, in_stock, ts, cfg)
        if a:
            alerts.append(a)

    meta.update({"last_status": status, "requests": f.requests, "blocked": f.blocked,
                 "checked": len(observations),
                 "duration_sec": int((now() - t0).total_seconds())})
    if status == "tamam":
        meta["last_ok"] = ts
    save_json(DB_PATH, db)
    export_site(db, cfg)
    send_telegram(alerts)
    print(f"Durum: {status}, istek: {f.requests}, engel: {f.blocked}, gözlem: {len(observations)}, "
          f"bildirim: {len(alerts)}")


if __name__ == "__main__":
    sys.exit(run())
