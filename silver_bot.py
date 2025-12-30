import os
import time
import requests
from datetime import datetime
from typing import Optional

PHUQUY_SILVER_URL = "https://giabac.phuquygroup.vn/"
REQUEST_TIMEOUT = 20

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def send_telegram_photo(
    bot_token: str,
    chat_id: str,
    photo_path: str,
    caption: str = "",
    retries: int = 3,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            log(f"Gửi Telegram photo (attempt {attempt}/{retries})...")
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "disable_web_page_preview": True,
                }
                r = requests.post(
                    url,
                    data=data,
                    files=files,
                    timeout=REQUEST_TIMEOUT,
                    proxies={"http": None, "https": None},
                )
            log(f"Telegram response: {r.status_code} — {r.text}")
            r.raise_for_status()
            return
        except Exception as e:
            last_err = e
            log(f"❌ Lỗi gửi Telegram photo: {e}")
            if attempt < retries:
                time.sleep(3)

    raise RuntimeError("Gửi Telegram photo thất bại") from last_err


def capture_silver_table_screenshot(out_path: str = "silver_table.png") -> str:
    """
    Render trang bằng Playwright và chụp riêng element #priceListContainer.
    """
    from playwright.sync_api import sync_playwright

    log(f"Render & capture: {PHUQUY_SILVER_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1100, "height": 900},
            device_scale_factor=2,  # nét hơn
        )

        page.goto(PHUQUY_SILVER_URL, wait_until="domcontentloaded", timeout=60000)

        # Chờ bảng xuất hiện (nếu trang load chậm)
        page.wait_for_selector("#priceListContainer", timeout=60000)

        container = page.locator("#priceListContainer")

        # Đảm bảo load xong layout
        page.wait_for_timeout(800)

        # Chụp riêng vùng bảng (đẹp, không thừa)
        container.screenshot(path=out_path)

        browser.close()

    log(f"✅ Đã tạo screenshot: {out_path}")
    return out_path


def main() -> None:
    bot_token = (os.getenv("SILVER_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("SILVER_TELEGRAM_CHAT_ID") or "").strip()

    if not bot_token or not chat_id:
        log("⚠️ Thiếu SILVER_TELEGRAM_BOT_TOKEN hoặc SILVER_TELEGRAM_CHAT_ID")
        return

    img_path = capture_silver_table_screenshot("silver_table.png")

    caption = f"🥈 Giá bạc Phú Quý\n⏱ {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    send_telegram_photo(bot_token, chat_id, img_path, caption=caption)


if __name__ == "__main__":
    main()
