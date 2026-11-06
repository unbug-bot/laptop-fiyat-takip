"""Mac'te sürekli çalışma (baslat.command bunu çalıştırır).

- Görünür bir Chrome penceresi açılır ve açık kalır (küçültebilirsin, kapatma).
- Her tur: GitHub'dan onay listesi (overrides.yaml) alınır, Amazon taranır, sonuç GitHub'a gönderilir;
  site oradan yayınlanır.
- Amazon doğrulama isterse Mac bildirimi ve Telegram mesajı gelir, pencere öne çıkar.
- İlk açılışta GitHub ve Telegram bilgileri sorulur ve bu klasörde (.ayarlar.json) saklanır.
"""
import base64
import glob
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta
from getpass import getpass

import requests
import yaml

SETTINGS = ".ayarlar.json"
REPO = "unbug-bot/laptop-fiyat-takip"
API = "https://api.github.com"


# ---------------------------------------------------------------- ayarlar
def load_settings():
    if os.path.exists(SETTINGS):
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    print("\nİlk kurulum. Bu bilgiler yalnızca bu bilgisayarda, bu klasörde saklanır.\n")
    s = {
        "repo": input(f"GitHub repo [{REPO}]: ").strip() or REPO,
        "github_token": getpass("GitHub erişim anahtarı (yazarken görünmez): ").strip(),
        "telegram_bot_token": getpass("Telegram bot token (yazarken görünmez, boş geçilebilir): ").strip(),
        "telegram_chat_id": input("Telegram chat id (boş geçilebilir): ").strip(),
    }
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f)
    os.chmod(SETTINGS, 0o600)
    return s


def set_telegram_env(s):
    os.environ["TELEGRAM_BOT_TOKEN"] = s.get("telegram_bot_token", "")
    os.environ["TELEGRAM_CHAT_ID"] = s.get("telegram_chat_id", "")


# ---------------------------------------------------------------- GitHub
class GitHub:
    def __init__(self, repo, token):
        self.repo = repo
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}

    def get(self, path):
        r = requests.get(f"{API}/repos/{self.repo}/contents/{path}?ref=main", headers=self.h, timeout=30)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        d = r.json()
        content = d.get("content")
        if not content and d.get("download_url"):          # 1 MB üstü dosyalar
            content = base64.b64encode(requests.get(d["download_url"], timeout=60).content).decode()
        return base64.b64decode(content or ""), d["sha"]

    def list_dir(self, path):
        """[{name, path, sha, download_url}] ya da klasör yoksa []."""
        r = requests.get(f"{API}/repos/{self.repo}/contents/{path}?ref=main", headers=self.h, timeout=30)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) else []

    def delete(self, path, sha, message):
        r = requests.delete(f"{API}/repos/{self.repo}/contents/{path}", headers=self.h, timeout=30,
                            json={"message": message, "sha": sha, "branch": "main"})
        if r.status_code not in (200, 404):
            r.raise_for_status()

    def put(self, path, data, message):
        _, sha = self.get(path)
        body = {"message": message, "content": base64.b64encode(data).decode(), "branch": "main"}
        if sha:
            body["sha"] = sha
        r = requests.put(f"{API}/repos/{self.repo}/contents/{path}", headers=self.h, json=body, timeout=60)
        r.raise_for_status()

    def check(self):
        r = requests.get(f"{API}/repos/{self.repo}", headers=self.h, timeout=30)
        if r.status_code != 200:
            raise SystemExit(f"GitHub'a erişilemedi (HTTP {r.status_code}). Erişim anahtarını kontrol et; "
                             f"yeniden girmek için {SETTINGS} dosyasını sil.")
        if not r.json().get("permissions", {}).get("push"):
            raise SystemExit("Erişim anahtarının bu repoya yazma yetkisi yok (Contents: Read and write).")


def pull_files(gh):
    """Veritabanı bu bilgisayarda yoksa GitHub'dan indirilir. Onay listesi (overrides.yaml) her turda
    GitHub'dan alınır, böylece GitHub'dan düzenlenebilir. Ayarlar (config.yaml) yalnızca bu klasördedir."""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists("data/db.json"):
        data, _ = gh.get("data/db.json")
        with open("data/db.json", "wb") as f:
            f.write(data or b'{"meta": {}, "products": {}}')
        print("Veritabanı GitHub'dan indirildi.")
    data, _ = gh.get("overrides.yaml")
    if data:
        with open("overrides.yaml", "wb") as f:
            f.write(data)


