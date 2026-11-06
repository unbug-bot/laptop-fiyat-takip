"""Ürün keşfi, turlara bölünmüş hâlde.

Gerçek veriden öğrenilenler (4 Kasım taraması):
  - Amazon bir sorgu için en fazla ~480 ilan gösteriyor (10 sayfa x 48). Toplamı bundan büyük
    aralıklarda 480/481'de takılıyor.
  - "Dizüstü Bilgisayarlar" kategorisi 30-175 bin TL arasında 30.000+ ilan bildiriyor. Tamamını tek
    seferde gezmek saatler sürüyor.

Bu yüzden:
  1) Kategori taraması her turda belli sayıda sayfa (sweep_pages_per_run) ilerler, kaldığı yeri
     veritabanında saklar ve sonraki turda devam eder. Bir tam tur (döngü) bitince baştan başlar.
     Fiyat aralıklarının gezilme sırası config'deki sweep_order ile belirlenir (artan/azalan).
     Amazon'un bildirdiği toplam gösterim sınırını aşıyorsa aralık sayfaları gezilmeden, toplam
     sayıya göre hemen yeterli sayıda alt aralığa bölünür.
  2) Anahtar kelime aramaları (beyaz listelerden otomatik) da aynı şekilde turlara bölünür.
  3) Her döngü bittiğinde, döngü boyunca bilinen onaylı ürünlerin kaçının görüldüğü kaydedilir.
"""
import math
import re
from urllib.parse import quote_plus

from . import specs
from .fetch import BASE, Blocked, save_debug
from .parse import parse_search_page

LAPTOP_WORDS = ("laptop", "notebook", "dizüstü", "dizustu", "oyuncu bilgisayar", "gaming bilgisayar",
                "oyun bilgisayar")
NOT_LAPTOP_WORDS = ("ekran kartı", "grafik kartı", "vga ", "pcie", "pci-e", "riser", "çanta", "sırt",
                    "backpack", "kılıf", "stand", "soğutucu", "cooling pad", "adaptör", "şarj aleti",
                    "masaüstü", "desktop", "all in one", "monitör", "klavye", "mouse", "oem paket", " kasa")
MIN_WIDTH = 500          # TL; bundan dar aralık bölünmez


# ---------------------------------------------------------------- yardımcılar
def sweep_url(node, lo, hi, page):
    rh = quote_plus(f"n:{node},p_36:{lo * 100}-{hi * 100}")
    return f"{BASE}/s?i=computers&rh={rh}&s=price-asc-rank&page={page}"


def keyword_url(q, page):
    return f"{BASE}/s?k={quote_plus(q)}&page={page}"


def auto_queries(cfg):
    qs = []
    for g in cfg["gpu_whitelist"]:
        qs += [f"{g} laptop", g.replace(" ", "")]
    for c in cfg["cpu_whitelist"]:
        m = re.search(r"(\d{3,5}[a-z0-9]*)$", c.replace("-", " "))
        if m:
            qs.append(m.group(1))
    qs += cfg.get("search_queries") or []
    seen, out = set(), []
    for q in qs:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def looks_like_laptop(title):
    t = specs.norm(title)
    return any(w in t for w in LAPTOP_WORDS)


def worth_detail(title, cfg):
    """Başlıktan elenemeyen ilanlardan hangileri ürün sayfasıyla değerlendirilmeli.
    - Açıkça laptop olmayan ürünler (ekran kartı, çanta, stand, masaüstü paket...) atlanır; ama başlıkta
      hem laptop kelimesi hem ekran kartı geçiyorsa (ör. "Gaming Laptop + Çanta") atlanmaz.
    - Kalanlar: başlıkta ekran kartı ya da 17 inç ve üstü ekran geçiyorsa değerlendirilir."""
    words = specs.norm(title)
    t = specs.fix_dotless(words)
    if any(w in words for w in NOT_LAPTOP_WORDS) and not (looks_like_laptop(title) and specs.find_gpu(t)):
        return False
    if specs.find_gpu(t):
        return True
    return any(s >= cfg["min_screen_inch"] for s in specs.find_size(t))


