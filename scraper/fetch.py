import os
import random
import time
from urllib.parse import quote_plus

from curl_cffi import requests as creq

from .parse import is_blocked

BASE = "https://www.amazon.com.tr"
DEBUG_DIR = "debug"


class Blocked(Exception):
    pass


class Fetcher:
    def __init__(self, delay_range):
        self.s = creq.Session(impersonate="chrome")
        self.s.headers.update({"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5"})
        self.delay = delay_range
        self.requests = 0
        self.blocked = 0
        self.consecutive_blocks = 0

    def _get(self, url, headers=None, tag="istek"):
        time.sleep(random.uniform(*self.delay))
        for attempt in range(2):
            self.requests += 1
            try:
                r = self.s.get(url, timeout=30, headers=headers)
            except Exception as e:  # ağ hatası
                print(f"  ağ hatası: {e}")
                time.sleep(5)
                continue
            if r.status_code in (503, 429) or is_blocked(r.text):
                self.blocked += 1
                print(f"  engel ({tag}): HTTP {r.status_code} {url}")
                if self.blocked <= 2:
                    save_debug(f"engel_{tag}_{self.blocked}_{r.status_code}.html", r.text)
                self.consecutive_blocks += 1
                if self.consecutive_blocks >= 3:
                    raise Blocked(url)
                time.sleep(15 + 15 * attempt)
                continue
            self.consecutive_blocks = 0
            if r.status_code == 404:
                return ""
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
