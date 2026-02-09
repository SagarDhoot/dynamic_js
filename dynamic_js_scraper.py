#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic JS Scraper (Playwright-based)

- Reads a list of domains or URLs from an input file.
- For each target:
    - Visits the URL (handles inputs with or without scheme)
    - Scrolls and clicks common interactive elements
    - Captures JS responses (static and lazy-loaded)
    - Downloads JS files into a per-domain folder
"""

import argparse
import asyncio
import os
import re
import sys
import subprocess
from urllib.parse import urlparse, unquote
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ===================================================================
# AUTO-PROXY (mitmdump)
# ===================================================================

def start_mitmdump():
    """Start mitmdump on port 8080."""
    try:
        print("[+] Starting mitmdump proxy on port 8080...")
        proc = subprocess.Popen(
            ["mitmdump", "-w", "mitm_traffic.log", "--listen-port", "8080"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return proc
    except FileNotFoundError:
        print("[!] mitmdump not found. Install with: pip install mitmproxy")
        print("[!] Continuing without proxy.")
        return None


def stop_mitmdump(proc):
    """Stop mitmdump if running."""
    if not proc:
        return
    print("[+] Stopping mitmdump proxy...")
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


# ===================================================================
# Helper Functions
# ===================================================================

def safe_filename(name):
    name = unquote(name)
    name = re.sub(r"[^0-9A-Za-z.\-_]", "_", name)
    return name[:200]


def parse_raw_cookie_string(cookie_str, domain):
    cookies = []
    if not cookie_str:
        return cookies
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, value = part.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/"
            })
    return cookies


def parse_cookies_file(path, domain):
    cookies = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) != 7:
                    continue
                c_domain, _, c_path, _, _, name, value = parts

                if c_domain.startswith("."):
                    if domain.endswith(c_domain.lstrip(".")):
                        cookies.append({
                            "name": name,
                            "value": value,
                            "domain": c_domain,
                            "path": c_path or "/"
                        })
                elif c_domain == domain:
                    cookies.append({
                        "name": name,
                        "value": value,
                        "domain": c_domain,
                        "path": c_path or "/"
                    })
    except Exception as e:
        print(f"[!] Failed to parse cookies file {path}: {e}")
    return cookies


async def fetch_and_save(session, url, save_path):
    try:
        resp = await session.get(url, timeout=30000)
        if resp.ok:
            text = await resp.text()
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(text)
            return True, None
        return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


# ===================================================================
# Scrape a Single Target
# ===================================================================

async def scrape_domain(semaphore, playwright, raw_target, args):
    async with semaphore:
        parsed = urlparse(raw_target)

        if parsed.scheme:
            urls_to_try = [raw_target]
            domain = parsed.netloc
        else:
            urls_to_try = [f"https://{raw_target}", f"http://{raw_target}"]
            domain = raw_target

        proxy_server = None
        if args.auto_proxy:
            proxy_server = "http://127.0.0.1:8080"
        elif args.proxy:
            proxy_server = args.proxy

        launch_kwargs = {
            "headless": not args.no_headless,
            "args": ["--no-sandbox"]
        }
        if proxy_server:
            launch_kwargs["proxy"] = {"server": proxy_server}

        browser = await playwright.chromium.launch(**launch_kwargs)
        context = await browser.new_context()

        if args.cookie:
            await context.add_cookies(parse_raw_cookie_string(args.cookie, domain))
        if args.cookies_file:
            await context.add_cookies(parse_cookies_file(args.cookies_file, domain))

        page = await context.new_page()
        js_urls = set()

        async def on_response(response):
            try:
                url = response.url
                ctype = response.headers.get("content-type", "")
                if url.endswith(".js") or "javascript" in ctype.lower() or re.search(r"\.js(\?|$)", url):
                    js_urls.add(url)
            except Exception:
                pass

        page.on("response", on_response)

        reachable = False
        for url in urls_to_try:
            try:
                await page.goto(url, timeout=args.timeout_ms)
                reachable = True
                break
            except Exception:
                pass

        if not reachable:
            print(f"[!] Could not load {raw_target}")
            await browser.close()
            return

        print(f"[+] Loaded {raw_target}, interacting...")

        for _ in range(6):
            try:
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(800)
            except Exception:
                pass

        selectors = ["button", "a", "[role='button']", "[onclick]", "[data-action]"]
        for sel in selectors:
            try:
                elems = page.locator(sel)
                for i in range(min(await elems.count(), args.click_limit)):
                    try:
                        await elems.nth(i).click(timeout=2500)
                        await page.wait_for_timeout(400)
                    except Exception:
                        pass
            except Exception:
                pass

        output_dir = Path(args.output).expanduser()
        domain_dir = output_dir / domain.replace(":", "_")
        domain_dir.mkdir(parents=True, exist_ok=True)

        req_ctx = await playwright.request.new_context(
            proxy={"server": proxy_server} if proxy_server else None
        )

        saved = 0
        for url in list(js_urls)[:args.max_js]:
            fname = safe_filename(os.path.basename(urlparse(url).path) or "script.js")
            save_path = domain_dir / fname
            ok, err = await fetch_and_save(req_ctx, url, save_path)
            if ok:
                saved += 1
            else:
                print(f"[x] Failed {url}: {err}")

        print(f"[=] Done {raw_target}: {saved} JS files saved")

        await req_ctx.dispose()
        await browser.close()


# ===================================================================
# Main Async Runner
# ===================================================================

async def main_async(args):
    mitm_proc = start_mitmdump() if args.auto_proxy else None

    input_path = Path(args.input).expanduser()
    if not input_path.is_file():
        print(f"[ERROR] Input file not found: {input_path}")
        stop_mitmdump(mitm_proc)
        sys.exit(2)

    with open(input_path) as f:
        targets = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not targets:
        print("[ERROR] No targets found.")
        stop_mitmdump(mitm_proc)
        sys.exit(3)

    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    print(f"[+] Targets: {len(targets)} | Concurrency: {args.concurrency}")

    async with async_playwright() as playwright:
        await asyncio.gather(
            *[scrape_domain(semaphore, playwright, t, args) for t in targets],
            return_exceptions=True
        )

    stop_mitmdump(mitm_proc)


# ===================================================================
# Entry Point
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Dynamic JS Scraper (Playwright) with optional auto-proxy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("-i", "--input", default="target_alive_subdomains.txt")
    parser.add_argument("-o", "--output", default="dynamic_js")
    parser.add_argument("-c", "--concurrency", type=int, default=4)

    parser.add_argument("--max-js", type=int, default=500)
    parser.add_argument("--click-limit", type=int, default=5)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--no-headless", action="store_true")

    parser.add_argument("--cookie")
    parser.add_argument("--cookies-file")
    parser.add_argument("--proxy")
    parser.add_argument("--auto-proxy", action="store_true")

    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[!] Interrupted")
        sys.exit(1)


if __name__ == "__main__":
    main()
