"""Tek seferlik keşif: kategori ve anahtar kelime döngülerini beklemeden sonuna kadar götürür.
Program klasöründe:  caffeinate -i ./.venv/bin/python -u -m scraper.kesif
Ana program (baslat.command) açıkken çalıştırma: ikisi aynı Chrome profilini kullanır."""
import sys


def main():
    from .discover import discover
    from .fetch import Blocked
    from .mac import load_cfg, open_browser, pull_files, push_results, setup
    from .main import DB_PATH, export_site, iso, load_db, now, save_json

    s, gh = setup()
    pull_files(gh)
    cfg = load_cfg()
    db = load_db()
    meta, products = db["meta"], db["products"]
    queue = meta.setdefault("eval_queue", {})
    f = open_browser(cfg, s)
    try:
        discover(f, cfg, meta, products, queue, iso(now()), sweep_budget=10**6, kw_budget=10**6)
    except Blocked as e:
        print(f"Amazon engelledi ve doğrulama çözülmedi: {e}")
    except KeyboardInterrupt:
        print("\nDurduruldu; o ana kadar bulunanlar kaydediliyor.")
    finally:
        f.close()
    save_json(DB_PATH, db)
    export_site(db, cfg)
    push_results(gh)
    print(f"Bitti. Değerlendirme kuyruğunda {len(queue)} ürün var; ana program bunları sırayla değerlendirir.")


if __name__ == "__main__":
    sys.exit(main())
