#!/usr/bin/env python3
"""
LeBonCoin Rental Property Analyzer — v2
─────────────────────────────────────────────────────────────────────────────
Champs extraits / calculés :
  Annonce     : type, surface, prix, loyer HC, taxe foncière, charges,
                nb lots, ville, CP, DPE, date, likes, lien
  Calculs     : prix/m², frais notaire, coût total, renta brute, renta nette
  Externes    : prix moyen/m² (meilleursagents.com), tension locative (locservice.fr)
─────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import os
import re
import csv
import json
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

try:
    from unidecode import unidecode
except ImportError:
    def unidecode(s): return s

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
EMAIL      = os.getenv("LBC_EMAIL")
PASSWORD   = os.getenv("LBC_PASSWORD")
MIN_YIELD  = float(os.getenv("MIN_YIELD", "10.0"))
MAX_PRICE  = int(os.getenv("MAX_PRICE", "150000"))
HEADLESS   = os.getenv("HEADLESS", "false").lower() == "true"
WA_PHONE   = os.getenv("WA_PHONE", "")
WA_APIKEY  = os.getenv("WA_APIKEY", "")

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
SEEN_FILE  = OUTPUT_DIR / "seen_urls.json"

# Cache externe (évite de refetcher la même ville)
_ma_cache: dict = {}   # "city-cp" -> avg_price_sqm (int|None)
_ls_cache: dict = {}   # "cp"      -> tension (str|None)

# ── Regex patterns ────────────────────────────────────────────────────────────

RENT_PATTERNS = [
    r'loyer\s+(?:actuel\s+)?(?:mensuel\s+)?(?:hors\s+charges?\s+)?(?:de\s+)?(\d[\d\s\u00a0]{1,6})\s*€',
    r'(\d[\d\s\u00a0]{1,6})\s*€\s*/?\s*(?:par\s+)?mois',
    r'(\d[\d\s\u00a0]{1,6})\s*€\s*(?:de\s+)?loyer',
    r'loyer\s*[:\-]\s*(\d[\d\s\u00a0]{1,6})',
    r'(\d[\d\s\u00a0]{1,6})\s*euros?\s*/?\s*mois',
    r'(\d[\d\s\u00a0]{1,6})\s*€\s*hc',
]
CHARGES_PATTERNS = [
    r'charges?\s*(?:mensuelles?\s+)?(?:de\s+copropri[eé]t[eé]\s+)?[:\-]?\s*(\d[\d\s\u00a0]{1,5})\s*€',
    r'(\d[\d\s\u00a0]{1,5})\s*€\s*de\s+charges?',
]
TF_PATTERNS = [
    r'taxe\s+fonci[eè]re?\s*[:\-]?\s*(\d[\d\s\u00a0]{1,6})\s*€',
    r'\btf\b\s*[:\-]\s*(\d[\d\s\u00a0]{1,6})\s*€',
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_number(s: str) -> int | None:
    s = re.sub(r'[\s\u00a0]', '', s)
    try:
        return int(s)
    except ValueError:
        return None

def extract_rent(text: str) -> int | None:
    for p in RENT_PATTERNS:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = clean_number(m.group(1))
            if val and 100 <= val <= 15_000:
                return val
    return None

def extract_price(text: str) -> int | None:
    m = re.search(r'(\d[\d\s\u00a0]{2,9})\s*€', text)
    if m:
        val = clean_number(m.group(1))
        if val and 5_000 <= val <= 5_000_000:
            return val
    return None

def extract_surface(text: str) -> int | None:
    m = re.search(r'(\d{1,4})\s*m[²2]', text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None

def extract_charges(text: str) -> int | None:
    for p in CHARGES_PATTERNS:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = clean_number(m.group(1))
            if val and 10 <= val <= 5_000:
                return val
    return None

def extract_taxe_fonciere(text: str) -> int | None:
    for p in TF_PATTERNS:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            val = clean_number(m.group(1))
            if val and 50 <= val <= 20_000:
                return val
    return None

def extract_nb_lots(text: str) -> str | None:
    for p in [r'(\d+)\s*lots?', r'(\d+)\s*appartements?', r'(\d+)\s*logements?',
               r'divis[eé]\s+en\s+(\d+)', r'lot\s+n[°o]?\s*\d+\s*/\s*(\d+)']:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return None

def extract_dpe(text: str) -> str | None:
    for p in [r'\bDPE\s*[:\-]?\s*([A-G])\b', r'\bclasse\s*[:\-]?\s*([A-G])\b',
               r'\b([A-G])\s*\(DPE\)', r'énergie\s*[:\-]?\s*([A-G])\b']:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None

def extract_property_type(text: str) -> str:
    t = text.lower()
    if 'immeuble' in t: return 'Immeuble'
    if 'maison' in t or 'villa' in t: return 'Maison'
    if 'studio' in t: return 'Studio'
    if 'appartement' in t or 'appt' in t: return 'Appartement'
    if 'terrain' in t: return 'Terrain'
    if 'commerce' in t or 'local' in t: return 'Local commercial'
    return 'Autre'

def extract_city_cp(location: str) -> tuple:
    if not location:
        return None, None
    m = re.search(r'(.+?)\s*\((\d{5})\)', location)
    if m: return m.group(1).strip(), m.group(2)
    m = re.search(r'(\d{5})\s+(.+)', location)
    if m: return m.group(2).strip(), m.group(1)
    m = re.search(r'(.+?)\s+(\d{5})', location)
    if m: return m.group(1).strip(), m.group(2)
    return location.strip(), None

def is_already_rented(text: str) -> bool:
    keywords = ["loué", "loue ", "actuellement loué", "occupé",
                "locataire en place", "bail en cours", "vendu loué",
                "investi", "déjà loué", "en location"]
    low = text.lower()
    return any(kw in low for kw in keywords)

def city_to_slug(city: str) -> str:
    slug = unidecode(city.lower().strip())
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    return slug.strip('-')

# ── Frais de notaire (bien ancien) ───────────────────────────────────────────

def calc_notaire_fees(price: int) -> tuple:
    """Retourne (montant_€, taux_%)."""
    droits  = price * 0.0580665
    emo     = 0.0
    tranches = [(6500, 0.03870), (10500, 0.01596), (43000, 0.01064)]
    remaining = price
    for montant, taux in tranches:
        emo += min(remaining, montant) * taux
        remaining -= montant
        if remaining <= 0: break
    if remaining > 0: emo += remaining * 0.00799
    emo_ttc = emo * 1.20
    total = droits + emo_ttc + price * 0.001 + 1000
    return round(total), round(total / price * 100, 2)

# ── Calcul rentabilité ────────────────────────────────────────────────────────

def calc_all_yields(price: int, rent_hc: int,
                    taxe_fonciere: int | None,
                    charges_monthly: int | None) -> dict:
    notaire_fees, notaire_rate = calc_notaire_fees(price)
    total_cost   = price + notaire_fees
    annual_rent  = rent_hc * 12
    gross        = round(annual_rent / price * 100, 2)
    tf           = taxe_fonciere if taxe_fonciere else round(rent_hc * 1.0)
    ch_a         = charges_monthly * 12 * 0.25 if charges_monthly else round(annual_rent * 0.10)
    vac          = round(rent_hc * 0.5)
    net          = round((annual_rent - tf - ch_a - vac) / total_cost * 100, 2)
    return {
        "gross_yield_pct":   gross,
        "net_yield_pct":     net,
        "notaire_fees":      notaire_fees,
        "notaire_fees_rate": notaire_rate,
        "total_cost":        total_cost,
    }

# ── Données externes ─────────────────────────────────────────────────────────

async def fetch_avg_price_sqm(page, city: str, postal_code: str) -> int | None:
    """Prix moyen/m² depuis meilleursagents.com."""
    key = f"{city}-{postal_code}"
    if key in _ma_cache:
        return _ma_cache[key]
    slug = city_to_slug(city)
    url  = f"https://www.meilleursagents.com/prix-immobilier/{slug}-{postal_code}/"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(2000)
        content = await page.content()
        m = re.search(r'(\d[\d\s\u00a0]{2,6})\s*€\s*/\s*m[²2]', content)
        if m:
            val = clean_number(m.group(1))
            if val and 500 <= val <= 30_000:
                _ma_cache[key] = val
                return val
        m2 = re.search(r'"price_per_sqm"\s*:\s*(\d+)', content)
        if m2:
            val = int(m2.group(1))
            _ma_cache[key] = val
            return val
    except Exception:
        pass
    _ma_cache[key] = None
    return None


async def fetch_rental_tension(page, postal_code: str) -> str | None:
    """Tension locative depuis locservice.fr."""
    if postal_code in _ls_cache:
        return _ls_cache[postal_code]
    try:
        await page.goto(
            f"https://www.locservice.fr/tensiometre/?cp={postal_code}",
            wait_until="domcontentloaded", timeout=20_000
        )
        await page.wait_for_timeout(2500)
        content = await page.content()
        for p in [r'class="[^"]*tension[^"]*"[^>]*>\s*([^<]+)',
                   r'"tension"\s*:\s*"([^"]+)"',
                   r'tension\s+(?:locative\s+)?(?:est\s+)?(\w+)']:
            m = re.search(p, content, re.IGNORECASE)
            if m:
                t = m.group(1).strip()
                if t and len(t) < 30:
                    _ls_cache[postal_code] = t
                    return t
        for sel in ['[class*="tension"]', '.tensiometre-value', '[id*="tension"]']:
            el = await page.query_selector(sel)
            if el:
                t = (await el.text_content()).strip()
                if t and len(t) < 50:
                    _ls_cache[postal_code] = t
                    return t
    except Exception:
        pass
    _ls_cache[postal_code] = None
    return None


async def enrich_external_data(page, listings: list) -> None:
    """Enrichit chaque listing avec prix marché/m² et tension locative."""
    unique_cities = {(l["city"], l["postal_code"])
                     for l in listings if l.get("city") and l.get("postal_code")}
    unique_cps    = {l["postal_code"] for l in listings if l.get("postal_code")}

    print(f"\n  → Données externes pour {len(unique_cities)} ville(s)...")
    for city, cp in unique_cities:
        print(f"    MA  {city} ({cp})...", end=" ", flush=True)
        val = await fetch_avg_price_sqm(page, city, cp)
        print(f"{val} €/m²" if val else "N/D")
    for cp in unique_cps:
        print(f"    LS  {cp}...", end=" ", flush=True)
        t = await fetch_rental_tension(page, cp)
        print(t or "N/D")

    for l in listings:
        if l.get("city") and l.get("postal_code"):
            key = f"{l['city']}-{l['postal_code']}"
            l["avg_price_sqm"]  = _ma_cache.get(key)
            l["rental_tension"] = _ls_cache.get(l["postal_code"])


# ── WhatsApp notification ──────────────────────────────────────────────────────

def send_whatsapp(message: str):
    """Send a WhatsApp message via CallMeBot (free).
    Registration: https://www.callmebot.com/blog/free-api-whatsapp-messages/
    """
    if not WA_PHONE or not WA_APIKEY:
        return
    try:
        encoded = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={WA_PHONE}&text={encoded}&apikey={WA_APIKEY}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            print(f"  📱 WhatsApp envoyé ({resp.status})")
    except Exception as e:
        print(f"  ⚠ WhatsApp erreur: {e}")

def load_seen_urls() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen_urls(urls: set):
    SEEN_FILE.write_text(json.dumps(list(urls)))

# ── Browser helpers ────────────────────────────────────────────────────────────

async def accept_cookies(page):
    for sel in [
        'button[data-testid="didomi-notice-agree-button"]',
        '#didomi-notice-agree-button',
        'button:has-text("Tout accepter")',
        'button:has-text("Accepter")',
    ]:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await page.wait_for_timeout(800)
                return
        except Exception:
            continue

def _is_logged_in(url: str) -> bool:
    return "login" not in url and "connexion" not in url and "compte" not in url.split("?")[0].rstrip("/").split("/")[-1]


async def login(page) -> bool:
    print("  → Vérification de la session LeBonCoin...")
    await page.goto("https://www.leboncoin.fr/mes-favoris/recherches", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    await accept_cookies(page)

    # Already logged in via saved profile?
    if "mes-favoris" in page.url:
        print("  ✓ Session existante détectée (profil persistant)")
        return True

    # Try automatic login
    print("  → Tentative de connexion automatique...")
    await page.goto("https://www.leboncoin.fr/compte/login", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    await accept_cookies(page)

    email_sel = 'input[name="st_username"], input[type="email"], input[id*="email"]'
    pwd_sel   = 'input[name="st_passwd"],  input[type="password"], input[id*="password"]'

    try:
        await page.fill(email_sel, EMAIL)
        await page.wait_for_timeout(500)
        await page.fill(pwd_sel, PASSWORD)
        await page.wait_for_timeout(500)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(4000)
    except Exception as e:
        print(f"  ✗ Formulaire introuvable: {e}")

    if "login" not in page.url and "connexion" not in page.url:
        print("  ✓ Connecté automatiquement")
        return True

    # Fallback: manual login
    print()
    print("  ┌─────────────────────────────────────────────────────────┐")
    print("  │  CONNEXION MANUELLE REQUISE                             │")
    print("  │  LeBonCoin a bloqué la connexion automatique.           │")
    print("  │  Connectez-vous manuellement dans le navigateur.        │")
    print("  │  Vous avez 120 secondes.                                │")
    print("  └─────────────────────────────────────────────────────────┘")
    print()

    await page.goto("https://www.leboncoin.fr/compte/login", wait_until="domcontentloaded")
    await accept_cookies(page)

    try:
        await page.wait_for_url("**/mes-favoris/**", timeout=120_000)
        print("  ✓ Connexion manuelle détectée")
        return True
    except Exception:
        pass

    # Check current URL one more time
    if "mes-favoris" in page.url or ("login" not in page.url and "connexion" not in page.url):
        print("  ✓ Connecté")
        return True

    await page.screenshot(path="output/debug_login.png")
    print(f"  ✗ Connexion échouée (URL: {page.url})")
    return False


async def get_saved_searches(page) -> list[dict]:
    print("\n  → Récupération des recherches sauvegardées...")
    await page.goto("https://www.leboncoin.fr/mes-favoris/recherches", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    searches = []
    seen_urls = set()

    # Multiple selector strategies
    for sel in ['[data-qa-id="search_card"]', 'article', 'li[class*="Card"]', 'li[class*="card"]', 'div[class*="Card"]']:
        cards = await page.query_selector_all(sel)
        if cards:
            for card in cards:
                try:
                    link = await card.query_selector(
                        'a[href*="recherche"], a[href*="search"], a[href*="ventes"], a[href*="immobilier"], a'
                    )
                    href = await link.get_attribute('href') if link else await card.get_attribute('href')
                    if not href:
                        continue
                    full_url = f"https://www.leboncoin.fr{href}" if href.startswith('/') else href
                    if full_url in seen_urls or "leboncoin.fr" not in full_url:
                        continue
                    seen_urls.add(full_url)
                    title_el = await card.query_selector('h2, h3, [class*="title"], [class*="Title"], strong')
                    title = (await title_el.text_content()).strip() if title_el else f"Recherche {len(searches)+1}"
                    searches.append({"title": title, "url": full_url})
                except Exception:
                    continue
            if searches:
                break

    # Fallback: parse page source for search links
    if not searches:
        content = await page.content()
        hrefs = re.findall(r'href="(/(?:recherche|mes-favoris)[^"]+)"', content)
        for href in dict.fromkeys(hrefs):  # preserve order, deduplicate
            full_url = f"https://www.leboncoin.fr{href}"
            if full_url not in seen_urls:
                seen_urls.add(full_url)
                searches.append({"title": f"Recherche {len(searches)+1}", "url": full_url})

    print(f"  ✓ {len(searches)} recherche(s)")
    for s in searches:
        print(f"    • {s['title']}")
    return searches


async def get_listing_stubs(page) -> list[dict]:
    """Extract URL + card text from a search results page."""
    await page.wait_for_timeout(1800)
    results = []
    seen = set()

    links = await page.query_selector_all('a[href*="/ventes_immobilieres/"]')
    for link in links:
        try:
            href = await link.get_attribute('href')
            if not href or href in seen:
                continue
            seen.add(href)
            full_url = f"https://www.leboncoin.fr{href}" if href.startswith('/') else href

            parent = await link.evaluate_handle(
                'el => el.closest("li") || el.closest("article") || el.parentElement'
            )
            try:
                card_text = await parent.evaluate('el => el.innerText')
            except Exception:
                card_text = await link.text_content() or ""

            results.append({"url": full_url, "card_text": card_text.strip()})
        except Exception:
            continue

    return results


async def scrape_listing_detail(page, url: str) -> dict | None:
    """
    Visite une annonce et extrait tous les champs.
    Retourne None si prix > MAX_PRICE ou si le loyer est absent.
    """
    data: dict = {
        # Annonce
        "url": url, "title": None, "property_type": None, "surface": None,
        "price": None, "price_per_sqm": None,
        "rent_hc": None,
        # Charges & fiscalité
        "charges_monthly": None, "charges_annual": None, "taxe_fonciere": None,
        # Calculs
        "notaire_fees": None, "notaire_fees_rate": None,
        "total_cost": None, "gross_yield_pct": None, "net_yield_pct": None,
        # Localisation
        "city": None, "postal_code": None, "location": None,
        # Bien
        "nb_lots": None, "dpe": None,
        # Meta annonce
        "listing_date": None, "nb_likes": None, "already_rented": False,
        # Données externes
        "avg_price_sqm": None, "rental_tension": None,
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(1500)

        # Titre
        for sel in ['h1', '[data-qa-id="adview_title"]', '[class*="Title"]']:
            el = await page.query_selector(sel)
            if el:
                data["title"] = (await el.text_content()).strip()
                break

        # Prix — sortie anticipée si hors budget
        for sel in ['[data-qa-id="adview_price"]', '[class*="price"]', '[class*="Price"]']:
            el = await page.query_selector(sel)
            if el:
                data["price"] = extract_price(await el.text_content())
                if data["price"]:
                    break
        if data["price"] and data["price"] > MAX_PRICE:
            return None

        # Localisation
        for sel in ['[data-qa-id="adview_location_informations"]', '[class*="location"]', '[class*="Location"]']:
            el = await page.query_selector(sel)
            if el:
                data["location"] = (await el.text_content()).strip()
                break
        data["city"], data["postal_code"] = extract_city_cp(data.get("location"))

        # Description
        description = ""
        for sel in ['[data-qa-id="adview_description_container"]', '[class*="description"]']:
            el = await page.query_selector(sel)
            if el:
                description = (await el.text_content()).strip()
                break

        # Section critères / attributs
        attrs_text = ""
        for sel in ['[data-qa-id="criteria_list"]', '[class*="criteria"]', '[class*="Criteria"]',
                    '[class*="attribute"]', '[class*="Attribute"]']:
            els = await page.query_selector_all(sel)
            for el in els:
                try:
                    attrs_text += " " + (await el.text_content())
                except Exception:
                    pass

        # Date de l'annonce
        for sel in ['[data-qa-id="adview_date"]', 'time', '[class*="date"]', '[class*="Date"]']:
            el = await page.query_selector(sel)
            if el:
                dt = (await el.text_content()).strip()
                if dt:
                    data["listing_date"] = dt[:30]
                    break

        # Likes / favoris
        for sel in ['[data-qa-id="adview_watchlist_button"]', '[class*="favorite"]',
                    '[class*="Favorite"]', '[class*="like"]', '[aria-label*="favori"]']:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.text_content()).strip()
                m = re.search(r'\d+', txt)
                if m:
                    data["nb_likes"] = int(m.group())
                    break

        # Texte complet pour extraction
        full_text = f"{data.get('title','') or ''} {description} {attrs_text}"

        data["surface"]        = extract_surface(full_text)
        data["property_type"]  = extract_property_type(full_text)
        data["already_rented"] = is_already_rented(full_text)
        data["dpe"]            = extract_dpe(full_text)
        data["nb_lots"]        = extract_nb_lots(full_text)
        data["taxe_fonciere"]  = extract_taxe_fonciere(full_text)
        ch = extract_charges(full_text)
        if ch:
            data["charges_monthly"] = ch
            data["charges_annual"]  = ch * 12

        # Loyer (filtre strict)
        rent = extract_rent(full_text)
        if not rent:
            return None
        data["rent_hc"] = rent

        # Calculs dérivés
        if data["price"]:
            if data["surface"]:
                data["price_per_sqm"] = round(data["price"] / data["surface"])
            data.update(calc_all_yields(
                data["price"], rent,
                data["taxe_fonciere"], data["charges_monthly"]
            ))

    except PlaywrightTimeout:
        print(f"    ⚠ Timeout: {url}")
        return None
    except Exception as e:
        print(f"    ⚠ Erreur: {e}")
        return None

    return data


async def scrape_search(page, search: dict) -> list[dict]:
    print(f"\n  ► {search['title']}")
    stubs = []
    current_url = search["url"]
    page_num = 1

    while current_url and page_num <= 15:
        print(f"    Page {page_num}...", end=" ", flush=True)
        try:
            await page.goto(current_url, wait_until="domcontentloaded", timeout=30_000)
        except PlaywrightTimeout:
            print("timeout")
            break

        page_stubs = await get_listing_stubs(page)
        print(f"{len(page_stubs)} annonces")
        stubs.extend(page_stubs)

        # Pagination
        next_href = None
        for sel in ['[data-qa-id="pagination_next"]', 'a[rel="next"]', 'a:has-text("Suivant")']:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    next_href = await btn.get_attribute('href')
                    break
            except Exception:
                continue

        if next_href:
            current_url = f"https://www.leboncoin.fr{next_href}" if next_href.startswith('/') else next_href
            page_num += 1
            await page.wait_for_timeout(1200)
        else:
            break

    # Sort stubs: "loué" mentions first (higher chance of having rent data)
    def stub_priority(s):
        low = s["card_text"].lower()
        rented  = any(kw in low for kw in ["loué", "loue", "locataire", "occupé", "bail", "investi"])
        rented_w_rent = rented and ("€" in low or "euro" in low)
        return (0 if rented_w_rent else (1 if rented else 2))

    stubs.sort(key=stub_priority)
    print(f"    Total: {len(stubs)} annonces à analyser")

    results = []
    for stub in stubs:
        card_price = extract_price(stub["card_text"])
        if card_price and card_price > MAX_PRICE:
            continue
        detail = await scrape_listing_detail(page, stub["url"])
        if detail:
            results.append(detail)
            gy = detail.get("gross_yield_pct", "?")
            ny = detail.get("net_yield_pct", "?")
            print(f"    ✓ {gy}% brut / {ny}% net — {detail.get('rent_hc','?')}€/mois"
                  f" — {(detail.get('title') or '')[:40]}")
        await page.wait_for_timeout(600)

    print(f"    → {len(results)} annonce(s) retenue(s)")
    return results

# ── Export CSV ────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "gross_yield_pct", "net_yield_pct", "property_type", "surface", "price",
    "price_per_sqm", "notaire_fees", "notaire_fees_rate", "total_cost",
    "rent_hc", "charges_monthly", "charges_annual", "taxe_fonciere",
    "nb_lots", "dpe", "city", "postal_code", "listing_date", "nb_likes",
    "avg_price_sqm", "rental_tension", "already_rented", "title", "url",
]

def export_csv(listings: list, path: Path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(listings)
    print(f"  ✓ CSV → {path}")


# ── Export HTML ───────────────────────────────────────────────────────────────

DPE_COLORS = {
    "A": "#009a44", "B": "#50b848", "C": "#c8d400",
    "D": "#ffcc00", "E": "#f7a600", "F": "#ee7200", "G": "#e2001a"
}

def _fmt(val, suffix="", fallback="—"):
    if val is None: return fallback
    if isinstance(val, float):
        return f"{val:.2f}{suffix}"
    if isinstance(val, int):
        return f"{val:,}{suffix}".replace(",", "\u202f")
    return str(val) + suffix

def _dpe(letter):
    if not letter: return "—"
    c = DPE_COLORS.get(letter.upper(), "#999")
    return f'<span class="dpe" style="background:{c}">{letter.upper()}</span>'

def _yield_span(val, min_y):
    if val is None: return "—"
    cls = "yh" if val >= min_y else ("ym" if val >= 7 else "yl")
    return f'<span class="{cls}">{val:.2f}%</span>'

def _row(l, min_yield):
    badge = '<span class="bl">★ Loué</span> ' if l.get("already_rented") else ""
    title = (l.get("title") or l["url"])[:55]
    nf    = _fmt(l.get("notaire_fees"))
    nfr   = f'<small>({_fmt(l.get("notaire_fees_rate"), "%")})</small>' if l.get("notaire_fees_rate") else ""
    return (
        f'<tr>'
        f'<td>{_yield_span(l.get("gross_yield_pct"), min_yield)}</td>'
        f'<td>{_yield_span(l.get("net_yield_pct"), min_yield)}</td>'
        f'<td>{l.get("property_type") or "—"}</td>'
        f'<td>{_fmt(l.get("surface"), " m²")}</td>'
        f'<td>{_fmt(l.get("price"), " €")}</td>'
        f'<td>{nf} € {nfr}</td>'
        f'<td><b>{_fmt(l.get("total_cost"), " €")}</b></td>'
        f'<td>{_fmt(l.get("rent_hc"), " €")}</td>'
        f'<td>{_fmt(l.get("charges_monthly"), " €")}</td>'
        f'<td>{_fmt(l.get("charges_annual"), " €")}</td>'
        f'<td>{_fmt(l.get("taxe_fonciere"), " €")}</td>'
        f'<td>{l.get("nb_lots") or "—"}</td>'
        f'<td>{_dpe(l.get("dpe"))}</td>'
        f'<td>{_fmt(l.get("price_per_sqm"), " €/m²")}</td>'
        f'<td>{_fmt(l.get("avg_price_sqm"), " €/m²")}</td>'
        f'<td>{l.get("rental_tension") or "—"}</td>'
        f'<td>{l.get("city") or "—"}<br><small>{l.get("postal_code") or ""}</small></td>'
        f'<td style="font-size:.8em">{l.get("listing_date") or "—"}</td>'
        f'<td>{_fmt(l.get("nb_likes"))}</td>'
        f'<td>{badge}<a href="{l["url"]}" target="_blank">{title}</a></td>'
        f'</tr>\n'
    )

def export_html(listings: list, path: Path, min_yield: float):
    above = [l for l in listings if (l.get("gross_yield_pct") or 0) >= min_yield]
    below = [l for l in listings if l not in above]
    rows  = "".join(_row(l, min_yield) for l in above + below)

    nb_loues  = sum(1 for l in listings if l.get("already_rented"))
    nb_net_ok = sum(1 for l in listings if (l.get("net_yield_pct") or 0) >= min_yield)
    nb_dpe    = sum(1 for l in listings if l.get("dpe"))

    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<title>LeBonCoin — Rentabilité locative</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,Arial,sans-serif;background:#f2f2f2;color:#222;padding:16px}}
h1{{color:#e0460b;margin-bottom:4px;font-size:1.5em}}
.sub{{color:#777;font-size:.85em;margin-bottom:16px}}
.stats{{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}}
.stat{{background:#fff;border-radius:8px;padding:12px 18px;text-align:center;
       box-shadow:0 1px 3px rgba(0,0,0,.1);min-width:110px}}
.stat .val{{font-size:1.8em;font-weight:700;color:#e0460b}}
.stat .lbl{{font-size:.74em;color:#777;margin-top:2px}}
.toolbar{{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}}
#search{{padding:7px 12px;border:1px solid #ddd;border-radius:6px;font-size:.88em;width:260px}}
.btn{{padding:7px 14px;background:#e0460b;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.84em}}
.btn:hover{{background:#c23a08}}
.wrap{{overflow-x:auto;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
table{{border-collapse:collapse;background:#fff;white-space:nowrap;font-size:.82em}}
thead th{{background:#e0460b;color:#fff;padding:8px 10px;text-align:left;
          cursor:pointer;position:sticky;top:0;z-index:2}}
thead th:hover{{background:#c23a08}}
td{{padding:7px 10px;border-bottom:1px solid #f0f0f0;vertical-align:middle}}
tr:hover td{{background:#fef5f2}}
.yh{{color:#177a17;font-weight:700}}
.ym{{color:#b06000;font-weight:600}}
.yl{{color:#aaa}}
.dpe{{display:inline-block;color:#fff;font-weight:700;
      padding:1px 7px;border-radius:4px;font-size:.9em}}
.bl{{background:#177a17;color:#fff;padding:1px 6px;
     border-radius:10px;font-size:.75em;white-space:nowrap}}
a{{color:#e0460b;text-decoration:none}}
a:hover{{text-decoration:underline}}
.note{{font-size:.74em;color:#aaa;margin-top:12px;line-height:1.7}}
</style></head><body>
<h1>LeBonCoin — Analyse rentabilité locative</h1>
<p class="sub">Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}
  &nbsp;|&nbsp; Seuil : <b>{min_yield}%</b> brut
  &nbsp;|&nbsp; Budget max : <b>{MAX_PRICE:,} €</b></p>

<div class="stats">
  <div class="stat"><div class="val">{len(listings)}</div><div class="lbl">annonces analysées</div></div>
  <div class="stat"><div class="val" style="color:#177a17">{len(above)}</div><div class="lbl">≥ {min_yield}% brut</div></div>
  <div class="stat"><div class="val">{nb_net_ok}</div><div class="lbl">≥ {min_yield}% net</div></div>
  <div class="stat"><div class="val">{nb_loues}</div><div class="lbl">déjà loués</div></div>
  <div class="stat"><div class="val">{nb_dpe}</div><div class="lbl">DPE renseigné</div></div>
</div>

<div class="toolbar">
  <input id="search" type="text" placeholder="Filtrer par ville, type, DPE…"
         oninput="filterTable(this.value)">
  <button class="btn" onclick="exportCSV()">⬇ Exporter CSV</button>
</div>

<div class="wrap">
<table id="tbl"><thead><tr>
  <th onclick="sortTable(0)">Brut ↕</th>
  <th onclick="sortTable(1)">Net ↕</th>
  <th>Type</th>
  <th onclick="sortTable(3)">Surface ↕</th>
  <th onclick="sortTable(4)">Prix ↕</th>
  <th>Frais notaire</th>
  <th onclick="sortTable(6)">Coût total ↕</th>
  <th onclick="sortTable(7)">Loyer HC/mois ↕</th>
  <th>Charges/mois</th>
  <th>Charges/an</th>
  <th>Taxe fonc./an</th>
  <th>Nb lots</th>
  <th>DPE</th>
  <th onclick="sortTable(13)">Prix/m² ↕</th>
  <th onclick="sortTable(14)">Moy. marché/m² ↕</th>
  <th>Tension locative</th>
  <th>Ville / CP</th>
  <th>Date annonce</th>
  <th>❤ Likes</th>
  <th>Annonce</th>
</tr></thead>
<tbody>
{rows}
</tbody></table>
</div>

<p class="note">
  <b>Rentabilité nette</b> = (loyer annuel − taxe foncière − charges copro non récupérables ~25% − vacance ~0,5 mois) / coût total (prix + frais de notaire).<br>
  <b>Frais de notaire</b> : bien ancien — droits de mutation 5,81% + émoluments notaire + CSI 0,1% + débours ~1 000 €.<br>
  Taxe foncière et charges : extraites de l'annonce si disponibles, sinon estimées (1 mois loyer / 10% loyer annuel).<br>
  Prix moyen/m² : <a href="https://www.meilleursagents.com" target="_blank">meilleursagents.com</a> &nbsp;|&nbsp;
  Tension locative : <a href="https://www.locservice.fr/tensiometre/" target="_blank">locservice.fr</a>
</p>

<script>
const TBL = document.getElementById('tbl');
function sortTable(col) {{
  const b = TBL.tBodies[0];
  const rows = [...b.rows];
  const asc  = TBL.dataset.col == col && TBL.dataset.dir == 'asc';
  rows.sort((a, b) => {{
    const av = parseFloat(a.cells[col]?.innerText) || 0;
    const bv = parseFloat(b.cells[col]?.innerText) || 0;
    return asc ? av - bv : bv - av;
  }});
  rows.forEach(r => b.appendChild(r));
  TBL.dataset.col = col;
  TBL.dataset.dir = asc ? 'desc' : 'asc';
}}
function filterTable(q) {{
  q = q.toLowerCase();
  [...TBL.tBodies[0].rows].forEach(r => {{
    r.style.display = r.innerText.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
function exportCSV() {{
  const rows = [...TBL.rows];
  const csv  = rows.map(r =>
    [...r.cells].map(c => '"' + c.innerText.replace(/"/g,'""') + '"').join(',')
  ).join('\\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
  a.download = 'leboncoin_analyse.csv';
  a.click();
}}
window.onload = () => sortTable(0);
</script>
</body></html>"""

    path.write_text(html, encoding="utf-8")
    print(f"  ✓ HTML → {path}")


