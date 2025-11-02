import os
import re
import json
import requests
import qrcode
import shutil
from http.cookies import SimpleCookie
from email.utils import parsedate_to_datetime
from playwright.sync_api import sync_playwright

current_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_dir)

csrftoken = ""
p20t = ""
expirationTimestamp = -1

def launch_persistent_ctx(pw, reset=False):
    user_data_dir = os.path.expanduser("~/.config/playwright-binance")
    if reset:
        if os.path.exists(user_data_dir):
            shutil.rmtree(user_data_dir)

    headless = os.environ.get("HEADLESS", "1").lower() in ("1", "true", "yes")

    common_kwargs = dict(
        user_data_dir=user_data_dir,
        headless=headless,
        args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--headless=new",
            "--disable-gpu",
            "--window-size=1280,960"
        ],
        viewport={"width": 1280, "height": 960},
        locale="zh-CN",
    )

    return pw.chromium.launch_persistent_context(**common_kwargs)


def get_token(reset=False):
    with sync_playwright() as pw:
        ctx = launch_persistent_ctx(pw, reset=reset)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        qr_results = []

        def update_p20t_from_context():
            try:
                cookies = ctx.cookies(["https://accounts.binance.com", "https://www.binance.com"])
                c = next((c for c in cookies if c.get("name") == "p20t"), None)
                token = c.get("value", "")
                if not token: return
                global p20t
                p20t = token
            except:
                pass

        def on_request(req):
            try:
                url = req.url
                if "https://www.binance.com/fapi/v1/ticker/24hr" in url:
                    token = req.headers.get("csrftoken", "")
                    if not token: return
                    global csrftoken
                    csrftoken = token
            except:
                pass

        def on_request_finished(req):
            try:
                url = req.url
                if "https://accounts.binance.com/bapi/accounts/v2/public/qrcode/login/get" in url:
                    resp = req.response()
                    if not resp: return
                    data = resp.json()
                    if not data.get("success"): return
                    code = data["data"]["qrCode"]
                    if code not in qr_results:
                        qr_results.append(code)
                        print("请使用 Binance App 扫描以下二维码登录")
                        qr = qrcode.QRCode()
                        qr.add_data(code)
                        qr.print_ascii(invert=True)
                        img = qr.make_image(fill_color="black", back_color="white")
                        img = img.convert("RGB")
                        img.save("qrcode.jpg", format="JPEG", quality=100)
                elif "https://accounts.binance.com/bapi/accounts/v2/private/authcenter/setTrustDevice" in url:
                    resp = req.response()
                    if not resp: return
                    hdrs_arr = resp.headers_array()
                    date_hdr = resp.headers.get("date") or resp.header_value("date")
                    if not hdrs_arr or not date_hdr: return
                    sc_values = [h.get("value", "") for h in hdrs_arr if h.get("name", "").lower() == "set-cookie"]
                    m = next((m for sc in sc_values for m in SimpleCookie(sc).values() if m.key == "p20t"), None)
                    if not m: return
                    global p20t, expirationTimestamp
                    p20t = m.value
                    expirationTimestamp = int(m["max-age"]) + int(parsedate_to_datetime(date_hdr).timestamp())
            except:
                pass

        page.on("request", on_request)
        page.on("requestfinished", on_request_finished)
        ctx.on("request", on_request)
        ctx.on("requestfinished", on_request_finished)

        page.goto("https://accounts.binance.com/zh-CN/login?loginChannel=&return_to=", wait_until="domcontentloaded")

        while True:
            page.wait_for_timeout(1500)

            if csrftoken and p20t:
                print("csrftoken:", csrftoken)
                print("p20t:", p20t)
                print("expirationTimestamp:", expirationTimestamp)
                token_dict = {"csrftoken": csrftoken, "p20t": p20t, "expirationTimestamp": expirationTimestamp}
                with open("token.json", "w") as f:
                    f.write(json.dumps(token_dict, indent=4, ensure_ascii=False))
                ctx.close()
                break

            try:
                if "accounts.binance.com" in page.url:
                    if page.get_by_text(re.compile("登录")).count() > 0 and page.get_by_text(re.compile("邮箱/手机号码")).count() > 0 and page.get_by_text(re.compile("用手机相机扫描")).count() == 0:
                        page.get_by_role("button", name=re.compile("登录")).first.click(timeout=1200, force=True)

                    if page.get_by_text(re.compile("刷新二维码")).count() > 0:
                        page.get_by_role("button", name=re.compile("刷新二维码")).first.click(timeout=1200, force=True)

                    if page.get_by_text(re.compile("保持登录状态")).count() > 0:
                        page.get_by_role("button", name=re.compile("是")).first.click(timeout=1200, force=True)
                else:
                    update_p20t_from_context()

            except:
                pass

def place_order_web():
    url = "https://www.binance.com/bapi/futures/v1/private/future/event-contract/place-order"
    headers = {
        "content-type": "application/json",
        "clienttype": "web",
        "csrftoken": csrftoken,
        "cookie": f"p20t={p20t}"
    }
    data = {
        "orderAmount": "5",
        "timeIncrements": "TEN_MINUTE",
        "symbolName": "BTCUSDT",
        "payoutRatio": "0.80",
        "direction": "LONG"
    }
    response = requests.post(url, headers=headers, json=data)
    return response.json()

if __name__ == "__main__":
    get_token(reset=False) # 设置 reset=True 清除浏览器缓存
    # result = place_order_web()
    # print("下单结果:", result)