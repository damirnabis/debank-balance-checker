<!-- Language Switcher -->
[![English](https://img.shields.io/badge/lang-English-blue.svg)](README.md)
[![Русский](https://img.shields.io/badge/язык-Русский-red.svg)](README.ru.md)

# 💰 Debank balance checker (EVM/Solana)

The script automatically collects wallet data from Debank and outputs the results to an HTML page.

Supports:
- Asynchronous multi-threaded parsing 🧩
- Proxy support 🌍
- Automatic HTML page generation with collected data 📊
- Auto-refresh of data every N minutes 🔁


## 🛠 Installation

1. Clone or download the project:

```bash
git clone https://github.com/damirnabis/debank-balance-checker.git
```
```bash
cd debank-balance-checker
```

2. Create and activate a virtual environment (recommended):

```bash
python -m venv venv
```
```bash
venv\Scripts\activate    # for Windows
```
```bash          
source venv/bin/activate # for macOS/Linux
```

3. Install the dependencies:

```bash
pip install -r requirements.txt
```
```bash
playwright install
```


## ⚙️ Configuration

1. Fill the file data/addresses.txt with EVM and/or Solana wallet public addresses.

2. Optional: to run the script in multiple threads, fill data/proxies.txt with proxies.

3. Parameters in config.py:

- MAX_CONCURRENT = 10       - number of threads
- UPDATE_DATA_MIN = 15      - data update interval (minutes)
- UPDATE_HTML_SEC = 60      - HTML refresh interval (seconds)


## 🚀 Run

```bash
python main.py
```


## After starting:

1. On the first run, the script begins collecting wallet data from Debank and displays progress.
2. The script will automatically open results.html — a browser page with balances and wallet lists.

⏱ Auto-refresh

- The HTML page refreshes every UPDATE_HTML_SEC seconds.
- Data from Debank refreshes every UPDATE_DATA_MIN minutes.


## 🧩 Hotkeys
 
Ctrl+C — Stops all processes and closes Playwright.


## 📊 Output

After running, the script will automatically generate a results.html file displaying all wallets, networks, tokens, and DeFi projects, including their current balance in USD.