def priority(title):
    t = specs.fix_dotless(specs.norm(title or ""))
    return (0 if looks_like_laptop(title or "") else 1) + (0 if specs.find_gpu(t) else 2)


def _band_filter_works(items, lo, hi):
    priced = [p for _, _, p in items if p]
    if len(priced) < 3:
        return True
    inside = [p for p in priced if lo * 100 * 0.97 <= p <= hi * 100 * 1.03]
    return len(inside) / len(priced) >= 0.7


def _split(a, b, parts, ascending=True):
    """[a, b] aralığını 'parts' eşit parçaya böler. Yığından son eleman önce işlenir:
    ascending=True ise ucuz aralık önce, False ise pahalı aralık önce taranır."""
    parts = max(2, min(parts, (b - a + 1) // MIN_WIDTH or 2))
    step = (b - a + 1) / parts
    out, start = [], a
    for i in range(parts):
        end = b if i == parts - 1 else int(a + step * (i + 1)) - 1
        if end >= start:
            out.append([start, end, 1, 0, None])
        start = end + 1
    return out[::-1] if ascending else out


# ---------------------------------------------------------------- kategori adımı
def sweep_step(f, cfg, meta, node, seen, src, log, budget, ts, known_ok):
    lo = cfg.get("sweep_min_tl", 60000)
    hi = int(cfg["max_price_tl"] * cfg.get("sweep_max_factor", 1.25))
    cap_items = cfg.get("amazon_result_cap", 480)
    page_cap = cfg.get("sweep_max_pages_per_band", 10)
    asc = cfg.get("sweep_order", "artan") != "azalan"
    st = meta.setdefault("sweep", {})
    if st.get("node") != node or st.get("range") != [lo, hi] or st.get("order") != asc or not st.get("stack"):
        st.update(node=node, range=[lo, hi], order=asc, stack=[[lo, hi, 1, 0, None]], cycle_started=ts,
                  cycle_pages=0, cycle_seen=0, cycle_ok_seen=[])
        log.append(f"Kategori taramasında yeni döngü başladı ({lo}-{hi} TL)")
    pages = 0
    while st["stack"] and pages < budget:
        a, b, page, band_seen, total = st["stack"].pop()
        while True:
            if pages >= budget:
                st["stack"].append([a, b, page, band_seen, total])
                break
            try:
                html = f.page_url(sweep_url(node, a, b, page))
            except BaseException:                  # engel ya da Ctrl+C: aralık kaybolmasın
                st["stack"].append([a, b, page, band_seen, total])
                raise
            pages += 1
            st["cycle_pages"] += 1
            res = parse_search_page(html) if html else None
            if res is None:
                if html:
                    save_debug(f"kategori_{a}_{b}_{page}.html", html)
                log.append(f"{a}-{b} TL sayfa {page}: okunamadı, aralık sonraki döngüye kaldı")
                break
            if page == 1:
                if not _band_filter_works(res["items"], a, b):
                    log.append("Fiyat filtresi çalışmadı; kategori taraması durduruldu (anahtar kelime sürüyor)")
                    st["stack"] = []
                    st["filter_broken"] = True
                    return pages
                total = res["total"]
                too_big = res["total_plus"] or (total is not None and total > cap_items)
                if too_big and b - a + 1 > MIN_WIDTH:
                    parts = math.ceil((total or cap_items * 2) / (cap_items * 0.8))
                    st["stack"] += _split(a, b, parts, asc)
                    print(f"  [kategori {a}-{b} TL] Amazon {total}{'+' if res['total_plus'] else ''} bildirdi "
                          f"-> {parts} alt aralığa bölündü", flush=True)
                    break
            for asin, title, price in res["items"]:
                if asin not in seen:
                    seen[asin] = (title, price)
                src.setdefault(asin, set()).add("kategori")
                if asin in known_ok and asin not in st["cycle_ok_seen"]:
                    st["cycle_ok_seen"].append(asin)
            band_seen += len(res["items"])
            st["cycle_seen"] += len(res["items"])
            print(f"  [kategori {a}-{b} TL] sayfa {page}: {len(res['items'])} ilan"
                  + (f" (Amazon toplam {total})" if page == 1 and total else "")
                  + f" | aralıkta {band_seen}", flush=True)
            if not res["has_next"] or page >= page_cap:
                if total and band_seen < total and b - a + 1 > MIN_WIDTH:
                    st["stack"] += _split(a, b, 2, asc)
                    log.append(f"{a}-{b} TL: {band_seen} görüldü, Amazon {total} bildirdi; ikiye bölündü")
                elif total and band_seen < total:
                    log.append(f"{a}-{b} TL: bölünemeyecek kadar dar; {total - band_seen} ilan görülemedi")
                break
            page += 1
    if not st["stack"]:
        st["last_cycle"] = {"started": st["cycle_started"], "finished": ts, "pages": st["cycle_pages"],
                            "ilan": st["cycle_seen"], "range": st["range"],
                            "kontrol_bilinen": len(known_ok),
                            "kontrol_bulunan": len([a for a in known_ok if a in st["cycle_ok_seen"]]),
                            "kontrol_kacan": [a for a in known_ok if a not in st["cycle_ok_seen"]]}
        log.append(f"Kategori döngüsü tamamlandı: {st['cycle_pages']} sayfa, {st['cycle_seen']} ilan")
    return pages


# ---------------------------------------------------------------- anahtar kelime adımı
def keyword_step(f, cfg, meta, seen, src, log, budget, ts, known_ok):
    queries = auto_queries(cfg)
    st = meta.setdefault("kw", {})
    if st.get("queries") != queries or st.get("i", 0) >= len(queries):
        st.update(queries=queries, i=0, page=1, cycle_started=ts, cycle_pages=0, cycle_ok_seen=[])
    pages = 0
    while st["i"] < len(queries) and pages < budget:
        q = queries[st["i"]]
        try:
            html = f.page_url(keyword_url(q, st["page"]))
        except Blocked:
            raise
        pages += 1
        st["cycle_pages"] += 1
        res = parse_search_page(html) if html else None
        if not res or not res["items"]:
            if html and st["page"] == 1 and res is None:
                save_debug(f"arama_{q.replace(' ', '_')}_1.html", html)
            st["i"], st["page"] = st["i"] + 1, 1
            continue
        for asin, title, price in res["items"]:
            if asin not in seen:
                seen[asin] = (title, price)
            src.setdefault(asin, set()).add("anahtar kelime")
            if asin in known_ok and asin not in st["cycle_ok_seen"]:
                st["cycle_ok_seen"].append(asin)
        print(f"  [sorgu '{q}'] sayfa {st['page']}: {len(res['items'])} ilan", flush=True)
        if not res["has_next"] or st["page"] >= cfg["search_pages"]:
            st["i"], st["page"] = st["i"] + 1, 1
        else:
            st["page"] += 1
    if st["i"] >= len(queries):
        st["last_cycle"] = {"started": st["cycle_started"], "finished": ts, "pages": st["cycle_pages"],
                            "sorgu": len(queries), "kontrol_bilinen": len(known_ok),
                            "kontrol_bulunan": len([a for a in known_ok if a in st["cycle_ok_seen"]]),
                            "kontrol_kacan": [a for a in known_ok if a not in st["cycle_ok_seen"]]}
        log.append(f"Anahtar kelime döngüsü tamamlandı: {len(queries)} sorgu, {st['cycle_pages']} sayfa")
    return pages


# ---------------------------------------------------------------- ana giriş
def discover(f, cfg, meta, products, queue, ts, sweep_budget=None, kw_budget=None):
    seen, log, src = {}, [], {}
    node = cfg.get("laptop_node") or meta.get("laptop_node")
    lo = cfg.get("sweep_min_tl", 60000)
    hi = int(cfg["max_price_tl"] * cfg.get("sweep_max_factor", 1.25))
    known_ok = [a for a, p in products.items() if p.get("status") == "onayli" and p.get("in_stock")
                and p.get("current_price") and lo * 100 <= p["current_price"] <= hi * 100]
    sweep_budget = cfg.get("sweep_pages_per_run", 30) if sweep_budget is None else sweep_budget
    kw_budget = cfg.get("keyword_pages_per_run", 15) if kw_budget is None else kw_budget
    cat_pages = kw_pages = 0
    try:
        if node and not meta.get("sweep", {}).get("filter_broken"):
            cat_pages = sweep_step(f, cfg, meta, node, seen, src, log, sweep_budget, ts, known_ok)
        elif not node:
            log.append("Dizüstü kategori numarası henüz öğrenilmedi; kategori taraması bekliyor")
        kw_pages = keyword_step(f, cfg, meta, seen, src, log, kw_budget, ts, known_ok)
    finally:
        _process(seen, cfg, products, queue, ts, meta, cat_pages, kw_pages, log)
    return len(seen)


def _process(seen, cfg, products, queue, ts, meta, cat_pages, kw_pages, log):
    new, skipped, rejected = 0, [], 0
    for asin, (title, _) in seen.items():
        if asin in products or asin in queue:
            continue
        why = specs.title_prefilter(title, cfg)
        if why:
            products[asin] = {"asin": asin, "title": title, "auto_status": "red", "status": "red",
                              "reasons": [why], "evaluated_at": ts, "first_seen": ts}
            rejected += 1
        elif worth_detail(title, cfg):
            queue[asin] = title
            new += 1
        else:
            skipped.append(title[:90])
    sw, kw = meta.get("sweep", {}), meta.get("kw", {})
    meta["discovery"] = {
        "at": ts, "kategori_sayfa": cat_pages, "anahtar_kelime_sayfa": kw_pages,
        "gorulen_ilan": len(seen), "yeni_aday": new, "basliktan_red": rejected,
        "atlanan": len(skipped), "atlanan_ornek": skipped[:15],
        "kategori_dongu": {"basladi": sw.get("cycle_started"), "sayfa": sw.get("cycle_pages", 0),
                           "kalan_aralik": len(sw.get("stack") or []), "son_tamamlanan": sw.get("last_cycle")},
        "anahtar_kelime_dongu": {"basladi": kw.get("cycle_started"), "sorgu": kw.get("i", 0),
                                 "toplam_sorgu": len(kw.get("queries") or []),
                                 "son_tamamlanan": kw.get("last_cycle")},
        "notlar": log[-30:],
    }
    if cat_pages or kw_pages:
        print(f"Keşif: kategori {cat_pages} sayfa, anahtar kelime {kw_pages} sayfa, {len(seen)} farklı ilan, "
              f"{new} yeni aday, {rejected} başlıktan red, {len(skipped)} atlandı", flush=True)
        if sw.get("stack") is not None:
            print(f"  Kategori döngüsü: {sw.get('cycle_pages', 0)} sayfa gezildi, {len(sw.get('stack') or [])} "
                  f"fiyat aralığı kaldı", flush=True)
        if kw.get("queries"):
            print(f"  Anahtar kelime döngüsü: {kw.get('i', 0)}/{len(kw['queries'])} sorgu", flush=True)
        for c, name in ((sw.get("last_cycle"), "kategori"), (kw.get("last_cycle"), "anahtar kelime")):
            if c:
                print(f"  Son tamamlanan {name} döngüsü: bilinen {c['kontrol_bilinen']} onaylı ürünün "
                      f"{c['kontrol_bulunan']} tanesi görüldü"
                      + (f"; görülmeyen: {', '.join(c['kontrol_kacan'])}" if c["kontrol_kacan"] else ""), flush=True)
        for line in log:
            print("  " + line, flush=True)
