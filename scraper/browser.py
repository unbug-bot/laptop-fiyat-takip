"""Mac'te gerçek Chrome ile sayfa çekme.

- Bu programa özel kalıcı bir Chrome profili (state/chrome-profil) görünür pencerede açılır.
  Bilgisayardaki Google Chrome kullanılır; yoksa Playwright'ın Chromium'u.
- "Alışverişe devam et" butonuna program kendisi tıklar.
- Amazon başka bir doğrulama isterse (resimli CAPTCHA, boş sayfa vb.) Mac bildirimi gelir, pencere öne
  çıkar; kullanıcı çözer ya da sayfayı yeniler, tarama kaldığı yerden devam eder.
"""
import os
import random
import subprocess
import time
from urllib.parse import quote_plus

from .fetch import BASE, Blocked, save_debug
from .parse import is_blocked

PROFILE_DIR = "state/chrome-profil"
READY = {
    "arama": 'div[data-component-type="s-search-result"], .s-main-slot',
    "urun": "#productTitle",
    "giris": "#nav-logo, #navbar",
}


def notify(title, text):
    """Mac bildirimi + ses; ayrıca terminale yazar."""
    print(f"\n*** {title}: {text}\n", flush=True)
    script = f'display notification "{text}" with title "{title}" sound name "Glass"'
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass                                # osascript yoksa (Mac dışı test ortamı) yalnızca terminal


