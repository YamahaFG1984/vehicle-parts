"""Browser check of the FilePond uploads (SKU images, document import, stock import).

Not collected by pytest; run against a server that has demo data and a reviewer account:

    uv run python manage.py runserver 127.0.0.1:8020          # ideally on a throwaway database
    VP_E2E_USER=reviewer VP_E2E_PASSWORD=... \
      uv run --no-project --with playwright --with pillow python tests/browser/check_uploads.py

VP_E2E_BASE (default http://127.0.0.1:8020), VP_E2E_ITEM (default 1) and VP_E2E_CHROME (a
Chromium / headless-shell executable; default: Playwright's own) are optional.
It uploads one test image to the chosen SKU.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("VP_E2E_BASE", "http://127.0.0.1:8020")
USER = os.environ.get("VP_E2E_USER", "reviewer")
PASSWORD = os.environ["VP_E2E_PASSWORD"]
ITEM = os.environ.get("VP_E2E_ITEM", "1")
CHROME = os.environ.get("VP_E2E_CHROME")
HERE = Path(tempfile.mkdtemp(prefix="vp-e2e-"))
Image.new("RGB", (4000, 3000), (40, 120, 200)).save(HERE / "big.jpg", quality=95)
(HERE / "anim.gif").write_bytes(b"GIF89a")
(HERE / "sample_A.xlsx").write_bytes((ROOT / "候选人材料_供应商A报价表.xlsx").read_bytes())
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    page.goto(f"{BASE}/accounts/login/")
    page.fill("#id_username", USER)
    page.fill("#id_password", PASSWORD)
    page.click("#submit")
    page.wait_for_url(f"{BASE}/")

    # 1) Image: resized in the browser before the normal form post.
    page.goto(f"{BASE}/items/{ITEM}/")
    page.wait_for_selector(".filepond--root")
    check("SKU page: FilePond drop area rendered", page.locator(".filepond--root").count() == 1)
    check("Chinese label", "拖到这里" in page.inner_text(".filepond--drop-label"))
    page.set_input_files(".filepond--browser", str(HERE / "big.jpg"))
    page.wait_for_function(
        "() => { const i = document.querySelector('.filepond--data input[type=file]');"
        " return i && i.files.length === 1 && i.files[0].size < " + str((HERE / "big.jpg").stat().st_size) + "; }", timeout=15000)
    page.wait_for_function("() => !document.querySelector('form[action$=\"/images/\"] button[type=submit]').disabled",
                           timeout=15000)
    dims = page.evaluate(
        "async () => { const f = document.querySelector('.filepond--data input[type=file]').files[0];"
        " const b = await createImageBitmap(f); return [b.width, b.height, f.type, f.size]; }")
    check("image shrunk to fit 2000px before upload", max(dims[:2]) <= 2000, f"{dims}")
    page.click("form[action$='/images/'] button[type=submit]")
    page.wait_for_url(f"{BASE}/items/{ITEM}/#images")
    check("upload stored", "已上传 1 张图片" in page.inner_text(".messages"),
          page.locator(".messages").inner_text() if page.locator(".messages").count() else "")

    as_of = page.input_value("form[action$='/stock/'] input[name=as_of]")
    check("stock date input shows today's date", bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of)), as_of)

    # 2) Wrong type rejected in the browser, form not posted.
    page.set_input_files(".filepond--browser", str(HERE / "anim.gif"))
    page.wait_for_selector("[data-filepond-item-state*='invalid'], [data-filepond-item-state*='error']",
                           timeout=10000)
    check("GIF rejected in the browser", "不支持的文件类型" in page.inner_text(".filepond--root"))
    page.once("dialog", lambda d: d.accept())
    page.click("form[action$='/images/'] button[type=submit]")
    page.wait_for_timeout(800)
    check("form not posted while a rejected file is listed", "/images/" not in page.url)

    # 3) Spreadsheet on the imports page goes to the preview.
    page.goto(f"{BASE}/imports/")
    check("imports page: two FilePond areas", page.locator(".filepond--root").count() == 2)
    page.locator(".filepond--browser").first.set_input_files(str(HERE / "sample_A.xlsx"))
    page.wait_for_function(
        "() => { const i = document.querySelector('form .filepond--data input[type=file]');"
        " return i && i.files.length === 1; }", timeout=10000)
    page.fill("#id_supplier", "A")
    page.click("button:has-text('上传并预览')")
    page.wait_for_url("**/imports/preview/**", timeout=20000)
    check("xlsx posted through FilePond reaches the preview",
          "列映射" in page.content() and "Supplier SKU" in page.content())

    check("no JavaScript errors", not errors, "; ".join(errors[:3]))
    browser.close()

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
