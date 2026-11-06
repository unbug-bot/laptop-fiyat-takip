"""Tek seferlik fiyat kontrolü: yalnızca veritabanındaki ürünler, keşif yok.
Program klasöründe:  caffeinate -i ./.venv/bin/python -u -m scraper.fiyat
Ana program (baslat.command) açıkken çalıştırma: ikisi aynı Chrome profilini kullanır."""
import sys


def main():
    from . import main as scan
    from .mac import delete_merged, load_cfg, merge_github, open_browser, pull_files, push_results, setup

    s, gh = setup()
    pull_files(gh)
    cfg = load_cfg()
    merged = merge_github(gh, cfg)
    f = open_browser(cfg, s)
    try:
        scan.run(fetcher=f, cfg_override={"kesif": False})
    finally:
        f.close()
    push_results(gh)
    delete_merged(gh, merged)


if __name__ == "__main__":
    sys.exit(main())
