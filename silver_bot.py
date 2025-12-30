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


# -----------------------------
# Cấu hình / hằng số
# -----------------------------
PHUQUY_SILVER_URL = "https://giabac.phuquygroup.vn/"
REQUEST_TIMEOUT = 20  # giây

TELEGRAM_RETRIES = 3
TELEGRAM_RETRY_DELAY = 3  # giây

GIST_FILE_NAME = "silver_price_snapshot.txt"  # lưu text trên Gist
LAST_DATA_FILE = "last_silver_price.txt"      # fallback local nếu không có Gist


# -----------------------------
# Model dữ liệu
# -----------------------------
@dataclass
class SilverItem:
    name: str
    unit: str
    buy: Optional[int]
    sell: Optional[int]


# -----------------------------
# Utils
# -----------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def normalize_name(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def parse_vnd_commas(value: str) -> Optional[int]:
    """
    Dành cho format kiểu '2,776,000' hoặc '74,026,482'
    Nếu trống / '-' / '—' => None
    """
    value = (value or "").strip()
    if value in ("", "-", "—"):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def format_vnd(value: Optional[int]) -> str:
    """In ra kiểu '2.776.000' (dùng dấu chấm cho đẹp)."""
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", ".")


def canonical_snapshot(items: List[SilverItem]) -> str:
    """
    Snapshot ổn định để lưu lên Gist:
    - normalize name + unit
    - None -> '' cho buy/sell
    - sort theo (name, unit) để chống reorder HTML
    """
    rows = []
    for it in items:
        name = normalize_name(it.name)
        unit = normalize_name(it.unit)
        buy = "" if it.buy is None else str(int(it.buy))
        sell = "" if it.sell is None else str(int(it.sell))
        rows.append((name, unit, buy, sell))

    rows.sort(key=lambda x: (x[0], x[1]))
    return "\n".join([f"{n} | {u} | {b} | {s}" for n, u, b, s in rows]).strip()


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def canonicalize_text_blob(s: str) -> str:
    """
    Chuẩn hoá text snapshot cũ lấy từ Gist/file để hash ổn định:
    - NBSP -> space
    - CRLF -> LF
    - strip
    """
    return (s or "").replace("\u00a0", " ").replace("\r\n", "\n").strip()


