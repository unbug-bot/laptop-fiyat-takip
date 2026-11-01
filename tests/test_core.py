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


def test_conflict_pending():
    st, r, _ = ev("Acer Predator 18 i9-14900HX RTX 4090 32GB DDR5 18\" 2560x1600 250Hz",
                  table=[("Ekran Yenileme Hızı", "120 Hz")])
    assert st == "beklemede" and "çelişiyor" in " ".join(r)


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
    assert j("ryzen 7 8845hs") == "kaldi" and j("core ultra 9 290hx") == "bilinmiyor"


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
