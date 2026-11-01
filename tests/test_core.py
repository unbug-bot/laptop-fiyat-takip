import yaml
from scraper import specs
from scraper.parse import parse_offers, parse_product, parse_search, parse_price_tl, offer_is_new
from scraper.main import apply_observation, apply_overrides

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def ev(title, table=(), bullets=(), bc="Bilgisayar › Dizüstü Bilgisayarlar"):
    return specs.evaluate(title, list(table), list(bullets), bc, CFG)


def test_full_pass():
    st, r, _ = ev("MSI Vector 17 HX AI, Intel Core Ultra 9 275HX, RTX 5070 Ti, 32GB DDR5, 17\" QHD+ 2560x1600 240Hz",
                  table=[("Standart Ekran Boyutu", "43,9 Santimetre"), ("RAM Boyutu", "32 GB")])
    assert st == "onayli", r


def test_size_fail():
    st, r, _ = ev("ASUS TUF A16 Ryzen 9 8940HX RTX 5070 16 inç QHD 165Hz 16GB DDR5")
    assert st == "red" and "Ekran boyutu" in r[0]


def test_fhd_fail():
    assert ev("Lenovo Legion 7i 18\" FHD+ 1920x1200 165Hz Ultra 9 275HX RTX 5080 32GB RAM")[0] == "red"


def test_camera_fhd_ignored():
    st, r, _ = ev("Legion Pro 7 Ultra 9 275HX RTX 5080 32GB DDR5 17.3\" 2560x1600 240Hz",
                  bullets=["FHD 1080p IR kamera ile güvenli giriş"])
    assert st == "onayli", r


def test_missing_refresh_pending():
    st, r, _ = ev("HP Omen 17 i9-14900HX RTX 4080 32GB RAM 17.3 inç QHD")
    assert st == "beklemede" and any("Yenileme" in x for x in r)


def test_conflict_pending_only_when_title_silent():
    # Başlıkta Hz yok; tablo ve açıklama çelişiyor -> beklemede
    st, r, _ = ev("Acer Predator 18 i9-14900HX RTX 4090 32GB DDR5 18\" 2560x1600",
                  table=[("Ekran Yenileme Hızı", "120 Hz")], bullets=["250Hz yenileme hızı"])
    assert st == "beklemede" and "çelişiyor" in " ".join(r)


def test_title_wins():
    st, r, _ = ev("Acer Predator 18 i9-14900HX RTX 4090 32GB DDR5 18\" 2560x1600 250Hz",
                  table=[("Ekran Yenileme Hızı", "120 Hz"), ("Grafik Kartı Açıklaması", "RTX 4060")])
    assert st == "onayli", r


def test_gpu_rules():
    j = lambda g: specs.judge_gpu(g, CFG)
    assert j("rtx 4070") == "kaldi" and j("rtx 5060") == "gecti" and j("rtx 4080") == "gecti"
    assert j("rx 7600m xt") == "gecti" and j("rx 7600s") == "kaldi" and j("rx 9070m") == "bilinmiyor"
    assert specs.find_gpu(specs.norm("NVIDIA GeForce RTX™ 5070Ti 12GB GDDR7")) == ["rtx 5070 ti"]
    assert specs.find_gpu(specs.norm("AMD Radeon RX7600M XT")) == ["rx 7600m xt"]


def test_cpu_rules():
    j = lambda c: specs.judge_cpu(c, CFG)
    assert specs.find_cpu(specs.norm("Intel® Core™ Ultra 7 255HX")) == ["core ultra 7 255hx"]
    assert specs.find_cpu(specs.norm("Intel Core i9-14900HX")) == ["core i9-14900hx"]
    assert specs.find_cpu(specs.norm("AMD Ryzen™ 9 8940HX")) == ["ryzen 9 8940hx"]
    assert j("core i7-14700hx") == "kaldi" and j("core ultra 7 155h") == "kaldi"
    assert j("ryzen 7 8845hs") == "kaldi" and j("core ultra 7 245hx") == "bilinmiyor" and j("core 7 240h") == "kaldi"


def test_ram_ignores_vram():
    assert specs.find_ram(specs.norm("RTX 5070 8GB GDDR7, 16GB DDR5 RAM")) == [16]
    st, r, _ = ev("Laptop i9-14900HX RTX 4090 8GB DDR5 18\" QHD 240Hz")
    assert st == "red" and "RAM" in r[0]


