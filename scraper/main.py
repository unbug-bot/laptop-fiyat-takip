import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import yaml

from . import specs
from .discover import discover, priority, worth_detail
from .fetch import BASE, Blocked, save_debug


def make_fetcher(cfg):
    """Tek başına çalıştırıldığında (python -m scraper.main) Mac tarayıcısını açar."""
    from .browser import BrowserFetcher
    return BrowserFetcher(tuple(cfg["request_delay_sec"]), human_wait_min=int(cfg.get("human_wait_min", 15)))


from .parse import (parse_offers, parse_product, parse_product_price, parse_search,
                    parse_variant_asins, parse_laptop_node)

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
def export_site(db, cfg, path=SITE_PATH):
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
    save_json(path, {"meta": db["meta"], "items": items, "pending": pending,
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
    return {"status": status, "reasons": reasons, "specs": summary, "title": data["title"], "html": html}


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


def is_detail_red(p):
    return p.get("auto_status") == "red" and not any(
        "arama başlığından" in r or "ikinci el" in r or "kategori" in r or "404" in r
        for r in p.get("reasons") or [])


def run(fetcher=None, cfg_override=None):
    """Bir tarama turu. 'fetcher' verilirse (Mac modu) tarayıcı tur sonunda kapatılmaz.
    cfg_override: config.yaml üzerine uygulanacak geçici ayarlar (ör. {"kesif": False})."""
    cfg = load_yaml("config.yaml")
    cfg.update(cfg_override or {})
    discovery_on = cfg.get("kesif", True)
    ov = load_yaml("overrides.yaml")
    db = load_db()
    t0 = now()
    ts = iso(t0)
    meta = db["meta"]
    meta["last_run"] = ts
    products = db["products"]
    alerts = []
    f = fetcher or make_fetcher(cfg)
    blocked = False
    observations = {}
    done = {"arama": 0, "degerlendirme": 0}
    search_ran = False
    candidates_found = 0
    apply_overrides(db, ov)

    # Kalıcı değerlendirme kuyruğu: engellenen tur yarım kalan işi sonraki tura bırakır
    queue = meta.setdefault("eval_queue", {})          # asin -> başlık
    for asin in ov.get("track") or []:                 # overrides.yaml'da elle eklenen ürünler
        asin = str(asin).strip().upper()
        if asin not in products:
            queue.setdefault(asin, asin)

    def learn_node(html):
        if html and not meta.get("laptop_node"):
            node = parse_laptop_node(html)
            if node:
                meta["laptop_node"] = node
                print(f"Dizüstü kategori numarası öğrenildi: {node}; kategori taraması sonraki turda başlayacak")

    def add_variants(html, source):
        """Değerlendirilen ya da fiyatı okunan ürünün diğer varyantlarını kuyruğa ekler."""
        if source.get("status") not in ("onayli", "beklemede"):
            return
        for v in parse_variant_asins(html or ""):
            if v not in products and v not in queue:
                queue[v] = f"{v} (varyant: {source.get('asin')})"

    cfg_hash = hashlib.sha1((json.dumps(cfg, sort_keys=True) + str(specs.RULES_VERSION))
                            .encode()).hexdigest()[:12]
    if meta.get("config_hash") != cfg_hash:
        for asin, p in products.items():
            if p.get("auto_status") == "beklemede" or is_detail_red(p):
                queue.setdefault(asin, p.get("title") or asin)
        meta["config_hash"] = cfg_hash
    recheck = timedelta(hours=cfg["pending_recheck_hours"])
    for asin, p in products.items():
        if p.get("auto_status") == "beklemede" and t0 - parse_iso(p["evaluated_at"]) >= recheck:
            queue.setdefault(asin, p.get("title") or asin)

    try:
        # 1) Keşif: her turda kategori ve anahtar kelime taramasından birer parça (kaldığı yerden)
        if discovery_on:
            search_ran = True
            candidates_found = discover(f, cfg, meta, products, queue, ts)
            done["arama"] += 1 if candidates_found else 0
        else:
            print("Keşif kapalı: yalnızca veritabanındaki ürünler kontrol ediliyor.", flush=True)

        # 2) Özellik doğrulama (kuyruktan, tur başına sınırlı)
        # Laptop olmadığı başlığından belli olanlar (ekran kartı, çanta, masaüstü paket) her turda ayıklanır
        for a, t in list(queue.items()):
            if t and not t.startswith(a) and not worth_detail(t, cfg):
                queue.pop(a)
        order = sorted(queue, key=lambda a: (priority(queue[a]), a))
        if not discovery_on:          # keşif kapalıyken yalnızca veritabanında olan ürünler yeniden değerlendirilir
            order = [a for a in order if a in products]
        for asin in order[: cfg["max_detail_fetch_per_run"]]:
            r = evaluate_detail(f, asin, cfg)
            if r is None:
                continue
            title_hint = queue.pop(asin)
            done["degerlendirme"] += 1
            p = products.setdefault(asin, {"asin": asin, "first_seen": ts})
            p["title"] = r["title"] or p.get("title") or title_hint
            p["auto_status"], p["status"] = r["status"], r["status"]
            p["reasons"], p["specs"], p["evaluated_at"] = r["reasons"], r["specs"], ts
            if r["status"] == "red":
                for k in ("history", "current_price", "min_price", "max_price"):
                    p.pop(k, None)
            print(f"  {asin}: {r['status']} {'; '.join(r['reasons'])[:160]}")
            add_variants(r.get("html"), p)
            learn_node(r.get("html"))

        apply_overrides(db, ov)

        # 3) Fiyat takibi: en uzun süredir bakılmayan ürün önce (engellenen turlar sırayı bozmaz)
        tracked = sorted((a for a, p in products.items() if p.get("status") in ("onayli", "beklemede")),
                         key=lambda a: products[a].get("last_checked") or "")
        use_aod = (not meta.get("aod_blocked_at") or
                   t0 - parse_iso(meta["aod_blocked_at"]) >= timedelta(hours=24)) and \
                  (not meta.get("aod_missing_at") or
                   t0 - parse_iso(meta["aod_missing_at"]) >= timedelta(days=7))
        f.reset_streak()
        for asin in tracked:
            res = None
            if use_aod:
                try:
                    html = f.offers(asin)
                    if html == "":
                        # Teklif paneli adresi bu sitede 404 veriyor: 7 gün boyunca hiç deneme
                        print("Teklif paneli adresi bulunamadı (404); fiyatlar ürün sayfasından okunacak.")
                        meta["aod_missing_at"] = ts
                        use_aod = False
                    res = parse_offers(html) if html else None
                    if res is None and html:
                        save_debug(f"teklif_{asin}.html", html)
                except Blocked:
                    print("Teklif paneli engellendi; fiyatlar ürün sayfasından okunacak.")
                    meta["aod_blocked_at"] = ts
                    use_aod = False
                    f.reset_streak()
            if res is None:
                html = f.product(asin)
                res = parse_product_price(html) if html else None
                add_variants(html, products[asin])
                learn_node(html)
                if res is None and html:
                    save_debug(f"urun_fiyat_{asin}.html", html)
            if res is None:
                continue           # bilinmiyor: ürüne dokunma
            prices = res["new_prices"]
            observations[asin] = (min(prices) if prices else None, bool(prices))
    except Blocked as e:
        blocked = True
        print(f"Amazon istekleri engelledi: {e}")

    # 4) Toplu stok kaybı koruması: ayrıştırma bozulduysa geçmişi kirletme
    anomaly = False
    before_in_stock = [a for a in observations if products[a].get("in_stock")]
    now_in_stock = [a for a, (_, s) in observations.items() if s]
    if len(before_in_stock) >= 3 and not now_in_stock:
        anomaly = True
        print("Takip edilen tüm ürünler aynı anda stok dışı göründü; bu turun fiyatları yok sayıldı.")
        observations = {}

    for asin, (price, in_stock) in observations.items():
        a = apply_observation(products[asin], price, in_stock, ts, cfg)
        if a:
            alerts.append(a)

    progress = done["arama"] + done["degerlendirme"] + len(observations)
    if anomaly:
        status = "anomali"
    elif search_ran and not candidates_found and not blocked:
        status = "anomali_arama"      # sayfalar açıldı ama hiç ürün okunamadı
    elif blocked:
        status = "kismen" if progress else "engellendi"
    else:
        status = "tamam"
    meta.update({"last_status": status, "requests": f.requests, "blocked": f.blocked,
                 "captcha_passed": f.captcha_passed, "checked": len(observations),
                 "evaluated": done["degerlendirme"], "search_pages": done["arama"],
                 "queue": len(queue), "duration_sec": int((now() - t0).total_seconds())})
    if status == "tamam":
        meta["last_ok"] = ts
    if progress:
        meta["last_progress"] = ts
    f.save_cookies()
    if fetcher is None and hasattr(f, "close"):
        f.close()
    save_json(DB_PATH, db)
    export_site(db, cfg)
    send_telegram(alerts)
    print(f"Durum: {status}, istek: {f.requests}, engel: {f.blocked}, ara sayfa geçildi: {f.captcha_passed}, "
          f"değerlendirme: {done['degerlendirme']}, fiyat: {len(observations)}, bildirim: {len(alerts)}")


if __name__ == "__main__":
    run()
    sys.exit(0)
