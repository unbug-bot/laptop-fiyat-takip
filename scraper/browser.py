"""Gerçek tarayıcı (Chromium) ile sayfa çekme.

Amazon.com.tr, JavaScript çalıştırmayan istemcilere iki tür sayfa gönderiyor:
  - Akamai doğrulama sayfası (bm-verify): tarayıcıda küçük bir hesap yapıp sonucu
    /_sec/verify adresine gönderen ve sayfayı yenileyen script.
  - Ürün alanı boş ürün sayfası.
Gerçek bir tarayıcı bu scriptleri normal kullanıcı gibi çalıştırır; ek bir "çözücü" yoktur.
"Alışverişe devam et" ara sayfasında ise butona tıklanır.
"""
import json
import os
import random
import time
from urllib.parse import quote_plus

from .fetch import BASE, Blocked, save_debug
from .parse import is_blocked

STATE_PATH = "state/browser_state.json"
READY = {
    "arama": 'div[data-component-type="s-search-result"], .s-no-outline, .s-main-slot',
    "urun": "#productTitle",
    "giris": "#nav-logo, #navbar",
}


class BrowserFetcher:
    def __init__(self, delay_range, headless=None):
        from playwright.sync_api import sync_playwright
        self.delay = delay_range
        self.requests = 0
        self.blocked = 0
        self.captcha_passed = 0
        self.consecutive_blocks = 0
        if headless is None:
            # GitHub'da xvfb-run ile görünür (headful) çalışır: HEADLESS=0
            headless = os.environ.get("HEADLESS", "1") != "0"
        self._pw = sync_playwright().start()
        # channel="chromium": ayrı "headless shell" yerine tam Chromium'un yeni başsız modu
        self.browser = self._pw.chromium.launch(
            headless=headless, channel="chromium",
            args=["--disable-blink-features=AutomationControlled"])
        major = self.browser.version.split(".")[0]
        ua = (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              f"Chrome/{major}.0.0.0 Safari/537.36")
        kwargs = dict(user_agent=ua, locale="tr-TR", timezone_id="Europe/Istanbul",
                      viewport={"width": 1366, "height": 850},
                      extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5"})
        if os.path.exists(STATE_PATH):
            kwargs["storage_state"] = STATE_PATH
            print(f"Tarayıcı oturumu yüklendi ({STATE_PATH}).")
        self.ctx = self.browser.new_context(**kwargs)
        # Görsel, yazı tipi ve video gereksiz: atlanır (scriptler ve XHR çalışır)
        self.ctx.route("**/*", lambda r: r.abort()
                       if r.request.resource_type in ("image", "media", "font") else r.continue_())
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(30000)

    # ------------------------------------------------ yardımcılar
    def _settle(self, tag):
        """Doğrulama sayfaları kendini yeniler; hedef içerik görünene kadar bekle."""
        deadline = time.time() + 25
        clicked = False
        while time.time() < deadline:
            try:
                if self.page.locator(READY[tag]).first.count():
                    return
                btn = self.page.locator('form[action*="validateCaptcha"] button[type="submit"]')
                if not clicked and btn.count():
                    btn.first.click()
                    clicked = True
                    self.captcha_passed += 1
                    print("  'Alışverişe devam et' butonuna tıklandı")
            except Exception:
                pass                    # sayfa o sırada yenileniyor olabilir
            self.page.wait_for_timeout(1000)

    def _get(self, url, tag):
        time.sleep(random.uniform(*self.delay))
        for attempt in range(2):
            self.requests += 1
            try:
                resp = self.page.goto(url, wait_until="domcontentloaded")
                status = resp.status if resp else 0
            except Exception as e:
                print(f"  sayfa açılamadı: {e}")
                time.sleep(5)
                continue
            if status == 404:
                return ""
            self._settle(tag)
            try:
                html = self.page.content()
            except Exception:           # içerik okunurken sayfa yenilendi
                self.page.wait_for_timeout(3000)
                html = self.page.content()
            if is_blocked(html) or status in (429, 503):
                self.blocked += 1
                self.consecutive_blocks += 1
                print(f"  engel ({tag}): HTTP {status} {url}")
                if self.blocked <= 3:
                    save_debug(f"engel_tarayici_{tag}_{self.blocked}.html", html)
                if self.consecutive_blocks >= 3:
                    raise Blocked(url)
                time.sleep(20 + 20 * attempt)
                continue
            self.consecutive_blocks = 0
            return html
        return None

    # ------------------------------------------------ Fetcher arayüzü
    def reset_streak(self):
        self.consecutive_blocks = 0

    def warmup(self):
        return self._get(BASE + "/", "giris") is not None

    def search(self, query, page):
        return self._get(f"{BASE}/s?k={quote_plus(query)}&page={page}", "arama")

    def product(self, asin):
        return self._get(f"{BASE}/dp/{asin}?th=1&psc=1", "urun")

    def offers(self, asin):
        return ""                       # teklif paneli adresi bu sitede 404; ürün sayfası kullanılır

    def save_cookies(self):
        try:
            os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
            self.ctx.storage_state(path=STATE_PATH)
        except Exception as e:
            print(f"Tarayıcı oturumu kaydedilemedi: {e}")

    def close(self):
        for fn in (self.ctx.close, self.browser.close, self._pw.stop):
            try:
                fn()
            except Exception:
                pass
