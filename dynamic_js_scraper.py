#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic JS Scraper (Playwright-based)

- Reads a list of domains (subdomains) from an input file.
- For each domain:
    - Visits https://domain (fallback to http://domain)
    - Scrolls and clicks common interactive elements
    - Captures JS responses (static and lazy-loaded)
    - Downloads JS files and saves them into a folder named after the domain
      in the chosen output directory (default: current working directory).
"""

import argparse
import asyncio
import os
import re
import sys
import subprocess   # <-- REQUIRED for mitmdump
from urllib.parse import urlparse, unquote
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ===================================================================
# AUTO-PROXY (mitmdump) — ADDED BLOCK
# ===================================================================

def start_mitmdump():
    """Start mitmdump on port 8080 automatically."""
    try:
        print("[+] Starting mitmdump proxy on port 8080...")
        proc = subprocess.Popen(
            ["mitmdump", "-w", "mitm_traffic.log", "--listen-port", "8080"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return proc
    except FileNotFoundError:
        print("[!] mitmdump not found. Install using: pip install mitmproxy")
        print("[!] Continuing WITHOUT proxy.")
        return None


def stop_mitmdump(proc):
    """Stop mitmdump if running."""
    if proc is None:
        return
    print("[+] Stopping mitmdump proxy...")
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


# --------------------------
# Helper Functions
# --------------------------

def safe_filename(name):
    """Make a filesystem-safe filename."""
    name = unquote(name)
    name = re.sub(r"[^0-9A-Za-z.\-_]", "_", name)
    return name[:200]  # limit length


def parse_raw_cookie_string(cookie_str, domain):
    cookies = []
    if not cookie_str:
        return cookies
    parts = cookie_str.split(";")
    for part in parts:
        part = part.strip()
        if "=" in part:
            name, value = part.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain if domain.startswith(".") else domain,
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
                c_domain, flag, c_path, secure, expiry, name, value = parts

                if c_domain.startswith("."):
                    if domain.endswith(c_domain) or ("." + domain) == c_domain:
                        cookies.append({
                            "name": name,
                            "value": value,
                            "domain": c_domain,
                            "path": c_path or "/"
                        })
                else:
                    if c_domain == domain:
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
        else:
            return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


# --------------------------
# Scrape a Single Domain
# --------------------------

async def scrape_domain(semaphore, playwright, domain, args):
    async with semaphore:
        headless = not args.no_headless

        # ============================================================
        # UPDATED PROXY LOGIC — NOW SUPPORTS auto-proxy
        # ============================================================
        proxy_server = None
        if args.auto_proxy:
            proxy_server = "http://127.0.0.1:8080"
        elif args.proxy:
            proxy_server = args.proxy

        launch_kwargs = {"headless": headless, "args": ["--no-sandbox"]}

        if proxy_server:
            launch_kwargs["proxy"] = {"server": proxy_server}

        browser = await playwright.chromium.launch(**launch_kwargs)

        context = await browser.new_context()

        # Apply cookies
        if args.cookie:
            try:
                await context.add_cookies(parse_raw_cookie_string(args.cookie, domain))
            except Exception as e:
                print(f"[!] Failed adding cookie: {e}")

        if args.cookies_file:
            try:
                await context.add_cookies(parse_cookies_file(args.cookies_file, domain))
            except Exception as e:
                print(f"[!] Failed adding cookies file: {e}")

        page = await context.new_page()
        js_urls = set()

        async def on_response(response):
            try:
                url = response.url
                ctype = response.headers.get("content-type", "") or ""
                if url.endswith(".js") or "javascript" in ctype.lower() or re.search(r"\.js(\?|$)", url):
                    js_urls.add(url)
            except:
                pass

        page.on("response", on_response)

        # Try visiting https:// then http://
        reachable = False

		parsed = urlparse(domain)
		if parsed.scheme:
		    urls_to_try = [domain]
		else:
		    urls_to_try = [f"https://{domain}", f"http://{domain}"]

		for url in urls_to_try:
		    try:
		        await page.goto(url, timeout=args.timeout_ms)
		        reachable = True
		        break
		    except:
		        pass


        if not reachable:
            print(f"[!] Could not load {domain}")
            await browser.close()
            return

        print(f"[+] Loaded {domain}, interacting...")

        # Scroll
        for _ in range(6):
            try:
                await page.mouse.wheel(0, 2500)
                await page.wait_for_timeout(800)
            except:
                pass

        # Click interactions
        selectors = ["button", "a", "[role='button']", "[onclick]", "[data-action]"]
        for sel in selectors:
            try:
                elems = page.locator(sel)
                count = await elems.count()
                for i in range(min(count, args.click_limit)):
                    try:
                        await elems.nth(i).click(timeout=2500)
                        await page.wait_for_timeout(400)
                    except:
                        pass
            except:
                pass

        # Save JS output
        output_dir = Path(args.output).expanduser()
        domain_dir = output_dir.joinpath(domain.replace(":", "_"))
        domain_dir.mkdir(parents=True, exist_ok=True)

        req_kwargs = {}
        if proxy_server:
            req_kwargs["proxy"] = {"server": proxy_server}

        request_context = await playwright.request.new_context(**req_kwargs)

        saved = 0
        for url in list(js_urls)[:args.max_js]:
            try:
                fname = safe_filename(os.path.basename(urlparse(url).path) or "script.js")
                save_path = domain_dir.joinpath(fname)
                ok, err = await fetch_and_save(request_context, url, str(save_path))
                if ok:
                    saved += 1
                    print(f"[+] {domain}: saved → {url}")
                else:
                    print(f"[x] {domain}: failed {url}: {err}")
            except Exception as e:
                print(f"[x] Error saving {url}: {e}")

        print(f"[=] Done {domain}: {saved} JS files saved.")
        await request_context.dispose()
        await browser.close()


# --------------------------
# Main Async Runner
# --------------------------

async def main_async(args):

    # ======================================================
    # START AUTO-PROXY HERE
    # ======================================================
    mitm_proc = start_mitmdump() if args.auto_proxy else None

    input_path = Path(args.input).expanduser()
    if not input_path.is_file():
        print(f"[ERROR] Input file not found: {input_path}")
        stop_mitmdump(mitm_proc)
        sys.exit(2)

    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(input_path, "r") as f:
        domains = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not domains:
        print("[ERROR] No domains found.")
        stop_mitmdump(mitm_proc)
        sys.exit(3)

    concurrency = max(1, int(args.concurrency))
    semaphore = asyncio.Semaphore(concurrency)

    print(f"[+] Targets: {len(domains)} | Concurrency: {concurrency}")

    async with async_playwright() as playwright:
        tasks = [
            asyncio.create_task(scrape_domain(semaphore, playwright, domain, args))
            for domain in domains
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ======================================================
    # STOP AUTO-PROXY HERE
    # ======================================================
    stop_mitmdump(mitm_proc)


# --------------------------
# Main Entry
# --------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Dynamic JS Scraper (Playwright) with optional Auto-Proxy (mitmdump)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("-i", "--input", default="target_alive_subdomains.txt")
    parser.add_argument("-o", "--output", default="dynamic_js")
    parser.add_argument("-c", "--concurrency", default=4, type=int)

    parser.add_argument("--max-js", default=500, type=int)
    parser.add_argument("--click-limit", default=5, type=int)
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--timeout-ms", default=30000, type=int)

    parser.add_argument("--cookie")
    parser.add_argument("--cookies-file")

    parser.add_argument("--proxy", help="Manual proxy use")

    # ======================================================
    # NEW FLAG: AUTO-PROXY
    # ======================================================
    parser.add_argument(
        "--auto-proxy",
        action="store_true",
        help="Automatically start mitmdump and route all traffic through it."
    )

    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.")
        sys.exit(1)


if __name__ == "__main__":
    main()
