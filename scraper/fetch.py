import json
import os
import random
import time
from urllib.parse import quote_plus, urlencode

from curl_cffi import requests as creq

from .parse import is_blocked, parse_captcha_form

BASE = "https://www.amazon.com.tr"
DEBUG_DIR = "debug"
COOKIE_PATH = "state/cookies.json"


class Blocked(Exception):
    pass


class Fetcher:
    def __init__(self, delay_range):
        self.s = creq.Session(impersonate="chrome")
        self.s.headers.update({"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5"})
        self.delay = delay_range
        self.requests = 0
        self.blocked = 0
        self.captcha_passed = 0
        self.consecutive_blocks = 0
        self.load_cookies()

    # ------------------------------------------------ çerezler (turlar arası, GitHub önbelleğinde)
    def load_cookies(self):
        try:
            with open(COOKIE_PATH, encoding="utf-8") as f:
                for c in json.load(f):
                    self.s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
            print(f"Çerezler yüklendi ({COOKIE_PATH}).")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Çerezler okunamadı: {e}")

    def save_cookies(self):
        os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)
        data = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.s.cookies.jar if "amazon" in (c.domain or "")]
        with open(COOKIE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)

    # ------------------------------------------------ "Alışverişe devam et" sayfasını geç
    def _pass_interstitial(self, html, url):
        form = parse_captcha_form(html)
        if not form:
            return False
        time.sleep(random.uniform(2, 5))
        target = BASE + form["action"] + "?" + urlencode(form["params"])
        self.requests += 1
        try:
            r = self.s.get(target, timeout=30, headers={"Referer": url})
        except Exception as e:
            print(f"  doğrulama isteği başarısız: {e}")
            return False
        ok = r.status_code == 200 and not is_blocked(r.text)
        print(f"  'Alışverişe devam et' sayfası {'geçildi' if ok else 'geçilemedi'} (HTTP {r.status_code})")
        if ok:
            self.captcha_passed += 1
        return ok

    def _get(self, url, headers=None, tag="istek"):
        time.sleep(random.uniform(*self.delay))
        for attempt in range(3):
            self.requests += 1
            try:
                r = self.s.get(url, timeout=30, headers=headers)
            except Exception as e:  # ağ hatası
                print(f"  ağ hatası: {e}")
                time.sleep(5)
                continue
            if r.status_code == 404:
                return ""                      # sayfa/uç nokta yok: engel değil
            if r.status_code == 200 and is_blocked(r.text) and attempt < 2 \
                    and self._pass_interstitial(r.text, url):
                time.sleep(random.uniform(*self.delay))
                continue                       # aynı sayfayı tekrar iste
            if r.status_code in (503, 429) or is_blocked(r.text):
                self.blocked += 1
                self.consecutive_blocks += 1
                print(f"  engel ({tag}): HTTP {r.status_code} {url}")
                if self.blocked <= 2:
                    save_debug(f"engel_{tag}_{self.blocked}_{r.status_code}.html", r.text)
                if self.consecutive_blocks >= 3:
                    raise Blocked(url)
                time.sleep(30 + 30 * attempt)
                continue
            self.consecutive_blocks = 0
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}: {url}")
                return None
            return r.text
        return None

    def reset_streak(self):
        self.consecutive_blocks = 0

    def warmup(self):
        return self._get(BASE + "/", tag="giris") is not None

    def search(self, query, page):
        return self._get(f"{BASE}/s?k={quote_plus(query)}&page={page}", tag="arama")

    def product(self, asin):
        return self._get(f"{BASE}/dp/{asin}?th=1&psc=1", tag="urun")

    def offers(self, asin):
        headers = {"Referer": f"{BASE}/dp/{asin}", "X-Requested-With": "XMLHttpRequest",
                   "Accept": "text/html,*/*"}
        return self._get(f"{BASE}/gp/aod/ajax/ref=auto_load_aod?asin={asin}&pc=dp",
                         headers=headers, tag="teklif")


def save_debug(name, html):
    if not html:
        return
    os.makedirs(DEBUG_DIR, exist_ok=True)
    with open(os.path.join(DEBUG_DIR, name), "w", encoding="utf-8") as f:
        f.write(html)
