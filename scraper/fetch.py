"""Ortak sabitler: Amazon adresi, engel istisnası, hata ayıklama kaydı."""
import os

BASE = "https://www.amazon.com.tr"
DEBUG_DIR = "debug"


class Blocked(Exception):
    """Amazon sayfayı vermedi ve doğrulama çözülmedi."""


def save_debug(name, html, directory=DEBUG_DIR):
    if not html:
        return
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
        f.write(html)