def push_results(gh):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    for path in ("data/db.json", "data/site.json"):     # site.json en son: siteyi o tetikler
        with open(path, "rb") as f:
            gh.put(path, f.read(), f"Mac taraması {stamp}")
    print("Sonuçlar GitHub'a gönderildi; site birkaç dakika içinde güncellenir.")


# ---------------------------------------------------------------- GitHub fiyat kontrolüyle uyum
def blob_sha(data):
    """Git'in dosya kimliği: içerik aynıysa GitHub'daki sha ile aynıdır."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


CODE_FILES = ("requirements.txt", "config.yaml", "site/index.html")


def sync_code(gh):
    """GitHub'daki fiyat kontrolü Mac'teki kodla ve ayarlarla aynı çalışsın: farklı olan dosyalar gönderilir.
    (İş akışı dosyaları .github/ altındadır; onlar bu yolla gönderilemez.)"""
    remote = {e["path"]: e["sha"] for e in gh.list_dir("scraper")}
    for path in CODE_FILES:
        _, sha = gh.get(path)
        if sha:
            remote[path] = sha
    sent = []
    for path in sorted(glob.glob("scraper/*.py")) + list(CODE_FILES):
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        if remote.get(path) != blob_sha(data):
            gh.put(path, data, f"Mac'ten kod/ayar: {path}")
            sent.append(path)
    if sent:
        print(f"GitHub'a gönderilen kod/ayar dosyaları: {', '.join(sent)}")
    return sent


def merge_github(gh, cfg):
    """GitHub'ın fiyat gözlemlerini yerel veritabanına işler. Dönüş: silinmeye hazır [(path, sha)]."""
    from .gozlem import DURUM_PATH, GOZLEM_DIR, apply_files
    from .main import DB_PATH, load_db, save_json
    entries = [e for e in gh.list_dir(GOZLEM_DIR) if e["name"].endswith(".json")]
    durum, _ = gh.get(DURUM_PATH)
    if not entries and not durum:
        return []
    docs, done = [], []
    for e in entries:
        data, sha = gh.get(e["path"])
        try:
            docs.append(json.loads(data))
            done.append((e["path"], sha))
        except (TypeError, ValueError):
            print(f"Okunamayan gözlem dosyası atlandı: {e['path']}")
    db = load_db()
    n = apply_files(db, docs, cfg)
    if durum:
        try:
            db["meta"]["github"] = json.loads(durum)
        except ValueError:
            pass
    save_json(DB_PATH, db)
    if docs:
        print(f"GitHub'ın {len(docs)} gözlem dosyasından {n} fiyat veritabanına işlendi.")
    return done


def delete_merged(gh, done):
    for path, sha in done:
        gh.delete(path, sha, "İşlendi (Mac)")


def telegram_human_alert(s):
    def send():
        tok, chat = s.get("telegram_bot_token"), s.get("telegram_chat_id")
        if tok and chat:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=20, json={
                "chat_id": chat, "text": "⚠️ Amazon doğrulama istiyor. Mac'teki Chrome penceresinde çöz."})
    return send


def open_browser(cfg, s):
    from .browser import BrowserFetcher
    return BrowserFetcher(tuple(cfg["request_delay_sec"]), human_wait_min=int(cfg.get("human_wait_min", 15)),
                          on_human_needed=telegram_human_alert(s))


def setup():
    """Ortak başlangıç: klasöre geç, ayarları oku, GitHub'ı kontrol et."""
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    s = load_settings()
    set_telegram_env(s)
    gh = GitHub(s["repo"], s["github_token"])
    gh.check()
    return s, gh


def load_cfg():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- döngü
def main():
    s, gh = setup()
    from . import main as scan

    cfg = load_cfg()
    f = open_browser(cfg, s)
    print("\nChrome penceresini küçültebilirsin; kapatma. Durdurmak için bu pencerede Ctrl+C.\n")
    try:
        while True:
            start = datetime.now()
            cfg = load_cfg()
            print(f"===== Tur başladı: {start:%H:%M} =====", flush=True)
            try:
                f.reopen_if_closed()
                pull_files(gh)
                merged = merge_github(gh, cfg)
                scan.run(fetcher=f)
                push_results(gh)
                delete_merged(gh, merged)
                sync_code(gh)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"Tur hata ile bitti: {e}")
            nxt = start + timedelta(minutes=int(cfg.get("mac_interval_min", 60)))
            print(f"Sonraki tur: {nxt:%H:%M}\n", flush=True)
            while datetime.now() < nxt:
                time.sleep(20)
    except KeyboardInterrupt:
        print("\nDurduruldu.")
    finally:
        f.close()


if __name__ == "__main__":
    sys.exit(main())
