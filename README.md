# Dynamic JS Scraper

Dynamic JavaScript scraper for modern web applications. It uses a real browser to load targets, trigger user-like interactions, and capture JavaScript files loaded at runtime. Built for bug bounty and web security recon where JS is lazy-loaded or injected dynamically and missed by passive crawling.

A Playwright-based tool for **dynamically discovering JavaScript files** from modern web applications.

This tool is intended for **bug bounty reconnaissance and web security testing**, where many JavaScript files are:
- loaded only after user interaction
- lazy-loaded or injected at runtime
- hidden from passive JS collectors

---

## What this tool does

For each target domain, the tool:

- Launches a real Chromium browser
- Visits the target like a normal user
- Scrolls and interacts with the page
- Captures JavaScript files loaded dynamically
- Saves all discovered JS files **per domain**

The goal is to collect JavaScript that would otherwise be missed by static tools.

---

## Output structure

JavaScript files are saved in a deterministic layout:

```

dynamic_js/
└── subdomain.example.com/
├── main-app.js
├── chunk-xxxx.js
├── vendor-xxxx.js

````

Each subdomain has its own directory.

---

## Intended use cases

- Bug bounty reconnaissance
- Hidden API discovery
- IDOR and access-control testing
- JavaScript-heavy single-page applications
- Webpack / React / Vue-based frontends

This tool focuses on **collection only**.  
It does not analyze JavaScript or detect vulnerabilities.

---

## Installation

### Requirements
- Python 3.9 or newer
- A system capable of running Chromium

### Install dependencies
```bash
pip install -r requirements.txt
````

### Install Playwright browser

```bash
python3 -m playwright install chromium
```

---

## Usage

### Basic usage

```bash
python3 dynamic_js_scraper.py -i target_alive_subdomains.txt
```

Where `target_alive_subdomains.txt` contains one domain or URL per line, for example:

```
subdomain1.example.com
subdomain2.example.com
https://subdomain3.example.com
```

---

### Common options

* Control concurrency:

```bash
python3 dynamic_js_scraper.py -i targets.txt -c 6
```

* Route traffic through a proxy (e.g. Burp):

```bash
python3 dynamic_js_scraper.py -i targets.txt --proxy http://127.0.0.1:8080
```

* Inject cookies:

```bash
python3 dynamic_js_scraper.py -i targets.txt --cookie "session=abc; token=xyz"
```

### Installing as a global command (optional)

If you want to run the tool from anywhere after cloning the repository, you can make it available globally.

1. Make the script executable:

```bash
chmod +x dynamic_js_scraper.py
```

2. Move it into a directory in your PATH (for example `/usr/local/bin`):

```bash
sudo mv dynamic_js_scraper.py /usr/local/bin/dynamic_js
```

3. Run it from anywhere:

```bash
dynamic_js
```

Alternatively, you can create a symlink instead of moving the file:

```bash
sudo ln -s "$(pwd)/dynamic_js_scraper.py" /usr/local/bin/dynamic_js
```

## Using a virtual environment (optional)

If you run into Python dependency or environment issues, it is recommended to use a virtual environment.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
```
To exit the virtual environment:
```deactivate```

Using a virtual environment helps avoid conflicts with system-wide Python packages.

---

## Notes & limitations

* The tool does not analyze JavaScript content
* It does not perform vulnerability scanning
* It only collects resources the browser is allowed to load
* Authentication flows are out of scope by default

Further analysis (endpoints, secrets, access control issues) is intentionally left to the user.

---