class BrowserFetcher:
    def __init__(self, delay_range, human_wait_min=15, on_human_needed=None,
                 profile_dir=PROFILE_DIR, debug_dir="debug"):
        from playwright.sync_api import sync_playwright
        self.profile_dir, self.debug_dir = profile_dir, debug_dir
        self.delay = delay_range
        self.human_wait = human_wait_min * 60
        self.on_human_needed = on_human_needed
        self.requests = self.blocked = self.captcha_passed = self.consecutive_blocks = 0
        self.human_solved = 0
        self._pw = sync_playwright().start()
        self._open()

    # ------------------------------------------------ tarayıcıyı aç
    def _open(self):
        os.makedirs(self.profile_dir, exist_ok=True)
        last_err = None
        for channel in ("chrome", "chromium"):          # önce bilgisayardaki Google Chrome
            try:
                self.ctx = self._pw.chromium.launch_persistent_context(
                    self.profile_dir, channel=channel, headless=False, no_viewport=True,
                    locale="tr-TR", timezone_id="Europe/Istanbul",
                    args=["--disable-blink-features=AutomationControlled"])
                print(f"Tarayıcı açıldı ({'Google Chrome' if channel == 'chrome' else 'Chromium'}).")
                break
            except Exception as e:
                last_err = e
        else:
            raise last_err
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.set_default_timeout(30000)

    def alive(self):
        try:
            return not self.page.is_closed() and self.page.evaluate("1") == 1
        except Exception:
            return False

    def reopen_if_closed(self):
        """Kullanıcı pencereyi kapattıysa yeniden aç."""
        if not self.alive():
            print("Tarayıcı penceresi kapanmış; yeniden açılıyor.")
            try:
                self.ctx.close()
            except Exception:
                pass
            self._open()

    # ------------------------------------------------ yardımcılar
    def _ready(self, tag):
        try:
            return self.page.locator(READY[tag]).first.count() > 0
        except Exception:
            return False

    def _settle(self, tag, seconds=25):
        """Doğrulama sayfaları kendini yeniler; hedef içerik görünene kadar bekle."""
        deadline = time.time() + seconds
        clicked = False
        while time.time() < deadline:
            if self._ready(tag):
                return
            try:
                form = self.page.locator('form[action*="alidateCaptcha"]')
                # Yalnızca kodu hazır gelen "Alışverişe devam et" formu otomatik geçilir;
                # resimli/yazılı CAPTCHA kullanıcıya bırakılır
                auto = form.count() and not form.locator('img, input[type="text"], input:not([type])').count()
                btn = form.locator('button[type="submit"], input[type="submit"]')
                if not clicked and auto and btn.count():
                    btn.first.click()
                    clicked = True
                    self.captcha_passed += 1
                    print("  'Alışverişe devam et' butonuna tıklandı")
            except Exception:
                pass
            self.page.wait_for_timeout(1000)

    def _content(self):
        for _ in range(3):
            try:
                return self.page.content()
            except Exception:              # sayfa o sırada yenileniyor
                self.page.wait_for_timeout(1500)
        return ""

    def _ask_human(self, url, tag):
        """Doğrulamayı kullanıcıya çözdürür. Çözülünce sayfanın içeriğini döndürür.
        human_wait_min=0 ise (GitHub'da, başında kimse yok) beklemeden vazgeçer."""
        if self.human_wait <= 0:
            print("  Amazon doğrulama istedi; bu ortamda çözecek kimse yok, bekleme yapılmadı.", flush=True)
            return None
        notify("Laptop fiyat takibi", "Amazon doğrulama istiyor. Chrome penceresinde çöz ya da sayfayı yenile.")
        if self.on_human_needed:
            try:
                self.on_human_needed()
            except Exception:
                pass
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        print(f"  Kullanıcı bekleniyor (en fazla {self.human_wait // 60} dakika)...", flush=True)
        deadline = time.time() + self.human_wait
        while time.time() < deadline:
            self.page.wait_for_timeout(3000)
            if not self.alive():
                self.reopen_if_closed()
                return None
            html = self._content()
            if not html or is_blocked(html):
                continue
            # Doğrulama geçildi: Amazon başka sayfaya yönlendirmiş olabilir, hedefi tekrar aç
            if not self._ready(tag):
                try:
                    self.page.goto(url, wait_until="domcontentloaded")
                except Exception:
                    continue
                self._settle(tag, 15)
                html = self._content()
                if is_blocked(html) or not self._ready(tag):
                    continue
            self.human_solved += 1
            print("  Doğrulama çözüldü, tarama devam ediyor.", flush=True)
            return html
        print("  Süre doldu, doğrulama çözülmedi.")
        return None

    def _get(self, url, tag):
        time.sleep(random.uniform(*self.delay))
        for attempt in range(2):
            self.requests += 1
            try:
                resp = self.page.goto(url, wait_until="domcontentloaded")
                status = resp.status if resp else 0
            except Exception as e:
                print(f"  sayfa açılamadı: {e}")
                self.reopen_if_closed()
                time.sleep(5)
                continue
            if status == 404:
                return ""
            self._settle(tag)
            html = self._content()
            if not (is_blocked(html) or status in (429, 503)):
                if not self._ready(tag) and tag != "giris" and attempt == 0:
                    continue                # başka sayfaya yönlendirildi: hedefi bir kez daha aç
                self.consecutive_blocks = 0
                return html
            self.blocked += 1
            print(f"  engel ({tag}): HTTP {status} {url}")
            if self.blocked <= 3:
                save_debug(f"engel_tarayici_{tag}_{self.blocked}.html", html, self.debug_dir)
            html = self._ask_human(url, tag)
            if html is not None:
                self.consecutive_blocks = 0
                return html
            raise Blocked(url)              # kullanıcı çözmedi: bu tur burada biter
        return None

    # ------------------------------------------------ main.py'nin kullandığı arayüz
    def reset_streak(self):
        self.consecutive_blocks = 0

    def warmup(self):
        return self._get(BASE + "/", "giris") is not None

    def product(self, asin):
        return self._get(f"{BASE}/dp/{asin}?th=1&psc=1", "urun")

    def page_url(self, url):
        return self._get(url, "arama")

    def offers(self, asin):
        return None                     # teklif paneli kullanılmıyor (amazon.com.tr'de 404); fiyat ürün sayfasından

    def save_cookies(self):
        pass                            # kalıcı profil kendini kaydeder

    def close(self):
        for fn in (self.ctx.close, self._pw.stop):
            try:
                fn()
            except Exception:
                pass
