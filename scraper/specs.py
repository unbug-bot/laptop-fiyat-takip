"""Ürün özelliklerini birden çok kaynaktan çıkarır ve katı filtreyle değerlendirir.

Her özellik için her kaynaktan bulunan değerler toplanır. Karar kuralı:
  - Hiç değer yok                         -> "bilinmiyor" (ürün beklemeye düşer)
  - Tanınmayan model (ör. yeni bir GPU)    -> "bilinmiyor"
  - Tüm değerler filtreyi geçiyor          -> "gecti"
  - Tüm değerler filtreyi geçmiyor         -> "kaldi"  (ürün reddedilir)
  - Değerler birbiriyle çelişiyor          -> "celiski" (ürün beklemeye düşer)
"""
import re

RULES_VERSION = 3  # değişince bekleyen ve detaydan reddedilen ürünler yeniden değerlendirilir

ATTRS = ["gpu", "cpu", "ekran_boyutu", "cozunurluk", "yenileme", "ram"]
ATTR_LABELS = {
    "gpu": "Ekran kartı", "cpu": "İşlemci", "ekran_boyutu": "Ekran boyutu",
    "cozunurluk": "Çözünürlük", "yenileme": "Yenileme hızı", "ram": "RAM",
}

REFURB_WORDS = ["yenilenmiş", "yenilendi", "renewed", "refurbished", "ikinci el",
                "kullanılmış", "outlet", "teşhir"]
CAMERA_WORDS = ["kamera", "webcam", "camera"]
PORT_WORDS = ["hdmi", "harici", "monitör", "monitor", "çıkış", "displayport", "thunderbolt"]