# -----------------------------
# Crawler bạc Phú Quý
# -----------------------------
def fetch_silver_page(url: str = PHUQUY_SILVER_URL) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    log(f"Đang tải trang giá bạc: {url}")
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_silver_table(page_html: str) -> List[SilverItem]:
    soup = BeautifulSoup(page_html, "html.parser")

    container = soup.select_one("#priceListContainer")
    if not container:
        raise RuntimeError("Không tìm thấy #priceListContainer trong HTML.")

    table = container.find("table")
    if not table:
        raise RuntimeError("Không tìm thấy table trong #priceListContainer.")

    items: List[SilverItem] = []

    for tr in table.select("tbody tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        # Bỏ dòng tiêu đề nhóm: <td colspan="4">BẠC THƯƠNG HIỆU ...</td>
        if len(tds) == 1 and (tds[0].get("colspan") in ("4", 4)):
            continue

        # Kỳ vọng đúng 4 cột: sản phẩm, đơn vị, mua, bán
        if len(tds) < 4:
            continue

        name = normalize_name(tds[0].get_text(" ", strip=True))
        unit = normalize_name(tds[1].get_text(" ", strip=True))
        buy_raw = tds[2].get_text(" ", strip=True)
        sell_raw = tds[3].get_text(" ", strip=True)

        buy = parse_vnd_commas(buy_raw)
        sell = parse_vnd_commas(sell_raw)

        # Nếu không có tên hoặc cả buy/sell đều None -> bỏ
        if not name or (buy is None and sell is None):
            continue

        items.append(SilverItem(name=name, unit=unit, buy=buy, sell=sell))

    return items


def get_silver_price() -> List[SilverItem]:
    page_html = fetch_silver_page()
    items = parse_silver_table(page_html)
    if not items:
        raise RuntimeError("Không parse được bất kỳ dòng giá bạc nào.")
    return items


# -----------------------------
# Lưu / tải snapshot (file fallback)
# -----------------------------
def load_last_data_from_file(path: str = LAST_DATA_FILE) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read() or ""
    except FileNotFoundError:
        return ""


def save_last_data_to_file(text: str, path: str = LAST_DATA_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# -----------------------------
# Lưu / tải snapshot bằng Gist
# -----------------------------
def _get_gist_token() -> Optional[str]:
    # hỗ trợ cả 2 tên secret bạn có thể đang dùng
    return (os.getenv("GIST_TOKEN") or os.getenv("TOKEN_GIST") or "").strip() or None


def load_last_data_from_gist(token: str, gist_id: str) -> str:
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        log(f"Đọc snapshot từ Gist: {gist_id}")
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            log("⚠️ Không tìm thấy Gist, xem như snapshot rỗng.")
            return ""
        resp.raise_for_status()
        data = resp.json()
        file_obj = data.get("files", {}).get(GIST_FILE_NAME)
        if not file_obj:
            log(f"⚠️ Không thấy file {GIST_FILE_NAME} trong Gist, xem như rỗng.")
            return ""
        return file_obj.get("content") or ""
    except Exception as e:
        log(f"⚠️ Lỗi khi đọc Gist: {e}, fallback snapshot rỗng.")
        return ""


def save_last_data_to_gist(token: str, gist_id: str, text: str) -> None:
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    payload = {"files": {GIST_FILE_NAME: {"content": text}}}
    log(f"Cập nhật snapshot lên Gist: {gist_id}")
    resp = requests.patch(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    log("✅ Đã lưu snapshot lên Gist.")


def load_last_snapshot() -> str:
    gist_token = _get_gist_token()
    gist_id = (os.getenv("GIST_ID") or "").strip()

    if gist_token and gist_id:
        return load_last_data_from_gist(gist_token, gist_id)

    log("ℹ️ Không có GIST token hoặc GIST_ID, dùng snapshot local (file).")
    return load_last_data_from_file()


def save_last_snapshot(text: str) -> None:
    gist_token = _get_gist_token()
    gist_id = (os.getenv("GIST_ID") or "").strip()

    if gist_token and gist_id:
        try:
            save_last_data_to_gist(gist_token, gist_id, text)
            return
        except Exception as e:
            log(f"⚠️ Lỗi lưu Gist, fallback sang file local: {e}")

    save_last_data_to_file(text)


# -----------------------------
# Telegram
# -----------------------------
def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    retries: int = TELEGRAM_RETRIES,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            log(f"Gửi Telegram (attempt {attempt}/{retries})...")
            r = requests.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                proxies={"http": None, "https": None},
            )
            log(f"Telegram response: {r.status_code} — {r.text}")
            r.raise_for_status()
            return
        except Exception as e:
            last_error = e
            log(f"❌ Lỗi gửi Telegram: {e}")
            if attempt < retries:
                log(f"👉 Thử lại sau {TELEGRAM_RETRY_DELAY}s...")
                time.sleep(TELEGRAM_RETRY_DELAY)

    raise RuntimeError(f"Gửi Telegram thất bại sau {retries} lần") from last_error


# -----------------------------
# Build message hiển thị
# -----------------------------
def build_message(items: List[SilverItem]) -> str:
    header = (
        "🥈 <b>Cập nhật giá bạc Phú Quý</b>\n"
        f"⏱ {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
    )

    rows: List[tuple[str, str, str, str]] = []
    rows.append(("SẢN PHẨM", "ĐƠN VỊ", "MUA VÀO", "BÁN RA"))

    for it in items:
        name = normalize_name(it.name)
        unit = normalize_name(it.unit)
        buy_s = format_vnd(it.buy)
        sell_s = format_vnd(it.sell)
        rows.append((name, unit, buy_s, sell_s))

    c1 = max(len(r[0]) for r in rows)
    c2 = max(len(r[1]) for r in rows)
    c3 = max(len(r[2]) for r in rows)
    c4 = max(len(r[3]) for r in rows)

    lines: List[str] = []
    for a, b, c, d in rows:
        lines.append(
            a.ljust(c1) + "  "
            + b.ljust(c2) + "  "
            + c.rjust(c3) + "  "
            + d.rjust(c4)
        )

    table_text_escaped = html.escape("\n".join(lines))

    return (
        header
        + "<pre><code>"
        + table_text_escaped
        + "</code></pre>"
        + "\nNguồn: giabac.phuquygroup.vn"
    )


# -----------------------------
# Main
# -----------------------------
def main() -> None:
    print("🔁 Cron job chạy lúc", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    bot_token = (os.getenv("SILVER_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("SILVER_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not bot_token or not chat_id:
        log("⚠️ Thiếu SILVER_TELEGRAM_BOT_TOKEN hoặc SILVER_TELEGRAM_CHAT_ID. Thoát.")
        return

    try:
        items = get_silver_price()
    except Exception as e:
        log(f"❌ Lỗi lấy giá bạc: {e}")
        return

    snapshot_text = canonical_snapshot(items)
    snapshot_hash = sha256_text(snapshot_text)

    last_text = load_last_snapshot()
    last_hash = sha256_text(canonicalize_text_blob(last_text))

    if snapshot_hash != last_hash:
        log(f"🔔 Phát hiện thay đổi (hash): {last_hash[:8]} -> {snapshot_hash[:8]}")
        msg = build_message(items)
        try:
            send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML")
            save_last_snapshot(snapshot_text)
            log("✅ Đã gửi Telegram bạc (có thay đổi).")
        except Exception as e:
            log(f"❌ Gửi Telegram thất bại: {e}")
    else:
        log("⏳ Không có thay đổi, không gửi Telegram.")


if __name__ == "__main__":

    main()