def test_refurb_red():
    assert ev("(Yenilenmiş) MSI Raider 18 i9-14900HX RTX 4090 32GB 18\" QHD 240Hz")[0] == "red"


def test_breadcrumb_red():
    assert ev("Ultra 9 275HX RTX 5080 32GB DDR5 18\" QHD 240Hz", bc="Bilgisayar › Monitörler")[0] == "red"


def test_price_parse():
    assert parse_price_tl("84.999,00 TL") == 8499900
    assert parse_price_tl("119.999 TL") == 11999900
    assert parse_price_tl("1.234,5 TL") == 123450


def test_offer_condition():
    assert offer_is_new("Yeni")
    assert not offer_is_new("Yenilenmiş")
    assert not offer_is_new("Kullanılmış - Çok iyi")
    assert not offer_is_new("")


AOD = """<div id="aod-container">
<div id="aod-pinned-offer"><div id="aod-offer-heading"><h5>Yenilenmiş</h5></div>
 <div id="aod-offer-price"><span class="a-price"><span class="a-offscreen">70.000,00 TL</span></span></div></div>
<div id="aod-offer-list">
 <div id="aod-offer"><div id="aod-offer-heading"><h5>Yeni</h5></div>
  <div id="aod-offer-price"><span class="a-price a-text-price"><span class="a-offscreen">99.999,00 TL</span></span>
  <span class="a-price"><span class="a-offscreen">92.500,00 TL</span></span></div></div>
 <div id="aod-offer"><div id="aod-offer-heading"><h5>Kullanılmış - İyi</h5></div>
  <div id="aod-offer-price"><span class="a-price"><span class="a-offscreen">60.000,00 TL</span></span></div></div>
 <div id="aod-offer"><div id="aod-offer-heading"><h5>Yeni</h5></div>
  <div id="aod-offer-price"><span class="a-price"><span class="a-offscreen">94.000,00 TL</span></span></div></div>
</div></div>"""


def test_offers_only_new():
    r = parse_offers(AOD)
    assert sorted(r["new_prices"]) == [9250000, 9400000]


def test_offers_empty_and_unknown():
    assert parse_offers('<div id="aod-container"></div>')["new_prices"] == []
    assert parse_offers("<html><body>baska sayfa</body></html>") is None


def test_product_parse():
    html = """<span id="productTitle"> MSI Titan 18 </span>
    <div id="wayfinding-breadcrumbs_feature_div">Bilgisayar › Dizüstü Bilgisayarlar</div>
    <table id="productDetails_techSpec_section_1"><tr><th>Grafik Kartı Açıklaması</th><td>NVIDIA RTX 5090</td></tr>
    <tr><th>Grafik Kartı Ram Boyutu</th><td>24 GB</td></tr></table>
    <div id="feature-bullets"><ul><li>18 inç 3840x2400 120Hz Mini LED</li></ul></div>"""
    d = parse_product(html)
    assert d["title"] == "MSI Titan 18" and ("Grafik Kartı Açıklaması", "NVIDIA RTX 5090") in d["table"]
    found = specs.collect(d["title"], d["table"], d["bullets"])
    assert found["ram"] == {}     # VRAM, RAM sayılmaz


def test_search_parse():
    html = """<div class="s-main-slot"><div data-component-type="s-search-result" data-asin="B0ABCDEFGH">
    <h2><span>ASUS ROG Strix G18</span></h2></div></div>"""
    assert parse_search(html) == [("B0ABCDEFGH", "ASUS ROG Strix G18")]


def _p():
    return {"asin": "X", "title": "PC1", "status": "onayli"}


