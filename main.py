import os
import sys
import re
from pathlib import Path
from datetime import datetime
import random
import json
import asyncio
from typing import Optional, Tuple
from playwright.async_api import async_playwright
from tqdm.asyncio import tqdm_asyncio, tqdm
import webbrowser
import signal

from config import *


OUTPUT_FILE = "results.html"
PROXIES_FILE = "proxies.txt"
ADDRESSES_FILE = "addresses.txt"
DATA_DIR = "data"
CHAIN_MAP_PATH = f"{DATA_DIR}/CHAIN_NAME_MAP.json"


# ======================
# Парсинг Debank
# ======================


def load_chain_name_map() -> dict[str, str]:
    """Загружает или создаёт файл с маппингом chain_id -> full_name"""
    os.makedirs("data", exist_ok=True)
    if os.path.exists(CHAIN_MAP_PATH):
        try:
            with open(CHAIN_MAP_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_chain_name_map(mapping: dict[str, str]):
    """Сохраняет маппинг chain_id -> full_name"""
    os.makedirs("data", exist_ok=True)
    with open(CHAIN_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


async def get_balance_chains_tokens(page, address: str) -> Tuple[Optional[float], dict]:
    """Парсит общий баланс, сети и токены с логотипами"""

    url = f"https://debank.com/profile/{address}"
    chains_result: dict[str, dict] = {}
    total_balance: Optional[float] = None
    chain_name_map = load_chain_name_map()

    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)

        # --- Показать все блоки ---
        for selector in [
            "div.AssetsOnChain_unfoldBtn__ov19o",  # сети
            "div.TokenWallet_showAll__PecCN",      # токены
            "div.Portfolio_projectsShowAll__Huhry"  # протоколы
        ]:
            try:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(1500)
            except:
                pass

        # --- Общий баланс ---
        total_balance = 0
        n = 0
        while total_balance <= 0:
            n += 1
            if n > 3: break
            element = await page.wait_for_selector("div[class*='HeaderInfo_totalAssetInner']", timeout=60000)
            text = await element.inner_text()
            total_balance = float(text.splitlines()[0].replace("$", "").replace(",", "").strip())

        if total_balance <= 0:
            return total_balance, {}

        # --- Сети ---
        while True:
            try:
                chain_items = await page.query_selector_all("div.AssetsOnChain_item__GBfMt")
                for item in chain_items:
                    chain_id = await item.get_attribute("data-chain")
                    if not chain_id:
                        continue
                    chain_id = chain_id.lower()

                    # Берём полное имя сети из нового класса
                    name_el = await item.query_selector("div.AssetsOnChain_chainName__jAJuC")
                    full_name = (await name_el.inner_text()).strip() if name_el else chain_id.capitalize()

                    # Логотип сети (URL)
                    logo_el = await item.query_selector("img.AssetsOnChain_chainlogo__xUCu5") or await item.query_selector("img")
                    logo_url = await logo_el.get_attribute("src") if logo_el else None

                    # Обновляем/дополняем карту соответствий
                    if chain_id not in chain_name_map:
                        chain_name_map[chain_id] = full_name

                    # Добавляем в результат
                    chains_result[full_name] = {
                        "logo_url": logo_url,
                        "tokens": {},
                        "defi": {},
                        "total": 0.0
                    }
                
                break
            except Exception as e:
                tqdm.write(f"⚠️ Ошибка при парсинге сетей:", e)
                asyncio.sleep(5)
                continue

        # --- Токены ---
        while True:
            try:
                token_rows = await page.query_selector_all("div.db-table-row")
                for row in token_rows:
                    token_el = await row.query_selector("a.TokenWallet_detailLink__goYJR")
                    token_logo_el = await row.query_selector("img.db-lazyMedia-img")

                    if not token_el:
                        continue

                    token_name = (await token_el.inner_text()).strip()
                    href = await token_el.get_attribute("href")
                    if not href:
                        continue

                    # chain_id из href
                    m = re.match(r"^/token/([^/]+)/", href)
                    chain_id = m.group(1).lower() if m else "unknown"

                    # Определяем полное имя сети по карте
                    full_chain_name = chain_name_map.get(chain_id, chain_id)

                    cells = await row.query_selector_all("div.db-table-cell")
                    if len(cells) < 4:
                        continue

                    amount_text = (await cells[2].inner_text()).strip()
                    usd_text = (await cells[3].inner_text()).strip()
                    try:
                        usd_value = float(usd_text.replace("$", "").replace(",", "").strip())
                    except:
                        usd_value = 0.0

                    logo_url = await token_logo_el.get_attribute("src") if token_logo_el else None

                    if full_chain_name not in chains_result:
                        chains_result[full_chain_name] = {"tokens": {}, "defi": {}, "total": 0.0}

                    chains_result[full_chain_name]["tokens"][token_name] = {
                        "amount": amount_text,
                        "usd": usd_value,
                        "logo_url": logo_url
                    }
                    chains_result[full_chain_name]["total"] += usd_value
                
                break
            except Exception as e:
                tqdm.write(f"⚠️ Ошибка при парсинге токенов:", e)
                asyncio.sleep(5)
                continue                

        # --- Протоколы (DeFi) ---
        while True:
            try:
                defi_blocks = await page.query_selector_all("div.Portfolio_defiItem__cVQM-")
                for defi_block in defi_blocks:
                    project_divs = await defi_block.query_selector_all("div.Project_project__GCrhx")
                    for proj in project_divs:
                        try:
                            # --- Название и логотип протокола ---
                            title_el = await proj.query_selector("div.ProjectTitle_name__x2ZNR span")
                            project_name = (await title_el.inner_text()).strip() if title_el else "Unknown"

                            logo_el = await proj.query_selector("div.ProjectTitle_projectIcon__yiNo9 img")
                            logo_url = await logo_el.get_attribute("src") if logo_el else None

                            # --- Общий баланс протокола ---
                            total_el = await proj.query_selector("div.projectTitle-number")
                            total_usd = 0.0
                            if total_el:
                                txt = (await total_el.inner_text()).replace("$", "").replace(",", "").strip()
                                try:
                                    total_usd = float(txt)
                                except:
                                    total_usd = 0.0

                            # --- Определение сети (chain_id) ---
                            chain_id = "unknown"
                            first_link = await proj.query_selector("a.utils_detailLink__XnB7N")
                            if first_link:
                                href = await first_link.get_attribute("href")
                                m = re.match(r"^/token/([^/]+)/", href)
                                if m:
                                    chain_id = m.group(1).lower()
                            elif logo_url:
                                m = re.search(r"/([a-z0-9]+)[_.-]", logo_url)
                                if m:
                                    chain_id = m.group(1).lower()

                            full_chain_name = chain_name_map.get(chain_id, chain_id)

                            colums_data = await proj.query_selector_all("div.table_header__onfbK")
                            for col in colums_data:
                                col_text = (await col.inner_text()).lower()
                                colums_positions = {name: i + 1 for i, name in enumerate(col_text.split('\n'))}
                                break

                            # --- Позиции в таблице (пулы, токены, USD) ---
                            positions = []
                            rows = await proj.query_selector_all("div.table_contentRow__Mi3k5")
                            for row in rows:
                                try:
                                    # --- Берём весь HTML блока и парсим количество + тикер ---
                                    cell_pos = await row.query_selector(f"div:nth-child({colums_positions['balance']}) span")
                                    cell_pos_list = (await cell_pos.inner_text()).split('\n') if cell_pos else ""  
                                
                                    # Разделяем на количество и тикер
                                    for cell_pos_text in cell_pos_list:               
                                        parts = cell_pos_text.split()
                                        balance_text = parts[0] if len(parts) > 0 else ""
                                        ticker_text = parts[1] if len(parts) > 1 else ""
                                   
                                        if len(cell_pos_list) >= 1 and cell_pos_list.index(cell_pos_text) == 0:
                                            # --- USD сумма --- 
                                            cell_usd = await row.query_selector(f"div:nth-child({colums_positions['usd value']}) span") 
                                            usd_text = await cell_usd.inner_text() if cell_usd else ""
                                            try:
                                                usd_val = float(usd_text.replace("$", "").replace(",", ""))
                                            except:
                                                usd_val = 0.0
                                        else:
                                            usd_val = 0.0

                                        positions.append({
                                            "balance": balance_text,
                                            "ticker": ticker_text,
                                            "usd": usd_val
                                        })
                                except Exception as e:
                                    pass          
                                    # print("⚠️ Ошибка при обработке строки пула:", e)    
                        
                            # --- Добавление в итог ---
                            if full_chain_name not in chains_result:
                                chains_result[full_chain_name] = {"tokens": {}, "defi": {}, "total": 0.0}

                            chains_result[full_chain_name]["defi"][project_name] = {
                                "logo_url": logo_url,
                                "total_usd": total_usd,
                                "positions": positions
                            }
                            chains_result[full_chain_name]["total"] += total_usd

                        except Exception as e:
                            print("⚠️ Ошибка парсинга протокола:", e)
            
                break
            except Exception as e:
                tqdm.write(f"⚠️ Ошибка при парсинге протоколов:", e)
                asyncio.sleep(5)
                continue                 

        # --- Сортировка токенов по USD ---
        for ch in chains_result.values():
            ch["tokens"] = dict(sorted(ch["tokens"].items(), key=lambda x: x[1]["usd"], reverse=True))

        # Сохраняем обновлённую карту сетей
        save_chain_name_map(chain_name_map)

        return total_balance, chains_result

    except Exception as e:
        pass
        #tqdm.write(f"❌ Ошибка при парсинге [{address}]: {e}")


# ======================
# Работа через прокси
# ======================


def parse_proxy_line(line: str) -> dict:
    line = line.strip()
    if not line:
        raise ValueError("Empty proxy line")
    if "@" not in line:
        raise ValueError(f"Неверный формат прокси: {line}")
    auth, hostport = line.split("@", 1)
    if ":" not in auth or ":" not in hostport:
        raise ValueError(f"Неверный формат прокси: {line}")
    username, password = auth.split(":", 1)
    domain, port = hostport.split(":", 1)
    server = f"http://{domain}:{port}"  # если нужна socks5 — замените
    return {"server": server, "username": username, "password": password}


async def try_with_proxy(playwright, browser_type, address: str, proxy_cfg: dict) -> Tuple[Optional[float], dict]:
    browser = await browser_type.launch(headless=True)
    try:
        context = await browser.new_context(proxy=proxy_cfg, viewport={"width": 1200, "height": 800})
        page = await context.new_page()
        try:
            bal, chains = await get_balance_chains_tokens(page, address)
            return bal, chains
        finally:
            if not page.is_closed():
                await page.close()
            await context.close()
    finally:
        await browser.close()


class ProxyRotator:
    def __init__(self, proxies: list[str]):
        self._proxies = proxies
        self._lock = asyncio.Lock()
        self._idx = 0

    async def get_next_proxy(self) -> dict:
        async with self._lock:
            if not self._proxies:
                raise RuntimeError("No proxies available")
            proxy = parse_proxy_line(self._proxies[self._idx])
            self._idx = (self._idx + 1) % len(self._proxies)
            return proxy


async def process_address(playwright, browser_type, address: str, semaphore: asyncio.Semaphore, proxy_rotator: ProxyRotator):
    async with semaphore:
        last_exc = None
        proxies = proxy_rotator._proxies
        total = len(proxies)

        # 🔹 Если нет прокси — работаем напрямую (без прокси)
        if total == 0:
            while True:
                await asyncio.sleep(random.uniform(1.2, 3.5))
                try:
                    bal, chains = await try_with_proxy(playwright, browser_type, address, None)

                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(os.path.join(DATA_DIR, f"{address}.json"), "w", encoding="utf-8") as f:
                        json.dump(
                            {"address": address, "balance": bal, "chains": chains},
                            f,
                            ensure_ascii=False,
                            indent=2
                        )
                    tqdm.write(f"✓ [{address}] Данные успешно получены из Debank")
                    return None

                except Exception as e:
                    last_exc = e
                    tqdm.write(
                        f"⚠️ [{address}] Ошибка получения данных: {e}, пробуем еще раз..."
                    )

        # 🔹 Иначе работаем через прокси
        await asyncio.sleep(random.uniform(0.1, 1.0))
        start_idx = random.randint(0, total - 1)
        proxy_attempt = 0

        while True:
            if "shutdown_flag" in globals() and shutdown_flag.is_set():
                tqdm.write(f"🛑 [{address}] Остановка по сигналу завершения.")
                return f"[{address}] cancelled"

            proxy_cfg = parse_proxy_line(proxies[(start_idx + proxy_attempt) % total])

            for attempt in range(ATTEMPTS_PER_PROXY):
                try:
                    bal, chains = await try_with_proxy(playwright, browser_type, address, proxy_cfg)

                    # 💾 сохраняем результат
                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(os.path.join(DATA_DIR, f"{address}.json"), "w", encoding="utf-8") as f:
                        json.dump(
                            {"address": address, "balance": bal, "chains": chains},
                            f,
                            ensure_ascii=False,
                            indent=2
                        )

                    tqdm.write(f"✓ [{address}] Данные успешно получены из Debank | {proxy_cfg.get('server')}")
                    return None

                except Exception as e:
                    last_exc = e
                    tqdm.write(
                        f"⚠️ [{address}] Попытка {attempt+1}/{ATTEMPTS_PER_PROXY} через {proxy_cfg.get('server')} не удалась: {e}"
                    )
                    await asyncio.sleep(0.5)

            proxy_attempt += 1
            if proxy_attempt >= total:
                proxy_attempt = 0
                tqdm.write(f"🔁 [{address}] Все прокси пройдены, начинаем новый круг... (последняя ошибка: {last_exc})")
                await asyncio.sleep(2.0)


# ======================
# HTML генератор
# ======================


def generate_html(data_dir: str = "data", output_file: str = "results.html"):
    all_data = {}
    total_balance = 0.0

    # Загрузка json'ов
    for fname in os.listdir(data_dir):
        if fname.endswith(".json") and fname != "CHAIN_NAME_MAP.json":
            path = os.path.join(data_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                addr = d.get("address") or fname[:-5]
                d["address"] = addr
                d["balance"] = float(d.get("balance", 0.0) or 0.0)
                all_data[addr] = d
                total_balance += d["balance"]
            except Exception as e:
                print(f"⚠️ Ошибка чтения {fname}: {e}")

    # Порядок кошельков по балансу (убывание)
    wallet_order = [addr for addr, _ in sorted(all_data.items(), key=lambda kv: kv[1].get("balance", 0.0), reverse=True)]

    # Собираем HTML как обычную строку
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Wallet Explorer</title>
<style>
body {
  font-family: Arial, sans-serif;
  margin: 0; padding: 0;
  display: flex; flex-direction: column;
  height: 100vh; overflow: hidden;
}
#topbar {
  flex: 0 0 auto;
  border-bottom: 1px solid #ccc;
  background: #fafafa;
  padding: 6px 10px;
  display: flex; align-items: center; justify-content: flex-end;
}
.main { flex: 1; display: flex; overflow: hidden; }
.column {
  overflow-y: auto;
  border-right: 1px solid #ddd;
  display: flex; flex-direction: column;
  min-width: 150px; padding: 0 5px;
}
.divider { width: 4px; cursor: col-resize; background: #eee; transition: background 0.15s; }
.divider:hover { background: #ccc; }
#wallets { width: 25%; max-width: 600px; }
#chains { width: 35%; max-width: 700px; }
#tokens { flex: 1; border-right: none; display:flex; flex-direction:column; }
.item {
  padding: 6px 10px;
  cursor: pointer;
  border-bottom: 1px solid #eee;
  display: flex; align-items: center; gap: 6px;
  transition: background 0.12s;
}
.item:hover { background: #f1fdf1; }
.selected { background: #d9f0d9 !important; font-weight: bold; }
.money { color: green; margin-left: auto; text-align: right; flex: 1; font-size: 16px; }
h2, .search-box, .network-filter, .tabs {
  position: sticky; top: 0;
  background: #fafafa;
  z-index: 3;
}
h2 {
  padding: 5px 0; margin: 0;
  border-bottom: 1px solid #ccc;
  display: flex; justify-content: space-between; align-items: center;
  font-size: 16px; font-weight: 600;
}
.token-subheader {
  font-size: 14px;
  font-weight: 600;
  padding: 5px 0;
  border-bottom: 1px solid #ccc;
  background: #fafafa;
  display: flex; align-items: center; justify-content: space-between; gap: 6px;
}
.token-subheader img {
  width: 16px; height: 16px; border-radius: 50%;
  vertical-align: middle;
}
.search-box, select.network-filter {
  margin: 5px; padding: 5px;
  border: 1px solid #ccc; border-radius: 4px;
  width: calc(100% - 20px);
}
.checkbox-small { display: flex; align-items: center; gap: 6px; font-size: 14px; }
.tabs {
  display: flex; align-items: center;
  border-bottom: 1px solid #ccc;
  top: 0; background: #fafafa; z-index: 4;
  padding: 4px 6px;
}
.tab {
  padding: 6px 14px;
  cursor: pointer;
  border-top-left-radius: 6px; border-top-right-radius: 6px;
  background: #f2f2f2;
  margin-right: 4px;
  font-size: 16px; font-weight: 600;
  transition: all 0.2s;
}
.tab:hover { background: #e3f5e3; }
.tab.active { background: #d9f0d9; font-weight: bold; }
#tabs-summary {
  margin-left: auto;
  font-weight: bold;
  font-size: 16px;
  color: #2d7a2d;
  padding-right: 10px;
}
.chain-logo, .token-logo, .protocol-logo {
  width: 16px; height: 16px; border-radius: 50%;
}
.no-wallets {
  text-align: center;
  color: #888;
  margin-top: 20px;
  font-style: italic;
}
.token-list { padding: 6px; display:flex; flex-direction:column; gap:6px; }
.wallet-address {
  cursor: pointer;
  user-select: text;
  position: relative;
}
.wallet-address:hover {
  text-decoration: underline;
}
.copy-hint {
  font-size: 12px;
  color: #4caf50;
  margin-left: 4px;
  opacity: 0.9;
  transition: opacity 0.4s;
}
</style>
</head>
<body>
<div id="topbar">
  <label class="checkbox-small">
    <input type="checkbox" id="hideSmallGlobal"> Hide balances < $1
  </label>
</div>
<div class="main">
  <div id="wallets" class="column"></div>
  <div id="div1" class="divider"></div>
  <div id="chains" class="column"></div>
  <div id="div2" class="divider"></div>
  <div id="tokens" class="column"></div>
</div>

<script>
""" + \
        "const data = " + json.dumps(all_data, ensure_ascii=False) + ";\n" + \
        "const walletOrder = " + json.dumps(wallet_order, ensure_ascii=False) + ";\n" + \
        "let totalBalance = " + str(round(total_balance, 2)) + ";\n" + \
        """
let selectedWallet = localStorage.getItem("selectedWallet") || null;
let selectedChain = localStorage.getItem("selectedChain") || null;
let selectedNetworkFilter = localStorage.getItem("selectedNetworkFilter") || "all";
let hideSmallBalances = localStorage.getItem("hideSmallBalances") === "true";
let activeTab = localStorage.getItem("activeTab") || "tabTokens";
let tokenFilterValue = localStorage.getItem("tokenFilterValue") || "";
let walletFilterValue = localStorage.getItem("walletFilterValue") || "";

// Инициализация состояния UI
document.getElementById("hideSmallGlobal").checked = hideSmallBalances;
document.getElementById("hideSmallGlobal").addEventListener("change", function() {
  hideSmallBalances = this.checked;
  localStorage.setItem("hideSmallBalances", hideSmallBalances);
  if (selectedWallet) { selectWallet(selectedWallet); if (selectedChain) renderTabs(); }
});

function initDividers() {
  const pairs = [
    { div: document.getElementById("div1"), left: document.getElementById("wallets"), key: "width_wallets" },
    { div: document.getElementById("div2"), left: document.getElementById("chains"), key: "width_chains" }
  ];
  pairs.forEach(({ div, left, key }) => {
    const saved = localStorage.getItem(key);
    if (saved) left.style.width = saved;
    let startX = 0, startW = 0;
    function onMove(e) {
      const dx = e.clientX - startX;
      const newW = startW + dx;
      if (newW > 120) left.style.width = newW + "px";
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      localStorage.setItem(key, left.style.width);
    }
    div.addEventListener("mousedown", e => {
      startX = e.clientX; startW = left.offsetWidth;
      document.body.style.cursor = "col-resize";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
      e.preventDefault();
    });
  });
}

// Рендер списка кошельков (использует walletOrder, отсортированный в Python)
function renderWallets(filter = "") {
  const el = document.getElementById("wallets");

  // 🔹 Сохраняем текущий фильтр
  if (!filter && walletFilterValue) filter = walletFilterValue;
  walletFilterValue = filter;
  localStorage.setItem("walletFilterValue", walletFilterValue);

  const q = (tokenFilterValue || "").toLowerCase();
  const networkFilter =
    selectedNetworkFilter && selectedNetworkFilter !== "all"
      ? selectedNetworkFilter
      : null;

  // 🔹 Фильтрация списка кошельков
  let walletsList = walletOrder
    .map((a) => data[a])
    .filter((w) =>
      (w.address || "").toLowerCase().includes(filter.toLowerCase())
    )
    // 🔹 Фильтр по сети
    .filter((w) => {
      if (!networkFilter) return true;
      const chains = Object.keys(w.chains || {});
      return chains.includes(networkFilter);
    })
    // 🔹 Фильтр по токену
    .filter((w) => {
      if (!q) return true;
      return Object.values(w.chains || {}).some((chain) => {
        const tokens = Object.entries(chain.tokens || {});
        const defi = Object.values(chain.defi || {});

        const matchToken = tokens.some(([t, tdata]) => {
          const keyL = (t || "").toLowerCase();
          const sym = (tdata.symbol || "").toLowerCase();
          const tick = (tdata.ticker || "").toLowerCase();
          return keyL.includes(q) || sym.includes(q) || tick.includes(q);
        });

        const matchDefi = defi.some((p) =>
          (p.positions || []).some((pos) => {
            const tick = (pos.ticker || pos.symbol || "").toLowerCase();
            return tick.includes(q);
          })
        );

        return matchToken || matchDefi;
      });
    });

  // 💰 Пересчёт totalBalance по отфильтрованным кошелькам
  let filteredTotal = 0;
  walletsList.forEach((w) => {
    const walletChains = Object.entries(w.chains || {});
    walletChains.forEach(([chain]) => {
      if (networkFilter && chain !== networkFilter) return;
      const sumTokens = calcTokensSumFiltered(w.address, chain, q);
      const sumProtocols = calcProtocolsSumFiltered(w.address, chain, q);
      filteredTotal += sumTokens + sumProtocols;
    });
  });

  el.innerHTML = `<h2>Wallets <span class='money'>$${filteredTotal.toFixed(
    2
  )}</span></h2>
  <input id='walletFilter' class='search-box' type='text' placeholder='Search wallet...' value='${walletFilterValue}'>`;

  const input = el.querySelector("#walletFilter");
  input.addEventListener("input", (e) => {
    walletFilterValue = e.target.value;
    localStorage.setItem("walletFilterValue", walletFilterValue);
    renderWallets(walletFilterValue);
  });

  if (walletsList.length === 0) {
    const emptyMsg = document.createElement("div");
    emptyMsg.className = "no-wallets";
    emptyMsg.textContent = "No wallets found";
    el.appendChild(emptyMsg);
    return;
  }

  walletsList.forEach((w) => {
    const div = document.createElement("div");
    div.className = "item";
    if (w.address === selectedWallet) div.classList.add("selected");

    // 💵 сумма по кошельку
    let walletSum = 0;
    Object.entries(w.chains || {}).forEach(([chain]) => {
      if (networkFilter && chain !== networkFilter) return;
      walletSum +=
        calcTokensSumFiltered(w.address, chain, q) +
        calcProtocolsSumFiltered(w.address, chain, q);
    });

    if (hideSmallBalances && walletSum < 1) return;

    // 💎 структура кошелька
    div.innerHTML = `
      <span class="wallet-address">${w.address}</span>
      <span class='money'>$${walletSum.toFixed(2)}</span>
    `;

    const addrEl = div.querySelector(".wallet-address");

    // 🖱️ Одинарный клик — выбор кошелька
    div.addEventListener("click", () => {
      const prevChain = selectedChain;
      selectedWallet = w.address;
      localStorage.setItem("selectedWallet", selectedWallet);
      const walletChains = Object.keys(w.chains || {});

      if (selectedNetworkFilter && selectedNetworkFilter !== "all") {
        if (walletChains.includes(selectedNetworkFilter)) {
          selectedChain = selectedNetworkFilter;
          localStorage.setItem("selectedChain", selectedChain);
        } else {
          selectedChain = null;
          localStorage.removeItem("selectedChain");
        }
      } else if (prevChain && walletChains.includes(prevChain)) {
        selectedChain = prevChain;
        localStorage.setItem("selectedChain", selectedChain);
      } else {
        selectedChain = walletChains.length > 0 ? walletChains[0] : null;
        if (selectedChain) localStorage.setItem("selectedChain", selectedChain);
      }

      renderWallets(filter);
      selectWallet(selectedWallet);

      if (selectedChain) {
        if (activeTab === "tabProtocols") {
          renderProtocols(selectedWallet, selectedChain);
          setActiveTab("tabProtocols");
        } else {
          renderTokens(selectedWallet, selectedChain);
          setActiveTab("tabTokens");
        }
      }

      applyTokenFilterImmediately();
    });

    // ✨ Двойной клик — копирование адреса
    addrEl.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      if (navigator.clipboard) {
        navigator.clipboard
          .writeText(w.address)
          .then(() => {
            const hint = document.createElement("span");
            hint.textContent = " ✅";
            hint.className = "copy-hint";
            addrEl.appendChild(hint);
            setTimeout(() => hint.remove(), 1000);
          })
          .catch((err) => console.error("Clipboard copy failed:", err));
      }
    });

    el.appendChild(div);
  });
}

// Построение списка сетей для выбранного кошелька (учитывает фильтр токенов)
function selectWallet(address) {
  const w = data[address];
  const el = document.getElementById("chains");
  el.innerHTML = "";

  if (!w) return;

  const walletChains = Object.entries(w.chains || {});
  const q = (tokenFilterValue || "").toLowerCase();

  // 🔹 Фильтрация сетей с учётом фильтра по сети и токену
  let filteredChains = walletChains;

  // 🟢 если выбран фильтр по сети — показываем только её
  if (selectedNetworkFilter && selectedNetworkFilter !== "all") {
    filteredChains = walletChains.filter(([chain]) => chain === selectedNetworkFilter);
  }
  // 🟢 иначе применяем токенный фильтр (если есть)
  else if (tokenFilterValue) {
    const q = (tokenFilterValue || "").toLowerCase();
    filteredChains = walletChains.filter(([chain, cdata]) => {
      const tokens = Object.entries(cdata.tokens || {});
      const defi = Object.values(cdata.defi || {});
      const matchToken = tokens.some(([t, tdata]) => {
        if (hideSmallBalances && (tdata.usd || 0) < 1) return false;
        const keyL = (t || "").toLowerCase();
        const sym = (tdata.symbol || "").toLowerCase();
        const tick = (tdata.ticker || "").toLowerCase();
        return keyL.includes(q) || sym.includes(q) || tick.includes(q);
      });
      const matchDefi = defi.some(p => {
        if (hideSmallBalances && (p.total_usd || 0) < 1) return false;
        return (p.positions || []).some(pos => {
          const tick = (pos.ticker || pos.symbol || "").toLowerCase();
          return tick.includes(q);
        });
      });
      return matchToken || matchDefi;
    });
  }

  // 🔹 Пересчёт totalSum по фильтру
  let totalSum = 0;
  filteredChains.forEach(([_, cdata]) => {
    const sumTokens = calcTokensSumFiltered(address, cdata.name || _, q);
    const sumProtocols = calcProtocolsSumFiltered(address, cdata.name || _, q);
    totalSum += sumTokens + sumProtocols;
  });

  el.innerHTML = `<h2>Chains <span class='money'>$${totalSum.toFixed(2)}</span></h2>
    <select id='networkFilter' class='network-filter'>
      <option value='all'>All chains</option>
      ${filteredChains
        .map(([c, _]) => `<option value='${c}' ${c === selectedNetworkFilter ? "selected" : ""}>${c}</option>`)
        .join("")}
    </select>`;

  const netSel = el.querySelector("#networkFilter");
  netSel.addEventListener("change", () => {
    selectedNetworkFilter = netSel.value;
    localStorage.setItem("selectedNetworkFilter", selectedNetworkFilter);

    if (selectedNetworkFilter !== "all") {
      // ✅ Показываем только выбранную сеть
      selectedChain = selectedNetworkFilter;
      localStorage.setItem("selectedChain", selectedChain);

      // 🔹 Список сетей теперь содержит только выбранную
      const w = data[selectedWallet];
      const cdata = w?.chains?.[selectedNetworkFilter];
      if (cdata) {
        const el = document.getElementById("chains");
        el.innerHTML = `
          <h2>Chains <span class='money'>$${(
            calcTokensSumFiltered(selectedWallet, selectedChain, tokenFilterValue) +
            calcProtocolsSumFiltered(selectedWallet, selectedChain, tokenFilterValue)
          ).toFixed(2)}</span></h2>
          <select id='networkFilter' class='network-filter'>
            <option value='${selectedNetworkFilter}' selected>${selectedNetworkFilter}</option>
            <option value='all'>All chains</option>
          </select>
        `;

        // повторное подключение обработчика — чтобы можно было вернуться к “All chains”
        el.querySelector("#networkFilter").addEventListener("change", e => {
          selectedNetworkFilter = e.target.value;
          localStorage.setItem("selectedNetworkFilter", selectedNetworkFilter);
          selectWallet(selectedWallet);
          renderWallets();
          applyTokenFilterImmediately();
        });

        // 🔹 отображаем только выбранную сеть
        const div = document.createElement("div");
        div.className = "item selected";
        div.innerHTML = `
          <img class='chain-logo' src='${cdata.logo_url || ""}'>
          ${selectedNetworkFilter}
          <span class='money'>$${(
            calcTokensSumFiltered(selectedWallet, selectedChain, tokenFilterValue) +
            calcProtocolsSumFiltered(selectedWallet, selectedChain, tokenFilterValue)
          ).toFixed(2)}</span>
        `;
        el.appendChild(div);
      }
    } else {
      // 🔙 Вернулись к “All chains” — перерисовать список полностью
      selectWallet(selectedWallet);
    }

    // 🔄 Обновляем кошельки и вкладки
    renderWallets();
    applyTokenFilterImmediately();
  });

  // 🔹 Перечисляем сети
  filteredChains.forEach(([chain, cdata]) => {
    const sumTokens = calcTokensSumFiltered(address, chain, q);
    const sumProtocols = calcProtocolsSumFiltered(address, chain, q);
    const chainSum = sumTokens + sumProtocols;

    if (hideSmallBalances && chainSum < 1) return;

    const div = document.createElement("div");
    div.className = "item";
    if (chain === selectedChain) div.classList.add("selected");
    div.innerHTML = `
      <img class='chain-logo' src='${cdata.logo_url || ""}'>
      ${chain}
      <span class='money'>$${chainSum.toFixed(2)}</span>
    `;

    div.addEventListener("click", () => {
      document.querySelectorAll("#chains .item").forEach(i => i.classList.remove("selected"));
      div.classList.add("selected");
      selectedChain = chain;
      localStorage.setItem("selectedChain", selectedChain);

      if (activeTab === "tabProtocols") {
        renderProtocols(selectedWallet, selectedChain);
        setActiveTab("tabProtocols");
      } else {
        renderTokens(selectedWallet, selectedChain);
        setActiveTab("tabTokens");
      }
    });

    el.appendChild(div);
  });
}

// Вкладки располагаются в колонке tokens (над списком токенов/протоколов)
function renderTabs() {
  const tokensColumn = document.getElementById("tokens");
  let tabsEl = tokensColumn.querySelector(".tabs");
  if (!tabsEl) {
    tabsEl = document.createElement("div");
    tabsEl.className = "tabs";
    tabsEl.innerHTML = `
      <div id='tabTokens' class='tab'>Tokens (0)</div>
      <div id='tabProtocols' class='tab'>Protocols (0)</div>
      <div id='tabs-summary'>Total: $0.00</div>
    `;

    tabsEl.querySelector("#tabTokens").addEventListener("click", () => {
      setActiveTab("tabTokens");
      renderTokens();
    });
    tabsEl.querySelector("#tabProtocols").addEventListener("click", () => {
      setActiveTab("tabProtocols");
      renderProtocols();
    });

    tokensColumn.prepend(tabsEl);
  }

  // Контейнер контента вкладок
  let contentEl = document.getElementById("tabContent");
  if (!contentEl) {
    contentEl = document.createElement("div");
    contentEl.id = "tabContent";
    contentEl.style.flex = "1";
    tokensColumn.appendChild(contentEl);
  }

  // ⚙️ Рендерим только активную вкладку
  if (activeTab === "tabProtocols") {
    setActiveTab("tabProtocols");
    renderProtocols();
  } else {
    setActiveTab("tabTokens");
    renderTokens();
  }
}

function setActiveTab(id) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  const el = document.getElementById(id);
  if (el) el.classList.add("active");
  activeTab = id;
  localStorage.setItem("activeTab", id);
}

function updateTotalDisplay(sumTokens, sumProtocols) {
  const total = (sumTokens + sumProtocols).toFixed(2);
  const el = document.getElementById("tabs-summary");
  if (el) el.textContent = `Total: $${total}`;
}

// Рендер токенов для выбранного кошелька и сети
function renderTokens(address, chain) {
  address = address || selectedWallet;
  chain = chain || selectedChain;
  const el = document.getElementById("tabContent");
  el.innerHTML = "";

  if (!address || !chain) {
    el.innerHTML = "<div class='no-wallets'>Select wallet and chain</div>";
    const tabT = document.getElementById("tabTokens");
    if (tabT) tabT.textContent = "Tokens (0)";
    updateTotalDisplay(0, 0);
    return;
  }

  const cdata = (data[address].chains || {})[chain] || { tokens: {}, defi: {}, total: 0 };
  const tokens = Object.entries(cdata.tokens || {}).sort(
    (a, b) => (b[1].usd || 0) - (a[1].usd || 0)
  );

  const header = document.createElement("div");
  header.className = "token-subheader";
  const logo = cdata.logo_url ? `<img src='${cdata.logo_url}'>` : "";
  header.innerHTML = `<div>${logo} ${chain} tokens</div><span class='money'>$0.00</span>`;
  el.appendChild(header);

  const input = document.createElement("input");
  input.type = "text";
  input.className = "search-box";
  input.placeholder = "Filter tokens...";
  input.value = tokenFilterValue;
  el.appendChild(input);

  const list = document.createElement("div");
  list.className = "token-list";
  el.appendChild(list);

  function update() {
    list.innerHTML = "";
    const q = (input.value || "").toLowerCase();
    let visible = 0;
    let sum = 0;

    tokens.forEach(([token, tdata]) => {
      if (hideSmallBalances && (tdata.usd || 0) < 1) return;
      if (!q || token.toLowerCase().includes(q)) {
        const div = document.createElement("div");
        div.className = "item";
        div.innerHTML = `<img class='token-logo' src='${tdata.logo_url||""}'> ${tdata.amount} ${token} <span class='money'>$${(tdata.usd||0).toFixed(2)}</span>`;
        list.appendChild(div);
        visible++;
        sum += tdata.usd || 0;
      }
    });

    const tabT = document.getElementById("tabTokens");
    if (tabT) tabT.textContent = `Tokens (${visible})`;
    header.querySelector(".money").textContent = `$${sum.toFixed(2)}`;
    updateTotalDisplay(sum, calcProtocolsSumFiltered(address, chain, q));

    // 🔄 обновляем заголовок второй вкладки (Protocols)
    const sumProtocols = calcProtocolsSumFiltered(address, chain, q);
    const protocolsCount = countFilteredProtocols(address, chain, q);
    document.getElementById("tabProtocols").textContent = `Protocols (${protocolsCount})`;

    tokenFilterValue = input.value;
    localStorage.setItem("tokenFilterValue", tokenFilterValue);
  }

  input.addEventListener("input", e => {
    tokenFilterValue = e.target.value;
    localStorage.setItem("tokenFilterValue", tokenFilterValue);
    update();

    if (tokenFilterValue.trim() !== "") {
      // При активном фильтре — обновляем сети
      selectWallet(address);
      renderWallets();
    } else {
      // 🔄 При очистке фильтра — возвращаем полный список сетей
      tokenFilterValue = "";
      localStorage.removeItem("tokenFilterValue");
      selectWallet(address);
      renderWallets();
    }
  });

  update();

  let tokensCache = document.getElementById("tokensContent");
  if (!tokensCache) {
    tokensCache = document.createElement("div");
    tokensCache.id = "tokensContent";
    tokensCache.style.display = "none";
    document.body.appendChild(tokensCache);
  }
  tokensCache.innerHTML = el.innerHTML;
}

// Рендер протоколов для выбранного кошелька и сети
function renderProtocols(address, chain) {
  address = address || selectedWallet;
  chain = chain || selectedChain;
  const el = document.getElementById("tabContent");
  el.innerHTML = "";

  if (!address || !chain) {
    el.innerHTML = "<div class='no-wallets'>Select wallet and chain</div>";
    const tabP = document.getElementById("tabProtocols");
    if (tabP) tabP.textContent = "Protocols (0)";
    updateTotalDisplay(0, 0);
    return;
  }

  const cdata = (data[address].chains || {})[chain] || { tokens: {}, defi: {}, total: 0 };
  const protocols = Object.entries(cdata.defi || {}).sort(
    (a, b) => (b[1].total_usd || 0) - (a[1].total_usd || 0)
  );

  const header = document.createElement("div");
  header.className = "token-subheader";
  const logo = cdata.logo_url ? `<img src='${cdata.logo_url}'>` : "";
  header.innerHTML = `<div>${logo} ${chain} protocols</div><span class='money'>$0.00</span>`;
  el.appendChild(header);

  const input = document.createElement("input");
  input.type = "text";
  input.className = "search-box";
  input.placeholder = "Filter tokens...";
  input.value = tokenFilterValue;
  el.appendChild(input);

  const list = document.createElement("div");
  list.className = "token-list";
  el.appendChild(list);

  function update() {
    list.innerHTML = "";
    const q = (input.value || "").toLowerCase();
    let visible = 0;
    let sum = 0;

    protocols.forEach(([name, pdata]) => {
      if (hideSmallBalances && (pdata.total_usd || 0) < 1) return;

      const filteredPositions = (pdata.positions || []).filter(pos => {
        const ticker = (pos.ticker || pos.symbol || "").toLowerCase();
        return !q || ticker.includes(q);
      });

      if (filteredPositions.length === 0) return;

      // 💰 Считаем сумму только по отфильтрованным позициям
      let localSum = 0;
      filteredPositions.forEach(pos => {
        const usd = pos.usd || pos.usd_value || 0;
        localSum += usd || 0;
      });

      const div = document.createElement("div");
      div.className = "item";
      div.style.flexDirection = "column";
      div.style.alignItems = "stretch";

      // 🔄 Заголовок протокола с суммой по фильтру
      const headerRow = document.createElement("div");
      headerRow.style.display = "flex";
      headerRow.style.alignItems = "center";
      headerRow.innerHTML = `
        <img class='protocol-logo' src='${pdata.logo_url || ""}'>
        <span style="flex:1">${name}</span>
        <span class='money'>$${localSum.toFixed(2)}</span>
      `;

      // 📊 Детали позиций
      const details = document.createElement("div");
      details.style.display = "block";
      details.style.paddingLeft = "24px";
      details.style.borderTop = "1px dashed #ccc";
      details.style.marginTop = "4px";
      details.style.fontSize = "14px";
      details.style.fontStyle = "italic";

      filteredPositions.forEach(pos => {
        const ticker = pos.ticker || pos.symbol || "";
        const amount = pos.amount || pos.balance || 0;
        const usd = pos.usd || pos.usd_value || 0;

        const row = document.createElement("div");
        row.style.display = "flex";
        row.style.justifyContent = "space-between";
        row.style.padding = "2px 0";
        row.innerHTML = `<span>${amount} ${ticker}</span><span class="money" style="font-size:14px;">$${(usd || 0).toFixed(2)}</span>`;
        details.appendChild(row);
      });

      div.appendChild(headerRow);
      div.appendChild(details);
      list.appendChild(div);

      visible++;
      sum += localSum;
    });

    // 🔢 Обновляем счётчики и итоговую сумму
    const tabP = document.getElementById("tabProtocols");
    if (tabP) tabP.textContent = `Protocols (${visible})`;
    header.querySelector(".money").textContent = `$${sum.toFixed(2)}`;

    // ✅ Итоговая сумма Total
    const sumTokens = calcTokensSumFiltered(address, chain, q);
    updateTotalDisplay(sumTokens, sum);

    // 🔄 обновляем вкладку Tokens
    const tokensCount = countFilteredTokens(address, chain, q);
    document.getElementById("tabTokens").textContent = `Tokens (${tokensCount})`;

    tokenFilterValue = input.value;
    localStorage.setItem("tokenFilterValue", tokenFilterValue);
  }

  input.addEventListener("input", e => {
    tokenFilterValue = e.target.value;
    localStorage.setItem("tokenFilterValue", tokenFilterValue);
    update();

    if (tokenFilterValue.trim() !== "") {
      selectWallet(address); // фильтр активен
      renderWallets();
    } else {
      // 🔄 фильтр очищен — вернуть все сети
      tokenFilterValue = "";
      localStorage.removeItem("tokenFilterValue");
      selectWallet(address);
      renderWallets();
    }
  });

  update();

  // 🟢 при первичном открытии вкладки сразу считаем фильтрованный Total
  const q = (input.value || "").toLowerCase();
  const sumTokens = calcTokensSumFiltered(address, chain, q);
  updateTotalDisplay(sumTokens, sum);

  // Кешируем состояние вкладки (для переключений)
  let protocolsCache = document.getElementById("protocolsContent");
  if (!protocolsCache) {
    protocolsCache = document.createElement("div");
    protocolsCache.id = "protocolsContent";
    protocolsCache.style.display = "none";
    document.body.appendChild(protocolsCache);
  }
  protocolsCache.innerHTML = el.innerHTML;
}

// 🟢 Мгновенное применение фильтра после смены сети или кошелька
function applyTokenFilterImmediately() {
  if (!selectedWallet || !selectedChain) return;

  if (activeTab === "tabProtocols") {
    renderProtocols(selectedWallet, selectedChain);
    setActiveTab("tabProtocols");
  } else {
    renderTokens(selectedWallet, selectedChain);
    setActiveTab("tabTokens");
  }
}

// Универсальная функция обновления вкладок при смене кошелька/сети/фильтра
function refreshTabs() {
  if (!selectedWallet || !selectedChain) return;
  // Обновляем обе вкладки
  renderTokens(selectedWallet, selectedChain);
  renderProtocols(selectedWallet, selectedChain);
  // Отрисовываем активную вкладку актуальными данными
  if (activeTab === "tabProtocols") {
    setActiveTab("tabProtocols");
    renderProtocols(selectedWallet, selectedChain);
  } else {
    setActiveTab("tabTokens");
    renderTokens(selectedWallet, selectedChain);
  }
}

// Хелперы для сумм с учётом фильтра
function calcTokensSumFiltered(address, chain, filter) {
  const cdata = (data[address].chains || {})[chain] || { tokens: {} };
  const q = (filter || "").toLowerCase();

  return Object.entries(cdata.tokens || {})
    .filter(([key, t]) => {
      // скрываем мелкие балансы, если опция включена
      if (hideSmallBalances && ((t.usd || 0) < 1)) return false;
      return true;
    })
    .filter(([key, t]) => {
      if (!q) return true;
      const keyL = (key || "").toLowerCase();
      const sym = (t.symbol || "").toLowerCase();
      const tick = (t.ticker || "").toLowerCase();
      return keyL.includes(q) || sym.includes(q) || tick.includes(q);
    })
    .reduce((acc, [_, t]) => acc + (t.usd || 0), 0);
}

function calcProtocolsSumFiltered(address, chain, filter) {
  const cdata = (data[address].chains || {})[chain] || { defi: {} };
  const q = (filter || "").toLowerCase();
  let total = 0;

  Object.values(cdata.defi || {}).forEach(p => {
    if (hideSmallBalances && (p.total_usd || 0) < 1) return;
    const positions = (p.positions || []).filter(pos => {
      const ticker = (pos.ticker || pos.symbol || "").toLowerCase();
      return !q || ticker.includes(q);
    });
    positions.forEach(pos => {
      total += pos.usd || pos.usd_value || 0;
    });
  });

  return total;
}

function countFilteredTokens(address, chain, filter) {
  const cdata = (data[address].chains || {})[chain] || { tokens: {} };
  return Object.entries(cdata.tokens || {})
    .filter(([name, t]) => !hideSmallBalances || (t.usd || 0) >= 1)
    .filter(([name, t]) => {
      const sym = (t.symbol || name || "").toLowerCase();
      return !filter || sym.includes(filter.toLowerCase());
    }).length;
}

function countFilteredProtocols(address, chain, filter) {
  const cdata = (data[address].chains || {})[chain] || { defi: {} };
  return Object.values(cdata.defi || {})
    .filter(p => !hideSmallBalances || (p.total_usd || 0) >= 1)
    .filter(p => {
      const positions = (p.positions || []).filter(pos => {
        const ticker = (pos.ticker || pos.symbol || "").toLowerCase();
        return !filter || ticker.includes(filter.toLowerCase());
      });
      return positions.length > 0;
    }).length;
}

// Инициализация UI
initDividers();
renderWallets();
if (!selectedWallet && walletOrder.length>0) {
  selectedWallet = walletOrder[0];
  localStorage.setItem("selectedWallet", selectedWallet);
}

if (selectedWallet) {
  selectWallet(selectedWallet);

  if (!selectedChain) {
    const w = data[selectedWallet] || {};
    const keys = w.chains ? Object.keys(w.chains) : [];
    selectedChain = keys.length > 0 ? keys[0] : null;
    if (selectedChain) localStorage.setItem("selectedChain", selectedChain);
  }

  // 🟢 Полное восстановление вкладок и данных при загрузке страницы
  renderTabs();

  // 🟢 Восстанавливаем контент активной вкладки
  const tabContent = document.getElementById("tabContent");
  if (activeTab === "tabProtocols") {
    setActiveTab("tabProtocols");
    tabContent.innerHTML = document.getElementById("protocolsContent")?.innerHTML || "";
  } else {
    setActiveTab("tabTokens");
    tabContent.innerHTML = document.getElementById("tokensContent")?.innerHTML || "";
  }

  // 🟢 Применяем фильтр
  applyTokenFilterImmediately();
}
window.addEventListener("load", () => {
    fetch("http://127.0.0.1:5000/regen", { method: "POST" })
      .catch(() => console.log("⚠️ Generate HTML skipped — no server endpoint."));
});
</script>
</body>
</html>
"""

    # Записываем HTML
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)

    # print(f"✅ HTML-файл успешно создан: {output_file}")


async def auto_generate_html(interval_sec: int = 5):
    """Фоновое обновление HTML независимо от обновления данных"""
    while True:
        try:
            generate_html()
            # print(f"🪄 [{datetime.now().strftime('%H:%M:%S')}] HTML обновлён автоматически ({OUTPUT_FILE})")
        except Exception as e:
            print(f"⚠️ Ошибка при автообновлении HTML: {e}")
        await asyncio.sleep(interval_sec)


# ======================
# MAIN
# ======================


shutdown_flag = asyncio.Event()


def handle_exit(sig, frame):
    """Ctrl+C: завершает все процессы"""
    shutdown_flag.set()
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(shutdown_all_tasks())
    except RuntimeError:
        pass


async def shutdown_all_tasks():
    """Принудительно завершает все активные asyncio-задачи"""
    print("🛑 Отмена всех задач...")
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0.1)


async def main():
    # 🔹 Загружаем адреса и прокси
    with open(ADDRESSES_FILE, "r", encoding="utf-8") as f:
        addresses = [line.strip() for line in f if line.strip()]
    with open(PROXIES_FILE, "r", encoding="utf-8") as f:
        raw_proxies = [line.strip() for line in f if line.strip()]

    proxy_rotator = ProxyRotator(raw_proxies)
    proxies_count = len(raw_proxies)

    # --- Настройка количества потоков ---
    if proxies_count <= 1:
        max_concurrent = 1
    else:
        if MAX_CONCURRENT > proxies_count:
            max_concurrent = proxies_count
        else:
            max_concurrent = MAX_CONCURRENT

    print(f"🚀 Запуск обновления данных из Debank (количество потоков: {max_concurrent})")
    semaphore = asyncio.Semaphore(max_concurrent)
    
    output_file_already_opened = False
    async with async_playwright() as p:
        browser_type = p.chromium
        
        output_path = Path(OUTPUT_FILE).resolve()
        if not output_file_already_opened and output_path.exists():
            asyncio.create_task(auto_generate_html(UPDATE_HTML_SEC))
            webbrowser.open(f"file://{output_path}")
            output_file_already_opened = True

        try:
            while not shutdown_flag.is_set():
                print(f"\n🔄 [{datetime.now().strftime('%H:%M:%S')}] Начато обновление данных...")

                tasks = [
                    asyncio.create_task(process_address(p, browser_type, addr, semaphore, proxy_rotator))
                    for addr in addresses
                ]
                
                for f in tqdm_asyncio.as_completed(tasks, desc="Обновление данных", total=len(tasks)):
                    err = await f
                    if err:
                        tqdm.write(f"⚠️ {err}")

                if not output_file_already_opened:
                    asyncio.create_task(auto_generate_html(UPDATE_HTML_SEC))
                    webbrowser.open(f"file://{output_path}")
                    output_file_already_opened = True

                print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Данные обновлёны после полного цикла")

                update_data_sec = int(UPDATE_DATA_MIN * 60)
                for remaining in range(update_data_sec, -1, -1):
                    if shutdown_flag.is_set():
                        break
                    try:
                        mins, secs = divmod(remaining, 60)
                        time_str = f"{mins:02d}:{secs:02d}"
                        print(f"\r⏳ Ожидание следующего обновления данных через: {time_str}", end="", flush=True)
                        await asyncio.sleep(1)
                    except asyncio.CancelledError:
                        break

        except asyncio.CancelledError:
            print("\n⚠️ Цикл обновления данных прерван (Ctrl+C).")

    print("\n🧹 Завершение Playwright и выход...")
    await asyncio.sleep(0.1)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Принудительное завершение программы.")
        sys.exit(0)