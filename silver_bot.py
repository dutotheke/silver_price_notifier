from __future__ import annotations

import os
import re
import time
import html
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

PHUQUY_SILVER_URL = "https://giabac.phuquygroup.vn/"
REQUEST_TIMEOUT = 20

TELEGRAM_RETRIES = 3
TELEGRAM_RETRY_DELAY = 3

GIST_FILE_NAME = "silver_price_snapshot.txt"
SNAPSHOT_PATH = "silver_snapshot.txt"
SCREENSHOT_PATH = "silver_table.png"


@dataclass
class SilverItem:
    name: str
    unit: str
    buy: Optional[int]
    sell: Optional[int]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def parse_vnd_commas(value: str) -> Optional[int]:
    value = (value or "").strip()
    if value in ("", "-", "—"):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def format_vnd(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", ".")


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def canonical_snapshot(items: List[SilverItem]) -> str:
    rows = []
    for it in items:
        name = normalize_text(it.name)
        unit = normalize_text(it.unit)
        buy = "" if it.buy is None else str(int(it.buy))
        sell = "" if it.sell is None else str(int(it.sell))
        rows.append((name, unit, buy, sell))
    rows.sort(key=lambda x: (x[0], x[1]))
    return "\n".join([f"{n} | {u} | {b} | {s}" for n, u, b, s in rows]).strip()


def fetch_silver_page() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    resp = requests.get(PHUQUY_SILVER_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_silver_table(page_html: str) -> List[SilverItem]:
    soup = BeautifulSoup(page_html, "html.parser")
    container = soup.select_one("#priceListContainer")
    if not container:
        raise RuntimeError("Không tìm thấy #priceListContainer")

    table = container.find("table")
    if not table:
        raise RuntimeError("Không tìm thấy table trong #priceListContainer")

    items: List[SilverItem] = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        # bỏ dòng tiêu đề nhóm colspan=4
        if len(tds) == 1 and (tds[0].get("colspan") in ("4", 4)):
            continue

        if len(tds) < 4:
            continue

        name = normalize_text(tds[0].get_text(" ", strip=True))
        unit = normalize_text(tds[1].get_text(" ", strip=True))
        buy = parse_vnd_commas(tds[2].get_text(" ", strip=True))
        sell = parse_vnd_commas(tds[3].get_text(" ", strip=True))

        if not name or (buy is None and sell is None):
            continue

        items.append(SilverItem(name=name, unit=unit, buy=buy, sell=sell))

    if not items:
        raise RuntimeError("Parse được 0 dòng giá bạc")
    return items


def get_gist_token() -> Optional[str]:
    return (os.getenv("GIST_TOKEN") or os.getenv("TOKEN_GIST") or "").strip() or None


def load_snapshot_from_gist(token: str, gist_id: str) -> str:
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    data = resp.json()
    file_obj = data.get("files", {}).get(GIST_FILE_NAME)
    return (file_obj or {}).get("content") or ""


def save_snapshot_to_gist(token: str, gist_id: str, text: str) -> None:
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    payload = {"files": {GIST_FILE_NAME: {"content": text}}}
    resp = requests.patch(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()


def write_output(name: str, value: str) -> None:
    """
    GitHub Actions step output: dùng file $GITHUB_OUTPUT
    """
    out = os.getenv("GITHUB_OUTPUT")
    if not out:
        log(f"(local) output {name}={value}")
        return
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def save_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content or "")


def load_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read() or ""
    except FileNotFoundError:
        return ""


def build_caption() -> str:
    return f"🥈 Giá bạc Phú Quý\n⏱ {datetime.now().strftime('%H:%M %d/%m/%Y')}"


def send_telegram_photo(bot_token: str, chat_id: str, photo_path: str, caption: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    last_err: Optional[Exception] = None

    for attempt in range(1, TELEGRAM_RETRIES + 1):
        try:
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": chat_id, "caption": caption}
                r = requests.post(url, data=data, files=files, timeout=REQUEST_TIMEOUT, proxies={"http": None, "https": None})
            r.raise_for_status()
            return
        except Exception as e:
            last_err = e
            log(f"❌ sendPhoto error attempt {attempt}: {e}")
            if attempt < TELEGRAM_RETRIES:
                time.sleep(TELEGRAM_RETRY_DELAY)

    raise RuntimeError("Gửi Telegram ảnh thất bại") from last_err


def capture_table_screenshot(out_path: str = SCREENSHOT_PATH) -> str:
    """
    Chỉ được gọi khi CHẮC CHẮN có thay đổi.
    Lazy import Playwright để không phụ thuộc khi chạy compare.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
        page.goto(PHUQUY_SILVER_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#priceListContainer", timeout=60000)
        page.wait_for_timeout(800)
        page.locator("#priceListContainer").screenshot(path=out_path)
        browser.close()
    return out_path


def cmd_compare() -> None:
    """
    1) Crawl + parse table -> snapshot_text
    2) Load last snapshot from Gist
    3) Compare hash
    4) Write snapshot_text to SNAPSHOT_PATH
    5) Set output changed=true/false
    """
    gist_token = get_gist_token()
    gist_id = (os.getenv("GIST_ID") or "").strip()

    if not gist_token or not gist_id:
        raise RuntimeError("Thiếu GIST token (GIST_TOKEN/TOKEN_GIST) hoặc GIST_ID")

    items = parse_silver_table(fetch_silver_page())
    snapshot_text = canonical_snapshot(items)
    save_file(SNAPSHOT_PATH, snapshot_text)

    last_text = load_snapshot_from_gist(gist_token, gist_id)
    new_hash = sha256_text(snapshot_text)
    old_hash = sha256_text((last_text or "").replace("\u00a0", " ").replace("\r\n", "\n").strip())

    changed = "true" if new_hash != old_hash else "false"
    log(f"Compare hash: {old_hash[:8]} -> {new_hash[:8]} changed={changed}")
    write_output("changed", changed)


def cmd_notify() -> None:
    """
    1) Read snapshot_text from SNAPSHOT_PATH
    2) Render + screenshot
    3) sendPhoto
    4) update Gist snapshot (ONLY after send success)
    """
    bot_token = (os.getenv("SILVER_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("SILVER_TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Thiếu SILVER_TELEGRAM_BOT_TOKEN hoặc SILVER_TELEGRAM_CHAT_ID")

    gist_token = get_gist_token()
    gist_id = (os.getenv("GIST_ID") or "").strip()
    if not gist_token or not gist_id:
        raise RuntimeError("Thiếu GIST token hoặc GIST_ID")

    snapshot_text = load_file(SNAPSHOT_PATH).strip()
    if not snapshot_text:
        raise RuntimeError(f"Không có snapshot text ở {SNAPSHOT_PATH}")

    img = capture_table_screenshot(SCREENSHOT_PATH)
    send_telegram_photo(bot_token, chat_id, img, caption=build_caption())

    save_snapshot_to_gist(gist_token, gist_id, snapshot_text)
    log("✅ Notify done: sent screenshot + updated Gist snapshot")


def main() -> None:
    import sys
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if mode == "compare":
        cmd_compare()
    elif mode == "notify":
        cmd_notify()
    else:
        raise SystemExit("Usage: python silver_bot.py compare|notify")


if __name__ == "__main__":
    main()

