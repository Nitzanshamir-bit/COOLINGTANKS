import os
import re
import json
import sys
from datetime import datetime
import requests
from playwright.sync_api import sync_playwright

LOGIN_URL = "http://icontrol.paccool.be/en/auth/login"
BASE_URL = "http://icontrol.paccool.be"

def extract_latest(page_text: str):
    """
    Extract temperature and timestamp from the tank detail page text.
    We parse by regex so it survives minor UI changes.
    """
    # Timestamp like: 2026-01-01 12:22:09
    ts_match = re.search(r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b", page_text)
    timestamp = ts_match.group(1) if ts_match else None

    # Temperature like: 7.00°C (accept 7.0°C, 7°C)
    temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°\s*C", page_text)
    temperature_c = float(temp_match.group(1)) if temp_match else None

    # Status message: often "Everything ok" in Success card
    # We'll try to find that phrase if exists, else None.
    status = None
    ok_match = re.search(r"\bEverything\s+ok\b", page_text, re.IGNORECASE)
    if ok_match:
        status = "Everything ok"

    return temperature_c, timestamp, status

def login(page, user: str, password: str):
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(500)

    page.fill('input[name="email"]', user)
    page.fill('input[name="password"]', password)
    page.click('button:has-text("Login")')

    # Wait until we are redirected somewhere authenticated
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)

def fetch_one_tank(context, tank_id: int, tank_code: str):
    page = context.new_page()
    url = f"{BASE_URL}/en/tankdetail/{tank_id}/{tank_code}"
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(800)

    text = page.inner_text("body")
    temperature_c, timestamp, status = extract_latest(text)

    page.close()
    return {
        "tank_id": tank_id,
        "tank_code": tank_code,
        "temperature_c": temperature_c,
        "last_update": timestamp,
        "status_text": status
    }

def post_to_base44(update_url: str, webhook_key: str, payload: dict):
    """
    Calls Base44 UpdateTank endpoint.
    We support both the old param name (temperature) and the new (temperature_c).
    """
    params = {
        "tank_id": payload["tank_id"],
        "temperature": payload["temperature_c"],  # backward compatible
    }
    # optional nicer params if your UpdateTank supports them
    if webhook_key:
        params["key"] = webhook_key
    if payload.get("tank_code"):
        params["tank_code"] = payload["tank_code"]
    if payload.get("last_update"):
        params["last_update"] = payload["last_update"]
    if payload.get("status_text"):
        params["status_text"] = payload["status_text"]

    r = requests.get(update_url, params=params, timeout=30)
    return r.status_code, r.text[:200]

def main():
    paccool_user = os.environ.get("PACCOOL_USER")
    paccool_pass = os.environ.get("PACCOOL_PASS")
    base44_update_url = os.environ.get("BASE44_UPDATE_URL")  # optional for now
    webhook_key = os.environ.get("WEBHOOK_KEY", "")

    if not paccool_user or not paccool_pass:
        print("Missing PACCOOL_USER or PACCOOL_PASS env vars.")
        sys.exit(1)

    with open("tanks.json", "r", encoding="utf-8") as f:
        tanks = json.load(f)

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        page = context.new_page()
        login(page, paccool_user, paccool_pass)
        page.close()

        for t in tanks:
            res = fetch_one_tank(context, int(t["tank_id"]), str(t["tank_code"]))
            results.append(res)

        context.close()
        browser.close()

    print("Fetched results:")
    for r in results:
        print(r)

    # If Base44 URL is provided - push updates
    if base44_update_url:
        print("\nPushing to Base44...")
        for r in results:
            if r["temperature_c"] is None:
                print(f"Skip tank {r['tank_id']} - temperature not found")
                continue
            code, text = post_to_base44(base44_update_url, webhook_key, r)
            print(f"UpdateTank tank_id={r['tank_id']} -> {code} {text}")

if __name__ == "__main__":
    main()