def norm(text):
    if not text:
        return ""
    t = text.replace("İ", "i").replace("™", "").replace("®", "")
    t = t.replace("\u200e", "").replace("\u200f", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", t.lower()).strip()


def fix_dotless(t):
    """Satıcıların 'Aı', 'Msı', 'İ7' gibi yazımlarını özellik aramaları için düzeltir.
    Yalnızca değer aramada kullanılır; anahtar ve kelime listesi eşleşmesi norm() ile kalır."""
    return t.replace("ı", "i")


def _num(s):
    return float(s.replace(",", "."))


def _context_ok(text, start, end, check_ports):
    window = text[max(0, start - 25): end + 25]
    if any(w in window for w in CAMERA_WORDS):
        return False
    if check_ports and any(w in window for w in PORT_WORDS):
        return False
    return True


# ---------------------------------------------------------------- GPU
RE_NVIDIA = re.compile(r"(?<![a-z0-9])rtx\s*-?\s*(\d{4})\s*(ti)?(?![a-z0-9])")
RE_AMD = re.compile(r"(?<![a-z0-9])rx\s*-?\s*(\d{4})\s*(m|s)?\s*(xt)?(?![a-z0-9])")
RE_ARC_DGPU = re.compile(r"arc\s*(a\d{3}m)(?![a-z0-9])")


def find_gpu(text, check_ports=False):
    out = []
    for m in RE_NVIDIA.finditer(text):
        out.append(f"rtx {m.group(1)}" + (" ti" if m.group(2) else ""))
    for m in RE_AMD.finditer(text):
        out.append(f"rx {m.group(1)}{m.group(2) or ''}" + (" xt" if m.group(3) else ""))
    for m in RE_ARC_DGPU.finditer(text):
        out.append(f"arc {m.group(1)}")
    return out


def judge_gpu(v, cfg):
    if v in cfg["gpu_whitelist"]:
        return "gecti"
    if v.startswith("rtx") or v.startswith("arc"):
        return "kaldi"            # NVIDIA ve Intel Arc laptop serileri tamamen biliniyor
    m = re.match(r"rx (\d{4})(m|s)?( xt)?$", v)
    if m:
        num, suf, xt = int(m.group(1)), m.group(2), m.group(3)
        if num < 7600:
            return "kaldi"
        if num == 7600 and suf in ("m", "s") and not xt:
            return "kaldi"
    return "bilinmiyor"           # tanınmayan AMD modeli: manuel onaya düşer


# ---------------------------------------------------------------- CPU
RE_ULTRA = re.compile(r"(?<![a-z0-9])(?:core\s*)?ultra\s*(?:core\s*)?([579])\s*[-\s]?\s*(\d{3})\s*(hx|h|v|u)?(?![a-z0-9])")
RE_CORE_I = re.compile(r"(?<![a-z0-9])(?:core\s*)?i([3579])\s*[-\s]?\s*(\d{4,5})\s*(hx|hk|h|u|p|k)?(?![a-z0-9])")
RE_RYZEN = re.compile(r"(?<![a-z0-9])ryzen\s*([3579])\s*[-\s]?\s*(?:pro\s*)?(\d{3,4})\s*(hx3d|hx|hs|h|u)?(?![a-z0-9])")
RE_CORE_N = re.compile(r"(?<![a-z0-9])(?<!ultra )(?<!ultra)core\s*([3579])\s*[-\s]?\s*(\d{3})\s*(hx|h|u|p)?(?![a-z0-9])")
RE_CORE_I_ARROW = re.compile(r"(?<![a-z0-9])(?:core\s*)?i([579])\s*[-\s]?\s*(2\d{2})\s*(hx|h)(?![a-z0-9])")
RE_RYZEN_AI = re.compile(r"(?<![a-z0-9])ryzen\s*ai\s*(max\+?\s*)?(?:([3579])\s*[-\s]?\s*)?(?:hx\s*[-\s]?\s*)?(\d{3})(?![a-z0-9])")


def find_cpu(text, check_ports=False):
    out = []
    for m in RE_ULTRA.finditer(text):
        out.append(f"core ultra {m.group(1)} {m.group(2)}{m.group(3) or ''}")
    for m in RE_CORE_I.finditer(text):
        out.append(f"core i{m.group(1)}-{m.group(2)}{m.group(3) or ''}")
    for m in RE_CORE_N.finditer(text):
        out.append(f"core {m.group(1)} {m.group(2)}{m.group(3) or ''}")
    for m in RE_CORE_I_ARROW.finditer(text):
        # 3 haneli 2xx numaralı Core i işlemcisi yoktur; satıcı Core Ultra'yı "i9-275HX" diye yazmış
        out.append(f"core ultra {m.group(1)} {m.group(2)}{m.group(3)}")
    for m in RE_RYZEN.finditer(text):
        out.append(f"ryzen {m.group(1)} {m.group(2)}{m.group(3) or ''}")
    for m in RE_RYZEN_AI.finditer(text):
        tier = "max" if m.group(1) else (m.group(2) or "?")
        out.append(f"ryzen ai {tier} {m.group(3)}")
    return out


def judge_cpu(v, cfg):
    if v in cfg["cpu_whitelist"]:
        return "gecti"
    if v.startswith("core i"):
        return "kaldi"
    m = re.match(r"core ultra (\d) (\d)(\d{2})(\w*)$", v)
    if m:
        tier, gen, num, suf = m.group(1), m.group(2), int(m.group(2) + m.group(3)), m.group(4)
        if tier == "5" or suf != "hx":
            return "kaldi"
        if gen == "2" and tier == "9":
            return "gecti"            # Ultra 9 2xxHX: 275HX/285HX/290HX Plus, hepsi 255HX ve 8940HX'ten iyi
        if gen == "2" and tier == "7" and num >= 255:
            return "gecti"            # 255HX ve üstü (270HX Plus dahil)
        return "bilinmiyor"
    m = re.match(r"core (\d) \d{3}(\w*)$", v)
    if m:
        return "bilinmiyor" if m.group(2) == "hx" else "kaldi"   # Core 7 240H, Core 5 210H vb.
    m = re.match(r"ryzen ai (\w+) \d{3}$", v)
    if m:
        # Ryzen AI 5/7/9 (350, 365, HX 370...): en fazla 12 çekirdek, 16 çekirdekli 8940HX'in altında
        return "bilinmiyor" if m.group(1) == "max" else "kaldi"
    m = re.match(r"ryzen (\d) (\d{3,4})(\w*)$", v)
    if m:
        if m.group(1) in ("3", "5", "7"):
            return "kaldi"
        if len(m.group(2)) == 3 or m.group(3) not in ("hx", "hx3d"):
            return "kaldi"
        return "bilinmiyor"
    return "bilinmiyor"


# ---------------------------------------------------------------- Ekran boyutu
RE_INCH = re.compile(r"(?<![\d.,])(\d{2}(?:[.,]\d{1,2})?)\s*(?:inç|inc|ınç|inch|in(?![a-zçğıöşü])|\"|”|″|'')")
RE_CM = re.compile(r"(?<![\d.,])(\d{2}(?:[.,]\d{1,2})?)\s*(?:cm|santimetre)")


def find_size(text, check_ports=False, allow_cm=False):
    out = []
    for m in RE_INCH.finditer(text):
        v = _num(m.group(1))
        if 10 <= v <= 21:
            out.append(round(v, 1))
    if allow_cm:
        for m in RE_CM.finditer(text):
            v = _num(m.group(1))
            if 25 <= v <= 55:
                out.append(round(v / 2.54, 1))
    return out


def judge_size(v, cfg):
    return "gecti" if v >= cfg["min_screen_inch"] else "kaldi"


# ---------------------------------------------------------------- Çözünürlük
RE_RES = re.compile(r"(?<!\d)(\d{3,4})\s*[x×\*]\s*(\d{3,4})(?!\d)")
RES_KEYWORDS = {
    "wqxga": "gecti", "wqhd+": "gecti", "wqhd": "gecti", "qhd+": "gecti", "qhd": "gecti",
    "1440p": "gecti", "2.5k": "gecti", "2,5k": "gecti", "3k": "gecti", "uhd+": "gecti",
    "uhd": "gecti", "4k": "gecti", "2160p": "gecti",
    "fhd+": "kaldi", "fhd": "kaldi", "full hd": "kaldi", "wuxga": "kaldi",
    "1080p": "kaldi", "hd+": "kaldi",
}
# Sağında harf/rakam olmamalı; ama satıcıların bitişik yazdığı "QHD+IPS", "QHDIPS", "4KOLED" kabul edilir
RE_RES_KW = re.compile(r"(?<![a-z0-9])(" + "|".join(
    re.escape(k) for k in sorted(RES_KEYWORDS, key=len, reverse=True)) + r")(?=$|[^a-z0-9+]|ips|oled|led|ekran|panel)")


def find_res(text, check_ports=False):
    out = []
    for m in RE_RES.finditer(text):
        w, h = int(m.group(1)), int(m.group(2))
        if w < h:
            w, h = h, w
        if w >= 640 and _context_ok(text, m.start(), m.end(), check_ports):
            out.append(f"{w}x{h}")
    for m in RE_RES_KW.finditer(text):
        if _context_ok(text, m.start(), m.end(), check_ports):
            out.append(m.group(1))
    return out


def judge_res(v, cfg):
    if v in RES_KEYWORDS:
        return RES_KEYWORDS[v]
    w, h = map(int, v.split("x"))
    mw, mh = cfg["min_resolution"]
    return "gecti" if (w >= mw and h >= mh) else "kaldi"


# ---------------------------------------------------------------- Yenileme hızı
RE_HZ = re.compile(r"(?<![\d.,])(\d{2,3})\s*hz(?![a-z])")


def find_hz(text, check_ports=False):
    out = []
    for m in RE_HZ.finditer(text):
        v = int(m.group(1))
        if 30 <= v <= 600 and _context_ok(text, m.start(), m.end(), check_ports):
            out.append(v)
    return out


def judge_hz(v, cfg):
    return "gecti" if v >= cfg["min_refresh_hz"] else "kaldi"


# ---------------------------------------------------------------- RAM
RE_RAM_TEXT = re.compile(r"(?<![\d.,])(\d{1,3})\s*gb\s*(?:(?:lp)?ddr\d\w*|ram|bellek(?!i))(?![a-z])")
RE_GB = re.compile(r"(?<![\d.,])(\d{1,3})\s*gb")


def find_ram(text, check_ports=False):
    out = []
    for m in RE_RAM_TEXT.finditer(text):
        # "8gb gddr7" eşleşmez çünkü desen 'g' ile başlayan gddr'yi kabul etmez
        v = int(m.group(1))
        if 4 <= v <= 256:
            out.append(v)
    return out


def judge_ram(v, cfg):
    return "gecti" if v >= cfg["min_ram_gb"] else "kaldi"


JUDGES = {"gpu": judge_gpu, "cpu": judge_cpu, "ekran_boyutu": judge_size,
          "cozunurluk": judge_res, "yenileme": judge_hz, "ram": judge_ram}
TEXT_FINDERS = {"gpu": find_gpu, "cpu": find_cpu, "ekran_boyutu": find_size,
                "cozunurluk": find_res, "yenileme": find_hz, "ram": find_ram}


# ---------------------------------------------------------------- Tablo anahtarları
def _table_attr(key):
    k = norm(key)
    if any(x in k for x in ("grafik", "ekran kartı", "gpu", "graphics")):
        return None if re.search(r"ram|bellek|boyut|hız|marka", k) else "gpu"
    if "işlemci" in k or "cpu" in k or "processor" in k:
        return None if any(x in k for x in ("hız", "sayı", "çekirdek", "marka", "soket")) else "cpu"
    if "ekran boyutu" in k or "ekran ebadı" in k or "screen size" in k:
        return "ekran_boyutu"
    if "çözünürl" in k or "resolution" in k:
        return "cozunurluk"
    if "yenileme" in k or "refresh" in k:
        return "yenileme"
    if (re.search(r"(?<![a-z])ram(?![a-z])", k) or "bellek" in k) and not any(
            x in k for x in ("maks", "desteklenen", "tür", "teknoloji", "hız", "yuva", "flash", "depolama")):
        return "ram"
    return None


def _table_values(attr, value):
    v = fix_dotless(norm(value))
    if attr == "ekran_boyutu":
        return find_size(v, allow_cm=True)
    if attr == "ram":
        return [int(m.group(1)) for m in RE_GB.finditer(v) if 4 <= int(m.group(1)) <= 256][:1]
    if attr == "yenileme":
        found = find_hz(v)
        if not found and re.fullmatch(r"\d{2,3}", v):
            found = [int(v)]
        return found
    return TEXT_FINDERS[attr](v)


# ---------------------------------------------------------------- Ana değerlendirme
def collect(title, table, bullets):
    """Her özellik için kaynak -> değerler sözlüğü üretir."""
    found = {a: {} for a in ATTRS}
    t = fix_dotless(norm(title))
    for a in ATTRS:
        vals = TEXT_FINDERS[a](t, check_ports=False)
        if vals:
            found[a]["başlık"] = vals
    for key, value in table:
        a = _table_attr(key)
        if a:
            vals = _table_values(a, value)
            if vals:
                found[a].setdefault("teknik tablo", []).extend(vals)
    for b in bullets:
        nb = fix_dotless(norm(b))
        for a in ATTRS:
            vals = TEXT_FINDERS[a](nb, check_ports=True)
            if vals:
                found[a].setdefault("açıklama", []).extend(vals)
    return found


def judge_attr(attr, sources, cfg):
    allvals = []
    for vals in sources.values():
        allvals.extend(vals)
    if not allvals:
        return "bilinmiyor", "hiçbir kaynakta bulunamadı"
    verdicts = {v: JUDGES[attr](v, cfg) for v in allvals}
    unknown = [str(v) for v, j in verdicts.items() if j == "bilinmiyor"]
    if unknown:
        return "bilinmiyor", "tanınmayan değer: " + ", ".join(sorted(set(unknown)))
    kinds = set(verdicts.values())
    if kinds == {"gecti"}:
        return "gecti", None
    if kinds == {"kaldi"}:
        return "kaldi", "filtreyi geçmiyor: " + ", ".join(sorted({str(v) for v in allvals}))
    detail = "; ".join(f"{src}: {', '.join(sorted({str(x) for x in vals}))}" for src, vals in sources.items())
    return "celiski", "kaynaklar çelişiyor (" + detail + ")"


def evaluate(title, table, bullets, breadcrumb, cfg):
    """Sonuç: (durum, gerekçeler, özellik özeti). durum: onayli | beklemede | red"""
    nt = norm(title)
    for w in REFURB_WORDS:
        if w in nt:
            return "red", [f"başlıkta '{w}' geçiyor (ikinci el/yenilenmiş)"], {}
    nb = norm(breadcrumb)
    if nb and not any(x in nb for x in ("dizüstü", "laptop", "notebook")):
        return "red", [f"kategori dizüstü bilgisayar değil ({breadcrumb.strip()[:80]})"], {}

    found = collect(title, table, bullets)
    summary, why_by, verdicts = {}, {}, {}
    for a in ATTRS:
        # Başlıkta değer varsa yalnızca başlık esas alınır; diğer kaynaklar sadece eksik bilgiyi tamamlar
        if "başlık" in found[a]:
            found[a] = {"başlık": found[a]["başlık"]}
        verdict, why = judge_attr(a, found[a], cfg)
        verdicts[a] = verdict
        vals = sorted({str(v) for src in found[a].values() for v in src})
        summary[a] = {"degerler": vals, "karar": verdict}
        if why:
            why_by[a] = f"{ATTR_LABELS[a]}: {why}"
    failed = [why_by[a] for a in ATTRS if verdicts[a] == "kaldi"]
    if failed:
        return "red", failed, summary
    if all(v == "gecti" for v in verdicts.values()):
        return "onayli", [], summary
    return "beklemede", [why_by[a] for a in ATTRS if a in why_by], summary


def title_prefilter(title, cfg):
    """Yalnızca arama sonucu başlığından kesin red kararı verilebiliyorsa gerekçeyi döndürür."""
    nt = norm(title)
    for w in REFURB_WORDS:
        if w in nt:
            return f"başlıkta '{w}' geçiyor (ikinci el/yenilenmiş)"
    found = collect(title, [], [])
    for a in ATTRS:
        verdict, why = judge_attr(a, found[a], cfg)
        if verdict == "kaldi":
            return f"{ATTR_LABELS[a]}: {why} (arama başlığından)"
    return None
