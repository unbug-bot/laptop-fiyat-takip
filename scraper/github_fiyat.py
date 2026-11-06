"""GitHub Actions'ta zamanlanmış fiyat kontrolü (fiyat.yml çalıştırır).

- Keşif YOK. Arama sayfası açılmaz. Yalnızca onaylı ürünlerin sayfası ASIN'den açılır, fiyat ve stok okunur.
- Veritabanına (data/db.json) ve data/site.json'a YAZMAZ; onların sahibi Mac'tir.
- Yazdıkları: data/gozlem/<zaman>.json (yeni dosya) ve data/github_durum.json (yalnızca GitHub yazar).
- Siteyi, veritabanı + henüz Mac'in işlemediği gözlemlerle bellekte birleştirip _site/ klasörüne hazırlar.
- Amazon engellerse kullanıcıyı beklemez; o ana kadar okunanları kaydeder ve durumu "engellendi" yazar.
"""
import glob
import json
import os
import shutil
import sys
from datetime import datetime, timezone

import yaml

from .fetch import Blocked
from .gozlem import DURUM_PATH, GOZLEM_DIR, apply_files
from .main import apply_observation, export_site, send_telegram
from .parse import parse_product_price

PROFILE = "state/chrome-profil-github"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def check_prices(f, db, cfg, ts):
    """Onaylı ürünlerin fiyatını okur, bellekteki db'ye işler. Dönüş: (gözlemler, bildirimler, engellendi)."""
    products = db["products"]
    tracked = sorted((a for a, p in products.items() if p.get("status") == "onayli"),
                     key=lambda a: products[a].get("last_checked") or "")
    obs, alerts, blocked = [], [], False
    try:
        for asin in tracked:
            html = f.product(asin)
            res = parse_product_price(html) if html else None
            if res is None:
                print(f"  {asin}: okunamadı", flush=True)
                continue
            prices = res["new_prices"]
            obs.append({"asin": asin, "price": min(prices) if prices else None, "in_stock": bool(prices)})
            print(f"  {asin}: {obs[-1]['price'] / 100 if prices else 'stokta yok'}", flush=True)
    except Blocked as e:
        blocked = True
        print(f"Amazon engelledi: {e}", flush=True)

    # Toplu stok kaybı koruması (main.py ile aynı kural): okuma bozulduysa hiçbir şey kaydedilmez
    before = [o for o in obs if products[o["asin"]].get("in_stock")]
    if len(before) >= 3 and not any(o["in_stock"] for o in obs):
        print("Bütün ürünler aynı anda stok dışı göründü; bu çalıştırmanın gözlemleri yok sayıldı.")
        return [], [], blocked
    for o in obs:
        a = apply_observation(products[o["asin"]], o["price"], o["in_stock"], ts, cfg)
        if a:
            o["alerted"] = True
            alerts.append(a)
    return obs, alerts, blocked


def run(f, ts=None):
    ts = ts or iso(datetime.now(timezone.utc))
    with open("config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    db = load_json("data/db.json", None)
    if not db or not db.get("products"):
        print("Veritabanı boş ya da yok; Mac'teki program en az bir tur göndermiş olmalı.")
        return 1
    pending = [load_json(p, {}) for p in sorted(glob.glob(os.path.join(GOZLEM_DIR, "*.json")))]
    apply_files(db, pending, cfg)                          # Mac'in henüz işlemediği önceki gözlemler

    obs, alerts, blocked = check_prices(f, db, cfg, ts)
    if obs:
        os.makedirs(GOZLEM_DIR, exist_ok=True)
        name = os.path.join(GOZLEM_DIR, ts.replace(":", "-") + ".json")
        with open(name, "w", encoding="utf-8") as fh:
            json.dump({"ts": ts, "kaynak": "github", "gozlemler": obs}, fh, ensure_ascii=False)
    status = "engellendi" if blocked and not obs else "kismen" if blocked else "tamam"
    durum = {"last_run": ts, "status": status, "checked": len(obs),
             "tracked": sum(1 for p in db["products"].values() if p.get("status") == "onayli"),
             "requests": getattr(f, "requests", 0), "blocked": getattr(f, "blocked", 0),
             "alerts": len(alerts)}
    with open(DURUM_PATH, "w", encoding="utf-8") as fh:
        json.dump(durum, fh, ensure_ascii=False)
    send_telegram(alerts)

    # Site: Mac'in veritabanı + bütün gözlemler, yalnızca _site/ içine (data/site.json'a dokunulmaz)
    db["meta"]["github"] = durum
    os.makedirs("_site", exist_ok=True)
    export_site(db, cfg, path="_site/data.json")
    shutil.copy("site/index.html", "_site/index.html")
    print(f"Durum: {status}, okunan fiyat: {len(obs)}, bildirim: {len(alerts)}", flush=True)
    return 0


def main():
    from .browser import BrowserFetcher
    with open("config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    f = BrowserFetcher(tuple(cfg["request_delay_sec"]), human_wait_min=0, profile_dir=PROFILE)
    try:
        return run(f)
    finally:
        f.close()


if __name__ == "__main__":
    sys.exit(main())