# ── Main ────────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 62)
    print("  LeBonCoin — Analyseur de rentabilité locative")
    print(f"  Seuil : {MIN_YIELD}% brut  |  Budget max : {MAX_PRICE:,} €")
    print(f"  Loyer : annonces avec loyer explicite uniquement")
    print("=" * 62)

    if not EMAIL or not PASSWORD:
        print("ERREUR : LBC_EMAIL / LBC_PASSWORD manquants dans .env")
        return

    seen_urls = load_seen_urls()

    # Persistent Chrome profile → survives between runs, bypasses bot detection
    PROFILE_DIR = Path.home() / ".leboncoin_profile"
    PROFILE_DIR.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,          # Always visible for persistent profile
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            args=[
                "--lang=fr-FR",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )
        # Mask navigator.webdriver on every page
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.pages[0] if context.pages else await context.new_page()

        # 1. Login
        if not await login(page):
            print("\nImpossible de continuer sans connexion.")
            await context.close()
            return

        # 2. Saved searches
        searches = await get_saved_searches(page)
        if not searches:
            print("\nAucune recherche sauvegardée trouvée.")
            await context.close()
            return

        # 3. Scrape toutes les recherches
        all_listings: list = []
        for search in searches:
            all_listings.extend(await scrape_search(page, search))

        # 4. Dédoublonnage par URL
        seen_u: set = set()
        unique = []
        for l in all_listings:
            if l["url"] not in seen_u:
                seen_u.add(l["url"])
                unique.append(l)

        # 5. Enrichissement données externes (meilleursagents + locservice)
        if unique:
            await enrich_external_data(page, unique)

        await context.close()

    # 6. Tri : loués en premier, puis rendement décroissant
    unique.sort(key=lambda l: (0 if l.get("already_rented") else 1,
                               -(l.get("gross_yield_pct") or 0)))
    qualified = [l for l in unique if (l.get("gross_yield_pct") or 0) >= MIN_YIELD]

    # 7. Nouvelles annonces
    new_listings = [l for l in qualified if l["url"] not in seen_urls]
    save_seen_urls(seen_u)

    # ── Résumé console ────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  RÉSULTATS")
    print(f"  Annonces retenues (loyer explicite) : {len(unique)}")
    print(f"  Rentabilité brute ≥ {MIN_YIELD}%          : {len(qualified)}")
    print(f"  Nouvelles ce scan                   : {len(new_listings)}")
    print("=" * 64)

    for l in qualified[:10]:
        star = "★ " if l.get("already_rented") else "  "
        print(f"  {star}{l['gross_yield_pct']:5.2f}% brut"
              f" / {l.get('net_yield_pct','?')}% net"
              f" | {l.get('rent_hc','?')} €/mois"
              f" | {l.get('price','?')} €"
              f" | {l.get('city','?')}"
              f" | {(l.get('title') or '')[:30]}")

    # 8. WhatsApp
    if new_listings and WA_PHONE and WA_APIKEY:
        lines = [f"LeBonCoin — {len(new_listings)} nouvelle(s) >= {MIN_YIELD}% :"]
        for l in new_listings[:5]:
            lines.append(f"• {l['gross_yield_pct']}% | {l.get('rent_hc')}€/mois"
                         f" | {l.get('price')}€ | {l['url']}")
        send_whatsapp("\n".join(lines))
    elif new_listings:
        print(f"\n  {len(new_listings)} nouvelle(s) — configurez WA_PHONE + WA_APIKEY dans .env pour WhatsApp")

    # 9. Export
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_csv(unique, OUTPUT_DIR / f"annonces_{ts}.csv")
    export_html(unique, OUTPUT_DIR / f"rapport_{ts}.html", MIN_YIELD)

    print(f"\n  Rapport : {(OUTPUT_DIR / f'rapport_{ts}.html').resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