def test_alert_user_example():
    # 100x -> 110x -> 105x -> 96x; en düşük 96x. 86.4x ve altı bildirim.
    p, x = _p(), 100000  # x = 1.000 TL (kuruş)
    for v in (100, 110, 105, 96):
        assert apply_observation(p, v * x, True, "t", CFG) is None
    assert p["min_price"] == 96 * x and p["max_price"] == 110 * x
    import copy
    assert apply_observation(copy.deepcopy(p), 865 * x // 10, True, "t", CFG) is None  # 86.5x: eşik üstü
    a = apply_observation(p, 864 * x // 10, True, "t", CFG)                 # 86.4x tam eşik
    assert a and a["ref_min"] == 96 * x
    assert apply_observation(p, 80 * x, True, "t", CFG) is None             # yeni en düşük 86.4x; %10 değil


def test_first_price_after_stockout_no_alert():
    p = _p()
    apply_observation(p, None, False, "t", CFG)
    assert apply_observation(p, 50000 * 100, True, "t", CFG) is None


def test_restock_cheaper_alert():
    p = _p()
    apply_observation(p, 100000 * 100, True, "t", CFG)
    apply_observation(p, None, False, "t", CFG)
    a = apply_observation(p, 89000 * 100, True, "t", CFG)
    assert a and p["last_change_pct"] == -11.0


def test_no_alert_above_limit_or_pending():
    p = _p()
    apply_observation(p, 150000 * 100, True, "t", CFG)
    assert apply_observation(p, 130000 * 100, True, "t", CFG) is None   # %13 ama limit üstü
    q = dict(_p(), status="beklemede")
    apply_observation(q, 100000 * 100, True, "t", CFG)
    assert apply_observation(q, 80000 * 100, True, "t", CFG) is None


def test_overrides():
    db = {"products": {"A": {"auto_status": "beklemede", "status": "beklemede"},
                       "B": {"auto_status": "onayli", "status": "onayli"},
                       "C": {"auto_status": "red", "status": "red"}}}
    apply_overrides(db, {"approve": ["A", "C"], "reject": ["B"]})
    p = db["products"]
    assert p["A"]["status"] == "onayli" and p["B"]["status"] == "red" and p["C"]["status"] == "red"


def test_monster_ultra_core_order():
    assert specs.find_cpu(specs.norm("Intel Ultra Core 9 275HX")) == ["core ultra 9 275hx"]


def test_real_listing_game_garaj():
    st, r, _ = ev('Game Garaj Slayer R9M XL-5060 C2 AMD Ryzen 9 8940HX 32GB RAM 1TB SSD RTX5060 17" QHD+ 240Hz IPS FreeDOS Oyuncu Laptop')
    assert st == "onayli", r


def test_dotless_and_ryzen_ai():
    assert specs.find_cpu(specs.fix_dotless(specs.norm("Acer Nitro 18 Aı Ryzen Aı 9 365"))) == ["ryzen ai 9 365"]
    assert specs.judge_cpu("ryzen ai 7 350", CFG) == "kaldi"
    assert specs.judge_cpu("ryzen ai 9 365", CFG) == "kaldi"
    assert specs.judge_cpu("ryzen 7 260", CFG) == "kaldi"
    assert specs.find_cpu(specs.norm("Intel Core 7 240H")) == ["core 7 240h"]
    assert specs.find_cpu(specs.norm("INTEL I9-275HX")) == ["core ultra 9 275hx"]
    st, r, _ = ev('Hp Omen 17-DB1019NT Amd Ryzen AI 7-350 32GB 1TB SSD 8GB RTX 5070 Freedos 17.3" QHD')
    assert st == "red" and "İşlemci" in r[0]


def test_290hx_plus():
    assert specs.find_cpu(specs.norm("Intel Core Ultra 9 290HX Plus")) == ["core ultra 9 290hx"]
    assert specs.judge_cpu("core ultra 9 290hx", CFG) == "gecti"


def test_product_page_price():
    from scraper.parse import parse_product_price
    base = '<span id="productTitle">X</span><div id="rightCol">{}</div>'
    html = base.format('<div id="corePriceDisplay_desktop_feature_div"><span class="a-price a-text-price">'
                       '<span class="a-offscreen">115.499,00 TL</span></span><span class="a-price priceToPay">'
                       '<span class="a-offscreen">110.301,55 TL</span></span></div>'
                       '<div id="olpLinkWidget_feature_div">Yeni (2) 108.000,00 TL itibaren</div>')
    assert sorted(parse_product_price(html)["new_prices"]) == [10800000, 11030155]
    used = base.format('<div id="buybox">Kullanılmış - Çok iyi <span class="a-price"><span class="a-offscreen">70.000 TL</span></span></div>')
    assert parse_product_price(used) is None
    mixed = base.format('<div id="olpLinkWidget_feature_div">Yeni ve kullanılmış (3) 60.000,00 TL</div>'
                        '<div id="availability">Şu anda mevcut değil.</div>')
    assert parse_product_price(mixed)["new_prices"] == []


PENDING_CASES = [
    # (ASIN, başlık, tablo, açıklama, beklenen)
    ("B0HFKHSHNP", "GIGABYTE GAMING A18 Slim Gaming Notebook, 18 inç 165Hz QHD 16:10 3ms IPS Ekran, NVIDIA GeForce RTX 5070 8GB 85W, AMD Ryzen 7 260, 16GB DDR5 RAM, 1TB SSD, Windows 11 Home", [], [], "red"),
    ("B0FMFM78FC", 'Game Garaj Slayer R9M XL-5060 C2 AMD Ryzen 9 8940HX 32GB RAM 1TB SSD RTX5060 17" QHD+ 240Hz IPS FreeDOS Oyuncu Laptop', [("Grafik Kartı Açıklaması", "NVIDIA RTX 4060")], [], "onayli"),
    ("B0HFKD4RRQ", "GIGABYTE GAMING A18 PRO Slim Gaming Notebook, 18 inç 165Hz QHD 16:10 3ms IPS Ekran, NVIDIA GeForce RTX 5070Ti 12GB 115W, Intel Core 7 240H, 32GB DDR5 RAM, 1TB SSD, Windows 11 Home", [], [], "red"),
    ("B0FTM5PR7J", 'Hp Omen 17-DB1019NT Amd Ryzen AI 7-350 32GB 1TB SSD 8GB RTX 5070 Freedos 17.3" QHD Laptop CB4D7EA', [], [], "red"),
    ("B0HMNY3XQ6", 'Acer Nitro 18 Aı Ryzen Aı 9 365 Rtx 5070 8Gb 115W 32Gb Ram 1Tb Ssd 18" Wqxga (2560X1600) 165Hz Win11 Home Nh.Qyjey.002P321 Gaming Laptop + Per4 Çanta', [], [], "red"),
    ("B0FNWKBZXT", 'Game Garaj Slayer R9M3D XL-5070 C2 AMD Ryzen 9 9955HX3D 32GB RAM 1TB SSD RTX5070 17" QHD+ 240Hz IPS FreeDOS Oyuncu Laptop', [("Grafik Kartı Açıklaması", "RTX 4060")], [], "onayli"),
    ("B0H1MQBX3R", "MSI RAIDER A18 HX A8WHG-280TR Dizüstü Bilgisayar, RYZEN 9-8940HX, 18.0 QHD+ 240Hz, 32GB RAM DDR5, RTX5070TI GDDR7 12GB, 1TB SSD, W11", [("Standart Ekran Boyutu", "18 İnç")], ["UHD+ 120Hz", "RTX 5090, Ryzen 9 9955HX3D, 64GB"], "onayli"),
    ("B0H3V17TYK", "ASUS ROG Strix SCAR 18 G835LXG-TQ545 - Gaming Dizüstü Bilgisayarı | NVIDIA GeForce RTX 5090 Laptop GPU 24GB GDDR7 |Intel Core Ultra 9 290HX Plus Processor 2.7 GHz | 64GB DDR5 RAM | 2TB SSD| 18 inç|DOS", [("Ekran Çözünürlüğü", "3840 x 2400"), ("Ekran Yenileme Hızı", "240 Hz")], [], "onayli"),
    ("B0F9FVTN56", 'GIGABYTE NOTEBOOK GAMING INTEL 18" I9-275HX RTX5090 W11 AORUS MASTER 18 BZHC6TRD45SP QHD+/240HZ/64GB RAM/2TB NVME', [("Ekran Boyutu", "16 İnç"), ("Ekran Çözünürlüğü", "1920x1200")], [], "onayli"),
    ("B0H4LFBGRW", "ASUS ROG Strix Scar G18 G835LXG-TQ547 24GB GeForce RTX5090 Intel Core Ultra 9 290HX 64GB RAM 2TB SSD 18 inç 4K WQUXGA 240Hz", [], [], "onayli"),
]


def test_user_pending_list():
    for asin, title, table, bullets, want in PENDING_CASES:
        st, r, _ = ev(title, table=table, bullets=bullets)
        assert st == want, (asin, st, r)
