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
PROXIES_FILE = "data/proxies.txt"
ADDRESSES_FILE = "data/addresses.txt"
STORAGE_DIR = "storage"
CHAIN_MAP_PATH = f"{STORAGE_DIR}/CHAIN_NAME_MAP.json"


# ======================
# Парсинг Debank
# ======================


def load_chain_name_map() -> dict[str, str]:
    """Загружает или создаёт файл с маппингом chain_id -> full_name"""
    os.makedirs("storage", exist_ok=True)
    if os.path.exists(CHAIN_MAP_PATH):
        try:
            with open(CHAIN_MAP_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_chain_name_map(mapping: dict[str, str]):
    """Сохраняет маппинг chain_id -> full_name"""
    os.makedirs("storage", exist_ok=True)
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

                    os.makedirs(STORAGE_DIR, exist_ok=True)
                    with open(os.path.join(STORAGE_DIR, f"{address}.json"), "w", encoding="utf-8") as f:
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
                    os.makedirs(STORAGE_DIR, exist_ok=True)
                    with open(os.path.join(STORAGE_DIR, f"{address}.json"), "w", encoding="utf-8") as f:
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


def generate_html(storage_dir: str = "storage", output_file: str = "results.html"):
    template_path = Path("templates/report_template.html")
    
    # читаем шаблон
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
    
    all_data = {}
    total_balance = 0.0

    # Загрузка json'ов
    for fname in os.listdir(storage_dir):
        if fname.endswith(".json") and fname != "CHAIN_NAME_MAP.json":
            path = os.path.join(storage_dir, fname)
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

    chain_name_map = load_chain_name_map()

    # Получаем список сетей по полным именам, сортируем по алфавиту
    chain_names = sorted(set(chain_name_map.values()))

    # подставляем данные
    html = (
        template
        .replace("{{CHAIN_LIST}}", json.dumps(chain_names, ensure_ascii=False))
        .replace("{{DATA_JSON}}", json.dumps(all_data, ensure_ascii=False))
        .replace("{{WALLET_ORDER}}", json.dumps(wallet_order, ensure_ascii=False))
        .replace("{{TOTAL_BALANCE}}", str(round(total_balance, 2)))
    )

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