# -*- coding: utf-8 -*-
import re
import os
import time
import json
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup

# ===== Telegram 環境變數 =====
BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")

def send_telegram_message(text: str):
    """發送文字訊息到 Telegram"""
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ 缺少 TG_BOT_TOKEN 或 TG_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload)
        print("✅ 已發送 Telegram 通知。")
    except Exception as e:
        print("❌ 發送失敗：", e)


# ===== 抓取職缺資料 =====
def fetch_job_html(keyword="統計"):
    """使用 Selenium 取得職缺 HTML (優化 GitHub Actions 穩定性)"""
    url = "https://web3.dgpa.gov.tw/want03front/AP/WANTF00001.ASPX"

    options = webdriver.ChromeOptions()
    
    # 1. (關鍵) 將頁面載入策略設為 'eager'
    # 讓 driver.get() 在 DOM 準備就緒後就返回，不等待所有資源載入
    # 接著由 WebDriverWait 來等待我們需要的特定元素
    options.page_load_strategy = 'eager'

    # 2. (關鍵) 移除硬編碼路徑
    # 依賴 GitHub Actions YML 中 (例如 browser-actions/setup-chrome@v1)
    # 自動安裝並加入到 PATH 的 chromedriver 和 chromium-browser
    # options.binary_location = "/usr/bin/chromium-browser" # 移除
    
    # --- 保留所有 GitHub Actions 需要的參數 ---
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # 3. (關鍵) 讓 Selenium 自動尋找驅動程式
    # service = Service("/usr/bin/chromedriver") # 移除
    # driver = webdriver.Chrome(service=service, options=options) # 舊版
    driver = webdriver.Chrome(options=options) # 新版 (Selenium 4+)

    # 4. (優化) 延長等待時間
    # 將 WebDriverWait 延長到 60 秒，與 page_load_timeout 一致
    # 讓元素有更充裕的時間在資源受限的環境中被載入
    wait = WebDriverWait(driver, 60) 
    driver.set_page_load_timeout(60)

    try:
        print("頁面載入中 (Eager 策略)...")
        driver.get(url)
        # time.sleep(2) # 移除：使用 eager 策略後，應完全依賴 WebDriverWait

        # 等待輸入框出現
        input_box = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'div#ctl00_ContentPlaceHolder1_trPerson4 input')
            )
        )
        print("輸入框已載入。")
        
        input_box.clear()
        input_box.send_keys(keyword)
        time.sleep(1) # 這裡的短暫停頓有助於模擬輸入
        input_box.send_keys(Keys.ARROW_DOWN)
        input_box.send_keys(Keys.ENTER)
        print(f"已選取『{keyword}』")

        # 等待查詢按鈕可被點擊
        search_button = wait.until(
            EC.element_to_be_clickable(
                (By.ID, "ctl00_ContentPlaceHolder1_btnQUERY")
            )
        )
        search_button.click()
        print("查詢中...")
        
        # 5. (優化) 點擊後等待表格出現
        # 不使用 time.sleep(3)，而是明確等待表格標記出現
        wait.until(
            EC.presence_of_element_located((By.XPATH, "//table[contains(., '職稱') or contains(., '機關名稱')]"))
        )
        print("查詢結果表格已載入。")
        
        # 執行 JS 抓取表格
        table_html = driver.execute_script("""
            let tables = document.querySelectorAll('table');
            for (let t of tables) {
                if (t.innerText.includes('職稱') || 
                    t.innerText.includes('機關名稱') || 
                    t.innerText.includes('統計')) {
                    return t.outerHTML;
                }
            }
            return '';
        """)

        if not table_html:
            raise Exception("沒有找到職缺表格")

        print("✅ 已取得表格 HTML。")
        return table_html

    except Exception as e:
        print("❌ 抓取錯誤：", e)
        # (可選) 增加除錯資訊
        # driver.save_screenshot("debug_screenshot.png")
        # print(driver.page_source)
        raise
    finally:
        driver.quit()


# ===== 解析與切割 =====
TITLE_KEYWORDS = [
    "書記官", "科員", "助理員", "專員", "技士", "分析師", "辦事員", "技佐", "主任", "幹事"
]

pattern = re.compile(
    r"""
    ^\s*
    (?P<序號>\d+)
    (?P<前半>.+?)
    \[?(?P<職系>[\u4e00-\u9fa5A-Za-z0-9]+)\]?[,]?
    (?P<工作地點>\d{1,3}-[\u4e00-\u9fa5A-Za-z0-9]+)
    (?P<職務列等>(委任|薦任|簡任).*?職等(?:或.*?職等)*)
    有效期間[:：]?\s*(?P<有效期間>\d{3}/\d{2}/\d{2}\s*~\s*\d{3}/\d{2}/\d{2})
    (?P<備註>.*)$
    """, re.X
)

def split_title_and_org(text: str):
    for kw in TITLE_KEYWORDS:
        if kw in text:
            pos = text.find(kw) + len(kw)
            return text[:pos], text[pos:].strip()
    return text[:3], text[3:].strip()

def parse_jobs(html: str):
    """解析 HTML 表格成結構化資料"""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr")
    print(f"🔍 共找到 {len(rows)} 列 (含表頭)")

    if rows and ("職稱" in rows[0].get_text() or "機關名稱" in rows[0].get_text()):
        rows = rows[1:]

    data = []
    for row in rows:
        line = "".join(td.get_text(strip=True) for td in row.find_all("td"))
        if not line or "共" in line:
            continue

        m = pattern.search(line)
        if m:
            title, org = split_title_and_org(m.group("前半"))
            data.append({
                "職稱": title,
                "機關名稱": org,
                "職系": m.group("職系"),
                "職務列等": m.group("職務列等"),
                "工作地點": m.group("工作地點"),
                "有效期間": m.group("有效期間"),
            })
    print(f"✅ 成功解析 {len(data)} 筆。")
    return data


# ===== 主流程 =====
def main():
    try:
        html = fetch_job_html("統計")
        jobs = parse_jobs(html)

        if not jobs:
            send_telegram_message("⚠️ 今天沒有抓到任何職缺。")
            return

        preview = jobs[:5]
        msg_lines = ["📊 <b>今日統計職缺更新：</b>"]
        for i, j in enumerate(preview, 1):
            msg_lines.append(
                f"\n<b>{i}. {j['職稱']}</b>（{j['職系']}）\n"
                f"📍 {j['機關名稱']}｜{j['工作地點']}\n"
                f"💼 {j['職務列等']}\n"
                f"⏰ {j['有效期間']}"
            )

        send_telegram_message("\n".join(msg_lines))

    except Exception as e:
        err_msg = f"❌ 任務執行失敗：{str(e)}"
        print(err_msg)
        send_telegram_message(err_msg)


if __name__ == "__main__":
    main()

