# -*- coding: utf-8 -*-
import re
import os
import time
import json
import requests
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

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
        requests.post(url, data=payload, timeout=20)
        print("✅ 已發送 Telegram 通知。", flush=True)
    except Exception as e:
        print("❌ 發送失敗：", e, flush=True)

# ===== Selenium 組態 =====
def build_driver():
    """建立在 CI/Actions 上較穩定的 Chrome Driver"""
    opts = Options()
    # 新版 headless 比舊版穩
    opts.add_argument("--headless=new")
    # GitHub Actions 必備兩件套
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    # 降低 compositor 不穩定因素
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,2000")
    # 在 renderer 卡住時較容易把錯丟回
    opts.add_argument("--remote-debugging-pipe")
    # 加速：不等所有資源（圖片/CSS）載完
    opts.page_load_strategy = "eager"
    # 可選：關閉圖片，若頁面不依賴圖片排版可打開
    # opts.add_argument("--blink-settings=imagesEnabled=false")

    # 交給 Selenium Manager 自動找驅動，不要手動指定 binary/driver 路徑
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(45)
    return driver

def robust_get(driver, url, retries=2):
    """載入頁面時自動重試，避免單次抖動造成整段失敗"""
    for i in range(retries + 1):
        try:
            print(f"➡️ 造訪 {url}（嘗試 {i+1}/{retries+1}）", flush=True)
            driver.get(url)
            # 在 page_load_strategy=eager 下，等到 DOM ready 即可
            WebDriverWait(driver, 30).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
            return
        except (TimeoutException, WebDriverException) as e:
            print(f"⚠️ 載入失敗：{e.__class__.__name__}，3 秒後重試", flush=True)
            if i == retries:
                raise
            time.sleep(3)

# ===== 抓取職缺資料 =====
def fetch_job_html(keyword="統計"):
    """使用 Selenium 取得職缺 HTML"""
    url = "https://web3.dgpa.gov.tw/want03front/AP/WANTF00001.ASPX"
    driver = build_driver()
    wait = WebDriverWait(driver, 40)

    try:
        print("頁面載入中...", flush=True)
        robust_get(driver, url)

        # 等主要搜尋區塊存在
        # 該頁為 ASP.NET，常見會在互動時重繪 DOM，因此盡量使用顯式等待
        wait.until(EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder1_trPerson4")))

        # 取得輸入框（該區塊下第一個 input）
        input_box = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#ctl00_ContentPlaceHolder1_trPerson4 input"))
        )
        input_box.clear()
        input_box.send_keys(keyword)

        # 等待自動完成清單出現再做鍵盤選擇（若沒有自動完成，這段會直接略過不報錯）
        try:
            wait.until(EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, ".ui-autocomplete li, .ui-menu-item")
            ))
            input_box.send_keys(Keys.ARROW_DOWN)
            input_box.send_keys(Keys.ENTER)
        except TimeoutException:
            # 沒有自動完成清單就直接用原輸入值
            pass

        print(f"已選取『{keyword}』", flush=True)

        # 查詢按鈕（PostBack）
        query_btn = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_ContentPlaceHolder1_btnQUERY")))
        query_btn.click()
        print("查詢中...", flush=True)

        # 等任一資料列出現
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tr")))

        # 以 JS 遍歷表格，挑包含關鍵欄位者
        table_html = driver.execute_script("""
            const tables = [...document.querySelectorAll('table')];
            for (const t of tables) {
                const text = (t.innerText || '').trim();
                if (text.includes('職稱') || text.includes('機關名稱') || text.includes('統計')) {
                    return t.outerHTML;
                }
            }
            return '';
        """)

        # 兜底：真的抓不到就回傳整個 body，後續再判斷
        if not table_html:
            table_html = driver.execute_script("return document.body ? document.body.outerHTML : '';") or ""

        if not table_html:
            raise Exception("沒有找到職缺表格")

        print("✅ 已取得表格 HTML。", flush=True)
        return table_html

    except Exception as e:
        print("❌ 抓取錯誤：", e, flush=True)
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
    # 兜底策略：粗略切三字
    return text[:3], text[3:].strip()

def parse_jobs(html: str):
    """解析 HTML 表格成結構化資料"""
    soup = BeautifulSoup(html, "html.parser")

    # 優先找包含關鍵字的表；找不到就取文字最多的表兜底
    target_table = None
    for t in soup.find_all("table"):
        txt = t.get_text(" ", strip=True)
        if any(k in txt for k in ("職稱", "機關名稱", "統計")):
            target_table = t
            break
    if target_table is None:
        tables = soup.find_all("table")
        if tables:
            target_table = max(tables, key=lambda x: len(x.get_text()))

    if target_table is None:
        print("⚠️ 沒有偵測到表格，回傳 0 筆", flush=True)
        return []

    rows = target_table.find_all("tr")
    print(f"🔍 共找到 {len(rows)} 列 (含表頭)", flush=True)

    if rows and ("職稱" in rows[0].get_text() or "機關名稱" in rows[0].get_text()):
        rows = rows[1:]

    data = []
    for row in rows:
        tds = row.find_all("td")
        if not tds:
            continue
        line = "".join(td.get_text(strip=True) for td in tds)
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
    print(f"✅ 成功解析 {len(data)} 筆。", flush=True)
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
        print(err_msg, flush=True)
        send_telegram_message(err_msg)

if __name__ == "__main__":
    main()
