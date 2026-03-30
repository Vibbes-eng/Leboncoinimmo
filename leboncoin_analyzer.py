#!/usr/bin/env python3
"""
LeBonCoin Rental Property Analyzer
─────────────────────────────────────────────────────────────────────────────
• Se connecte au compte LeBonCoin
• Parcourt toutes les recherches sauvegardées
• Retient UNIQUEMENT les annonces où le loyer est mentionné explicitement
• Calcule rentabilité brute ET nette
• Filtre : loyer présent + prix ≤ MAX_PRICE + rendement ≥ MIN_YIELD
• Trie : biens loués en priorité, puis par rendement décroissant
• Exporte : CSV + rapport HTML interactif
• Notifie via WhatsApp (CallMeBot, gratuit) les nouvelles annonces
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

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
EMAIL       = os.getenv("LBC_EMAIL")
PASSWORD    = os.getenv("LBC_PASSWORD")
MIN_YIELD   = float(os.getenv("MIN_YIELD", "10.0"))    # rentabilité brute min %
MAX_PRICE   = int(os.getenv("MAX_PRICE", "150000"))    # budget max €
HEADLESS    = os.getenv("HEADLESS", "false").lower() == "true"

# WhatsApp via CallMeBot (gratuit) — remplir dans .env
WA_PHONE    = os.getenv("WA_PHONE", "")               # ex: 33612345678
WA_APIKEY   = os.getenv("WA_APIKEY", "")

# Hypothèses pour rentabilité NETTE (personnalisables dans .env)
CHARGES_RATE      = float(os.getenv("CHARGES_RATE", "0.15"))   # 15% loyer annuel (copro + entretien)
TAXE_FONCIERE_MOIS= float(os.getenv("TAXE_FONCIERE_MOIS", "1.0"))  # ~ 1 mois de loyer/an
VACANCE_MOIS      = float(os.getenv("VACANCE_MOIS", "0.5"))    # 0.5 mois de vacance/an

OUTPUT_DIR  = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
SEEN_FILE   = OUTPUT_DIR / "seen_urls.json"  # Pour détecter les nouvelles annonces

# ── Rent / price helpers ───────────────────────────────────────────────────────

RENT_PATTERNS = [
    r'loyer\s+(?:actuel\s+)?(?:mensuel\s+)?(?:hors\s+charges?\s+)?(?:de\s+)?(\d[\d\s\u00a0]{1,6})\s*€',
    r'(\d[\d\s\u00a0]{1,6})\s*€\s*/?\s*(?:par\s+)?mois',
    r'(\d[\d\s\u00a0]{1,6})\s*€\s*(?:de\s+)?loyer',
    r'loyer\s*[:\-]\s*(\d[\d\s\u00a0]{1,6})',
    r'loyer\s+(?:en\s+cours\s+)?(?:de\s+)?(\d[\d\s\u00a0]{1,6})',
    r'(\d[\d\s\u00a0]{1,6})\s*euros?\s*/?\s*mois',
    r'(\d[\d\s\u00a0]{1,6})\s*€\s*cc',       # charges comprises
    r'(\d[\d\s\u00a0]{1,6})\s*€\s*hc',       # hors charges
]

def clean_number(s: str) -> int | None:
    s = re.sub(r'[\s\u00a0]', '', s)
    try:
        return int(s)
    except ValueError:
        return None

def extract_rent(text: str) -> int | None:
    for pattern in RENT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
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

def is_already_rented(text: str) -> bool:
    """Returns True if the listing clearly indicates the property is currently rented."""
    keywords = [
        "loué", "loue ", "bien loué", "actuellement loué", "occupé",
        "locataire en place", "bail en cours", "vendu loué",
        "investi", "déjà loué", "bail", "en location",
    ]
    low = text.lower()
    return any(kw in low for kw in keywords)

# ── Yield calculations ─────────────────────────────────────────────────────────

def calc_yields(price: int, monthly_rent: int) -> dict:
    annual_rent = monthly_rent * 12
    brute = round((annual_rent / price) * 100, 2)

    # Net estimation
    charges      = annual_rent * CHARGES_RATE
    taxe_fonc    = monthly_rent * TAXE_FONCIERE_MOIS
    vacance      = monthly_rent * VACANCE_MOIS
    net_annual   = annual_rent - charges - taxe_fonc - vacance
    net          = round((net_annual / price) * 100, 2)

    return {"gross_yield_pct": brute, "net_yield_pct": net}

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

async def login(page) -> bool:
    print("  → Connexion à LeBonCoin...")
    await page.goto("https://www.leboncoin.fr/compte/login", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    await accept_cookies(page)

    email_sel = 'input[name="st_username"], input[type="email"], input[id*="email"]'
    pwd_sel   = 'input[name="st_passwd"],  input[type="password"], input[id*="password"]'

    try:
        await page.fill(email_sel, EMAIL)
        await page.wait_for_timeout(400)
        await page.fill(pwd_sel, PASSWORD)
        await page.wait_for_timeout(400)
        await page.click('button[type="submit"]')
    except Exception as e:
        print(f"  ✗ Formulaire introuvable: {e}")
        await page.screenshot(path="output/debug_login.png")
        return False

    await page.wait_for_timeout(4000)

    if "login" not in page.url:
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
    Visit a single listing.
    Returns None if price > MAX_PRICE or no rent found.
    """
    data = {
        "url": url, "title": None, "price": None, "surface": None,
        "location": None, "description": None,
        "monthly_rent": None, "rent_source": None,
        "gross_yield_pct": None, "net_yield_pct": None,
        "already_rented": False,
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(1500)

        # Title
        for sel in ['h1', '[data-qa-id="adview_title"]', '[class*="Title"]']:
            el = await page.query_selector(sel)
            if el:
                data["title"] = (await el.text_content()).strip()
                break

        # Price — early exit if over budget
        for sel in ['[data-qa-id="adview_price"]', '[class*="price"]', '[class*="Price"]']:
            el = await page.query_selector(sel)
            if el:
                data["price"] = extract_price(await el.text_content())
                if data["price"]:
                    break

        if data["price"] and data["price"] > MAX_PRICE:
            return None  # Over budget, skip

        # Location
        for sel in ['[data-qa-id="adview_location_informations"]', '[class*="location"]']:
            el = await page.query_selector(sel)
            if el:
                data["location"] = (await el.text_content()).strip()
                break

        # Description
        for sel in ['[data-qa-id="adview_description_container"]', '[class*="description"]']:
            el = await page.query_selector(sel)
            if el:
                data["description"] = (await el.text_content()).strip()
                break

        full_text = f"{data.get('title','') or ''} {data.get('description','') or ''}"

        data["surface"]         = extract_surface(full_text)
        data["already_rented"]  = is_already_rented(full_text)
        rent = extract_rent(full_text)

        # ── STRICT FILTER: only keep listings with explicit rent ──
        if not rent:
            return None

        data["monthly_rent"]  = rent
        data["rent_source"]   = "annonce"
        yields = calc_yields(data["price"], rent) if data["price"] else {}
        data.update(yields)

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
        # Quick pre-filter: price visible in card
        card_price = extract_price(stub["card_text"])
        if card_price and card_price > MAX_PRICE:
            continue  # Skip before visiting

        detail = await scrape_listing_detail(page, stub["url"])
        if detail:
            results.append(detail)
            gy  = detail.get("gross_yield_pct", "?")
            ny  = detail.get("net_yield_pct", "?")
            rent = detail.get("monthly_rent", "?")
            print(f"    ✓ {gy}% brut / {ny}% net — {rent}€/mois — {detail.get('title','')[:45]}")
        await page.wait_for_timeout(600)

    print(f"    → {len(results)} annonce(s) retenue(s) (loyer explicite + budget)")
    return results

# ── Export ─────────────────────────────────────────────────────────────────────

def export_csv(listings: list[dict], path: Path):
    fields = [
        "gross_yield_pct", "net_yield_pct", "monthly_rent", "price",
        "surface", "location", "already_rented", "title", "url", "rent_source"
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(listings)
    print(f"  ✓ CSV → {path}")


def export_html(listings: list[dict], path: Path, min_yield: float):
    above = [l for l in listings if l.get("gross_yield_pct", 0) >= min_yield]
    below = [l for l in listings if l not in above]

    def row(l, highlight=False):
        gy   = l.get("gross_yield_pct")
        ny   = l.get("net_yield_pct")
        gy_s = f"{gy:.2f}%" if gy else "—"
        ny_s = f"{ny:.2f}%" if ny else "—"
        rent = f"{l.get('monthly_rent'):,} €/mois".replace(",", "\u202f") if l.get("monthly_rent") else "—"
        price= f"{l.get('price'):,} €".replace(",", "\u202f") if l.get("price") else "—"
        surf = f"{l.get('surface')} m²" if l.get("surface") else "—"
        badge= '<span class="badge">Loué</span>' if l.get("already_rented") else ""
        cls  = "high" if gy and gy >= min_yield else "mid"
        hl   = ' style="background:#fff8f0"' if highlight else ""
        return f"""<tr{hl}>
            <td><b class="y-{cls}">{gy_s}</b></td>
            <td><span class="y-net">{ny_s}</span></td>
            <td>{rent}</td><td>{price}</td><td>{surf}</td>
            <td>{l.get('location') or '—'}</td>
            <td>{badge} <a href="{l['url']}" target="_blank">{(l.get('title') or '')[:55]}</a></td>
        </tr>"""

    rows = "".join(row(l, True) for l in above) + "".join(row(l) for l in below)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>LeBonCoin — Rentabilité locative</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:system-ui,Arial,sans-serif;background:#f4f4f4;color:#222;padding:20px}}
  h1{{color:#e0460b;margin-bottom:4px}}
  .sub{{color:#666;font-size:.9em;margin-bottom:20px}}
  .stats{{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}}
  .stat{{background:#fff;border-radius:10px;padding:14px 22px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  .stat .val{{font-size:2em;font-weight:700;color:#e0460b}}
  .stat .lbl{{font-size:.82em;color:#666;margin-top:2px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  th{{background:#e0460b;color:#fff;padding:10px 12px;text-align:left;cursor:pointer;white-space:nowrap;font-size:.9em}}
  th:hover{{background:#c23a08}}
  td{{padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:.88em;vertical-align:middle}}
  tr:hover td{{background:#fef5f2}}
  .y-high{{color:#177a17;font-weight:700;font-size:1.05em}}
  .y-mid{{color:#b06000;font-weight:600}}
  .y-net{{color:#555;font-size:.92em}}
  .badge{{background:#177a17;color:#fff;padding:1px 7px;border-radius:10px;font-size:.76em;white-space:nowrap}}
  a{{color:#e0460b;text-decoration:none}}
  a:hover{{text-decoration:underline}}
  .sep td{{background:#fce9e3;font-weight:600;color:#e0460b;font-size:.82em;padding:5px 12px}}
  input#search{{padding:8px 14px;border:1px solid #ddd;border-radius:8px;font-size:.9em;width:280px;margin-bottom:12px}}
  .note{{font-size:.78em;color:#999;margin-top:14px}}
</style>
</head>
<body>
<h1>LeBonCoin — Analyse rentabilité locative</h1>
<p class="sub">Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} &nbsp;|&nbsp;
  Seuil : <b>{min_yield}%</b> brut &nbsp;|&nbsp; Budget max : <b>{MAX_PRICE:,} €</b></p>

<div class="stats">
  <div class="stat"><div class="val">{len(listings)}</div><div class="lbl">annonces (loyer explicite)</div></div>
  <div class="stat"><div class="val" style="color:#177a17">{len(above)}</div><div class="lbl">≥ {min_yield}% rentabilité brute</div></div>
  <div class="stat"><div class="val">{sum(1 for l in listings if l.get("already_rented"))}</div><div class="lbl">déjà loués</div></div>
  <div class="stat"><div class="val">{sum(1 for l in listings if l.get("net_yield_pct") and l["net_yield_pct"] >= min_yield)}</div><div class="lbl">≥ {min_yield}% rentabilité nette</div></div>
</div>

<input id="search" type="text" placeholder="Filtrer (ville, prix…)" oninput="filterTable(this.value)">

<table id="tbl">
<thead><tr>
  <th onclick="sortTable(0)">Brut ↕</th>
  <th onclick="sortTable(1)">Net ↕</th>
  <th onclick="sortTable(2)">Loyer/mois ↕</th>
  <th onclick="sortTable(3)">Prix ↕</th>
  <th>Surface</th>
  <th>Localisation</th>
  <th>Annonce</th>
</tr></thead>
<tbody>
{rows}
</tbody>
</table>

<p class="note">
  Rentabilité nette estimée = loyer annuel - charges ({int(CHARGES_RATE*100)}%) - taxe foncière (~{TAXE_FONCIERE_MOIS} mois) - vacance (~{VACANCE_MOIS} mois) &nbsp;/&nbsp; prix d'achat.<br>
  Seules les annonces mentionnant explicitement un loyer sont affichées.
</p>

<script>
function sortTable(col) {{
  const t = document.getElementById('tbl');
  const b = t.tBodies[0];
  const rows = [...b.rows].filter(r => !r.classList.contains('sep'));
  const asc = t.dataset.col == col && t.dataset.dir == 'asc';
  rows.sort((a,b) => {{
    const av = parseFloat(a.cells[col]?.innerText) || 0;
    const bv = parseFloat(b.cells[col]?.innerText) || 0;
    return asc ? av-bv : bv-av;
  }});
  rows.forEach(r => b.appendChild(r));
  t.dataset.col = col; t.dataset.dir = asc ? 'desc' : 'asc';
}}
function filterTable(q) {{
  q = q.toLowerCase();
  [...document.querySelectorAll('#tbl tbody tr:not(.sep)')].forEach(r => {{
    r.style.display = r.innerText.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
window.onload = () => sortTable(0);
</script>
</body>
</html>"""

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

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--lang=fr-FR"],
        )
        context = await browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # 1. Login
        if not await login(page):
            print("\nImpossible de continuer sans connexion.")
            await browser.close()
            return

        # 2. Saved searches
        searches = await get_saved_searches(page)
        if not searches:
            print("\nAucune recherche sauvegardée trouvée.")
            await browser.close()
            return

        # 3. Scrape
        all_listings: list[dict] = []
        for search in searches:
            results = await scrape_search(page, search)
            all_listings.extend(results)

        await browser.close()

    # 4. Deduplicate
    seen_u: set = set()
    unique = []
    for l in all_listings:
        if l["url"] not in seen_u:
            seen_u.add(l["url"])
            unique.append(l)

    # 5. Filter by MIN_YIELD (brut)
    qualified = [l for l in unique if l.get("gross_yield_pct") and l["gross_yield_pct"] >= MIN_YIELD]

    # 6. Sort: already rented first, then by gross yield desc
    qualified.sort(key=lambda l: (0 if l.get("already_rented") else 1, -(l.get("gross_yield_pct") or 0)))

    # 7. Detect NEW listings (for WhatsApp notification)
    new_listings = [l for l in qualified if l["url"] not in seen_urls]
    save_seen_urls(seen_u)

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("  RÉSULTATS")
    print(f"  Annonces avec loyer explicite   : {len(unique)}")
    print(f"  Rentabilité ≥ {MIN_YIELD}%              : {len(qualified)}")
    print(f"  Nouvelles annonces (ce scan)     : {len(new_listings)}")
    print("=" * 62)

    if qualified:
        print(f"\n  TOP (rentabilité ≥ {MIN_YIELD}%) :")
        for l in qualified[:10]:
            rented = "★ " if l.get("already_rented") else "  "
            print(f"  {rented}{l['gross_yield_pct']:5.2f}% brut / {l.get('net_yield_pct','?')}% net"
                  f" | {l.get('monthly_rent','?')}€/mois | {l.get('price','?')}€"
                  f" | {(l.get('title') or '')[:40]}")

    # 8. WhatsApp notification for new qualifying listings
    if new_listings and WA_PHONE and WA_APIKEY:
        msg_lines = [f"🏠 LeBonCoin — {len(new_listings)} nouvelle(s) annonce(s) ≥ {MIN_YIELD}% :"]
        for l in new_listings[:5]:
            msg_lines.append(
                f"• {l['gross_yield_pct']}% brut | {l.get('monthly_rent')}€/mois | {l.get('price')}€ | {l['url']}"
            )
        send_whatsapp("\n".join(msg_lines))
    elif new_listings:
        print(f"\n  💡 {len(new_listings)} nouvelle(s) annonce(s) — configurez WA_PHONE + WA_APIKEY dans .env pour les recevoir sur WhatsApp")

    # 9. Export
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_csv(unique, OUTPUT_DIR / f"toutes_annonces_{ts}.csv")
    export_html(unique, OUTPUT_DIR / f"rapport_{ts}.html", MIN_YIELD)

    print(f"\n  Ouvrez le rapport dans votre navigateur :")
    print(f"  {(OUTPUT_DIR / f'rapport_{ts}.html').resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
