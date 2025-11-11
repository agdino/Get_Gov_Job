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
from bs4 import BeautifulSoup


# ===== Telegram 設定 =====
BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
CHAT_ID = os.getenv("TG_CHAT_ID")

def send_telegram_message(text: str):
    """發送文字訊息到 Telegram"""
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ 缺少 TG_BOT_TOKEN 或 TG_CHAT_ID，請確認環境變數或 GitHub Secrets。")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"  # 支援粗體與換行
    }
    try:
        requests.post(url, data=payload)
        print("✅ 已發送 Telegram 通知。")
    except Exception as e:
        print("❌ 發送失敗：", e)


# ===== 抓取職缺資料 =====
def fetch_job_html(keyword="統計"):
    """使用 Selenium 取得職缺 HTML"""
    url = "https://web3.dgpa.gov.tw/want03front/AP/WANTF00001.ASPX"

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 20)
    driver.get(url)

    try:
        print("頁面載入中...")
        time.sleep(2)

        input_box = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, 'div#ctl00_ContentPlaceHolder1_trPerson4 input')))
        input_box.clear()
        input_box.send_keys(keyword)
        time.sleep(1)
        input_box.send_keys(Keys.ARROW_DOWN)
        input_box.send_keys(Keys.ENTER)
        print(f"已選取『{keyword}』")

        driver.find_element(By.ID, "ctl00_ContentPlaceHolder1_btnQUERY").click()
        print("查詢中...")
        time.sleep(3)

        table_html = driver.execute_script("""
            let tables = document.querySelectorAll('table');
            for (let t of tables) {
                if (t.innerText.includes('職稱') || t.innerText.includes('機關名稱') || t.innerText.includes('統計')) {
                    return t.outerHTML;
                }
            }
            return '';
        """)
        if not table_html:
            raise Exception("沒有找到職缺表格")

        print("✅ 已取得表格 HTML。")
        return table_html

    finally:
        driver.quit()


# ===== 解析與切割 =====
TITLE_KEYWORDS = ["書記官", "科員", "助理員", "專員", "技士", "分析師", "辦事員", "技佐", "主任", "幹事"]

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

    # 若第一列是表頭則略過
    if rows and ("職稱" in rows[0].get_text() or "機關名稱" in rows[0].get_text()):
        print("⚙️ 偵測到表頭，略過第一列。")
        rows = rows[1:]

    data, unparsed = [], []

    for idx, row in enumerate(rows):
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
                "備註": m.group("備註").strip(),
            })
        else:
            # fallback 容錯
            if "統計" in line:
                loc_match = re.search(r"\d{1,3}-[\u4e00-\u9fa5A-Za-z0-9]+", line)
                date_match = re.search(r"\d{3}/\d{2}/\d{2}\s*~\s*\d{3}/\d{2}/\d{2}", line)
                title, org = split_title_and_org(line.split("[統計]")[0] + "統計")

                data.append({
                    "職稱": title,
                    "機關名稱": org,
                    "職系": "統計",
                    "職務列等": "未明確解析",
                    "工作地點": loc_match.group(0) if loc_match else "",
                    "有效期間": date_match.group(0) if date_match else "",
                    "備註": "",
                })
            else:
                unparsed.append(line)

    print(f"✅ 成功解析 {len(data)} 筆，未解析 {len(unparsed)} 筆。")
    if unparsed:
        print("⚠️ 未解析行：", json.dumps(unparsed, ensure_ascii=False, indent=2))
    return data


# ===== 主流程 =====
def main():
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

    text_message = "\n".join(msg_lines)
    send_telegram_message(text_message)


if __name__ == "__main__":
    main()
