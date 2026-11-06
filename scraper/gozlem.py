"""Mac ile GitHub arasında fiyat gözlemlerinin çakışmasız taşınması.

Kural: veritabanının (data/db.json) TEK yazarı Mac'tir.
  - GitHub fiyat kontrolü veritabanına hiç yazmaz. Okuduğu fiyatları her çalıştırmada YENİ bir dosyaya
    yazar: data/gozlem/<zaman>.json. Var olan bir dosyayı asla değiştirmez.
  - Mac her tur başında bu dosyaları okuyup veritabanına işler, veritabanını gönderdikten sonra işlediği
    dosyaları siler. GitHub yalnızca yeni dosya eklediği, Mac yalnızca işlenmiş dosyayı sildiği için
    ikisi aynı dosyaya hiçbir zaman yazmaz.
  - Bir gözlem, ürünün veritabanındaki son kontrol zamanından daha yeniyse işlenir; daha eskiyse (Mac o
    ürüne sonradan zaten bakmışsa) yok sayılır. Aynı dosya iki kez işlense bile sonuç değişmez.
  - Telegram bildirimini gözlemi yapan taraf gönderir; Mac, GitHub'ın gözlemini işlerken bildirim göndermez.
"""
from .main import apply_observation

GOZLEM_DIR = "data/gozlem"
DURUM_PATH = "data/github_durum.json"


def apply_files(db, files, cfg):
    """files: [{"ts": ..., "gozlemler": [{"asin", "price", "in_stock", "alerted"}]}] (sıra önemsiz).
    Dönüş: işlenen gözlem sayısı."""
    products = db["products"]
    applied = 0
    for doc in sorted(files, key=lambda d: d.get("ts") or ""):
        ts = doc.get("ts")
        for o in doc.get("gozlemler") or []:
            p = products.get(o.get("asin"))
            if not p or not ts:
                continue
            if p.get("last_checked") and ts <= p["last_checked"]:
                continue                                  # Mac daha yeni bir değer okumuş
            alert = apply_observation(p, o.get("price"), bool(o.get("in_stock")), ts, cfg)
            if o.get("alerted") and not alert and o.get("price") is not None:
                p.setdefault("alerts", []).append([ts, o["price"], p.get("min_price")])
            applied += 1
    return applied
