"""Onay testi: fiyat dışındaki bütün filtreleri geçmiş ürünlerin (onaylılar) fiyatını tek tek kontrol eder.

Kapsam: veritabanında durumu "onaylı" olan her ürün; fiyatı sınırın altında (sitede yayımlanan) da,
üstünde (fiyatı yüzünden sitede olmayan) da. Ekran, işlemci, ekran kartı gibi değişmeyen özellikler
yeniden kontrol EDİLMEZ; reddedilen ve onay bekleyen ürünlere hiç bakılmaz. Her ürünün yalnızca
güncel fiyatı ve stoku ASIN'den açılan ürün sayfasından okunur.

Çakışma olmaması için:
  - Veritabanı (data/db.json), site dosyası, GitHub ve onay listesi YALNIZCA OKUNUR, hiçbir şey yazılmaz.
  - Ayrı bir Chrome profili kullanılır (state/chrome-profil-onay-testi); ana program açıkken de çalışır.
  - Sonuç yalnızca raporlar/ klasörüne ve terminale yazılır.
"""
import json
import os
import sys
from datetime import datetime

import yaml

PROFILE = "state/chrome-profil-onay-testi"
REPORT_DIR = "raporlar"


def tl(kurus):
    if kurus is None:
        return "—"
    return f"{kurus / 100:,.0f}".replace(",", ".") + " TL"


def ref_price(p):
    return p.get("current_price") if p.get("in_stock") else p.get("last_known_price")


def approved(db):
    """Fiyat dışındaki bütün filtreleri geçmiş ürünler (fiyatından bağımsız), ucuzdan pahalıya."""
    items = [p for p in db["products"].values() if p.get("status") == "onayli"]
    return sorted(items, key=lambda p: (ref_price(p) is None, ref_price(p) or 0, p["asin"]))


def check_one(f, p, cfg):
    """Yalnızca fiyat ve stok."""
    from .parse import parse_product_price
    limit = cfg["max_price_tl"] * 100
    old = ref_price(p)
    row = {"asin": p["asin"], "baslik": p.get("title"), "kayitli_fiyat": old,
           "sitede_miydi": old is None or old <= limit}
    html = f.product(p["asin"])
    if html == "":
        row.update(sonuc="SAYFA YOK", ayrinti=["ürün sayfası bulunamadı (404)"])
        return row
    res = parse_product_price(html) if html else None
    if res is None:
        row.update(sonuc="OKUNAMADI", ayrinti=["fiyat/stok okunamadı"])
        return row
    price = min(res["new_prices"]) if res["new_prices"] else None
    notes = []
    if price is None:
        sonuc = "STOKTA YOK"
    elif price <= limit:
        sonuc = "SİTEDE" if row["sitede_miydi"] else "SINIR ALTINA İNDİ"
    else:
        sonuc = "SINIR ÜSTÜ"
        if row["sitede_miydi"] and old is not None:
            notes.append("sitedeydi, fiyatı sınırın üstüne çıktı")
    if price is not None and old and price != old:
        notes.append(f"fiyat değişmiş: {tl(old)} → {tl(price)} (%{(price - old) / old * 100:+.1f})")
    if price is not None and p.get("min_price") and price < p["min_price"]:
        notes.append(f"takip süresindeki en düşük fiyatın ({tl(p['min_price'])}) altında")
    row.update(sonuc=sonuc, guncel_fiyat=price, ayrinti=notes)
    return row


def write_report(rows, started, total, interrupted):
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = started.strftime("%Y-%m-%d_%H-%M")
    base = os.path.join(REPORT_DIR, f"onay-testi_{stamp}")
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump({"baslangic": started.isoformat(timespec="seconds"), "toplam": total,
                   "kontrol_edilen": len(rows), "yarida_kaldi": interrupted, "urunler": rows},
                  fh, ensure_ascii=False, indent=1)
    lines = [f"Onay testi — {started:%d.%m.%Y %H:%M}",
             f"Kontrol edilen: {len(rows)}/{total}" + (" (yarıda durduruldu)" if interrupted else ""), ""]
    down = [r for r in rows if r["sonuc"] == "SINIR ALTINA İNDİ"]
    if down:
        lines.append("FİYATI SINIRIN ALTINA İNEN ÜRÜNLER:")
        for r in down:
            lines.append(f"  {r['asin']}  {tl(r['guncel_fiyat'])}  {(r['baslik'] or '')[:80]}")
            lines.append(f"  https://www.amazon.com.tr/dp/{r['asin']}")
        lines.append("")
    for r in rows:
        lines.append(f"[{r['sonuc']}] {r['asin']}  {(r['baslik'] or '')[:90]}")
        lines.append(f"    kayıtlı: {tl(r['kayitli_fiyat'])}   güncel: {tl(r.get('guncel_fiyat'))}")
        for n in r.get("ayrinti") or []:
            lines.append(f"    - {n}")
    counts = {}
    for r in rows:
        counts[r["sonuc"]] = counts.get(r["sonuc"], 0) + 1
    lines += ["", "Özet: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
    with open(base + ".txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return base + ".txt"


def run(f, db, cfg):
    started = datetime.now()
    items = approved(db)
    limit = cfg["max_price_tl"] * 100
    on_site = sum(1 for p in items if ref_price(p) is None or ref_price(p) <= limit)
    print(f"Onay testi: fiyat dışındaki bütün filtreleri geçen {len(items)} ürünün fiyatı kontrol edilecek "
          f"({on_site} tanesi sitede, {len(items) - on_site} tanesi fiyatı yüzünden sitede değil).\n", flush=True)
    rows, interrupted = [], False
    try:
        for i, p in enumerate(items, 1):
            print(f"({i}/{len(items)}) {p['asin']}  {(p.get('title') or '')[:80]}", flush=True)
            try:
                r = check_one(f, p, cfg)
            except Exception as e:                      # engel çözülmedi vb.: bu ürün atlanır
                r = {"asin": p["asin"], "baslik": p.get("title"), "kayitli_fiyat": p.get("current_price"),
                     "sonuc": "OKUNAMADI", "ayrinti": [f"hata: {e}"]}
            rows.append(r)
            print(f"    -> {r['sonuc']}  güncel fiyat: {tl(r.get('guncel_fiyat'))}", flush=True)
            for n in r.get("ayrinti") or []:
                print(f"       - {n}", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print("\nDurduruldu; o ana kadarki sonuçlar rapora yazılıyor.", flush=True)
    down = [r for r in rows if r["sonuc"] == "SINIR ALTINA İNDİ"]
    print("\nFiyatı sınırın altına inen: " + (", ".join(f"{r['asin']} ({tl(r['guncel_fiyat'])})" for r in down)
                                               if down else "yok"), flush=True)
    path = write_report(rows, started, len(items), interrupted)
    print(f"\nRapor: {path}", flush=True)
    return rows


def main():
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from .browser import BrowserFetcher
    with open("config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not os.path.exists("data/db.json"):
        print("Veritabanı (data/db.json) bulunamadı. Önce baslat.command ile en az bir tur çalıştır.")
        return 1
    with open("data/db.json", encoding="utf-8") as fh:      # yalnızca okunur
        db = json.load(fh)
    f = BrowserFetcher(tuple(cfg["request_delay_sec"]), human_wait_min=int(cfg.get("human_wait_min", 15)),
                       profile_dir=PROFILE, debug_dir=os.path.join(REPORT_DIR, "debug"))
    try:
        run(f, db, cfg)
    finally:
        f.close()


if __name__ == "__main__":
    sys.exit(main())
