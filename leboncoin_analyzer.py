#!/usr/bin/env python3
"""
LeBonCoin Analyzer — Phase 1 MVP (CDP)
======================================
1. Lancez Chrome via lancer_chrome.bat (port 9222)
2. Naviguez vers vos recherches sauvegardees OU une page de resultats
3. Lancez : python leboncoin_analyzer.py

Sortie : rapport HTML interactif + CSV + notification WhatsApp (optionnel)
"""

import asyncio
import csv
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ── Configuration ─────────────────────────────────────────────────
load_dotenv()

CDP_PORT   = int(os.getenv("CDP_PORT", "9222"))
MIN_YIELD  = float(os.getenv("MIN_YIELD", "10.0"))
MAX_PRICE  = int(os.getenv("MAX_PRICE", "150000"))
WA_PHONE   = os.getenv("WA_PHONE", "").strip()
WA_APIKEY  = os.getenv("WA_APIKEY", "").strip()

CDP_URL    = f"http://localhost:{CDP_PORT}"
SEEN_FILE  = Path("seen_urls.json")
OUTPUT_DIR = Path(".")

BLANK = "—"  # valeur manquante — JAMAIS estimée


# ── Persistance des URLs déjà vues ────────────────────────────────

def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_seen(seen: set) -> None:
    SEEN_FILE.write_text(
        json.dumps(sorted(seen), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Détection du type de page ─────────────────────────────────────

async def detect_page_type(page) -> str:
    """
    Retourne :
      'saved_searches' — page liste des recherches sauvegardées
      'results'        — page de résultats d'annonces
      'unknown'        — autre
    """
    url = page.url
    if "mes-favoris/recherches" in url:
        return "saved_searches"
    if "recherche" in url or "ventes_immobilieres" in url or "locations" in url:
        return "results"
    await page.wait_for_load_state("domcontentloaded")
    url = page.url
    if "mes-favoris/recherches" in url:
        return "saved_searches"
    if "recherche" in url or "ventes_immobilieres" in url or "locations" in url:
        return "results"
    return "unknown"


# ── Extraction des recherches sauvegardées ────────────────────────

async def get_saved_search_urls(page) -> list:
    """Retourne les URLs de résultats de chaque recherche sauvegardée."""
    await page.wait_for_load_state("networkidle")
    urls = []
    selectors = [
        "a[href*='recherche']",
        "a[href*='ventes_immobilieres']",
        "a[href*='locations']",
        "[data-qa-id='saved-search-link']",
        "a[data-test-id='saved-search-link']",
    ]
    for sel in selectors:
        links = await page.query_selector_all(sel)
        for link in links:
            href = await link.get_attribute("href")
            if href and ("recherche" in href or "ventes_immobilieres" in href or "locations" in href):
                full = href if href.startswith("http") else "https://www.leboncoin.fr" + href
                if full not in urls:
                    urls.append(full)
    return urls


# ── Parsing d'une annonce individuelle ───────────────────────────

def _text(el_text: str) -> str:
    return el_text.strip() if el_text else ""


def parse_price(text: str):
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else None


def parse_surface(text: str):
    if not text:
        return None
    m = re.search(r"(\d+[\.,]?\d*)\s*m", text)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def parse_loyer(text: str):
    if not text:
        return None
    patterns = [
        r"loyer[^\d]*(\d[\d\s]*)\s*[€e]",
        r"(\d[\d\s]*)\s*[€e]\s*/\s*mois",
        r"(\d[\d\s]*)\s*[€e]\s*par\s*mois",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = re.sub(r"\s", "", m.group(1))
            try:
                return int(val)
            except ValueError:
                pass
    return None


def parse_taxe_fonciere(text: str):
    if not text:
        return None
    m = re.search(r"taxe\s+fonci[eè]re[^\d]*(\d[\d\s]*)\s*[€e]", text, re.IGNORECASE)
    if m:
        val = re.sub(r"\s", "", m.group(1))
        try:
            return int(val)
        except ValueError:
            pass
    return None


def parse_charges(text: str):
    if not text:
        return None
    patterns = [
        r"charges[^\d]*(\d[\d\s]*)\s*[€e]\s*/\s*mois",
        r"charges\s+mensuelles[^\d]*(\d[\d\s]*)",
        r"(\d[\d\s]*)\s*[€e]\s*de\s*charges",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = re.sub(r"\s", "", m.group(1))
            try:
                return int(val)
            except ValueError:
                pass
    return None


# ── Frais de notaire (bien ancien) ────────────────────────────────

def frais_notaire(price: int) -> int:
    droits = price * 0.0580665
    if price <= 6500:
        emol_ht = price * 0.03945
    elif price <= 17000:
        emol_ht = 6500 * 0.03945 + (price - 6500) * 0.01627
    elif price <= 60000:
        emol_ht = 6500 * 0.03945 + 10500 * 0.01627 + (price - 17000) * 0.01085
    else:
        emol_ht = 6500 * 0.03945 + 10500 * 0.01627 + 43000 * 0.01085 + (price - 60000) * 0.00814
    emol_ttc = emol_ht * 1.20
    csi = price * 0.001
    debours = 1000
    return int(droits + emol_ttc + csi + debours)


# ── Calcul rentabilité ────────────────────────────────────────────

def calc_yield_brut(loyer_mensuel, prix_net):
    if loyer_mensuel is None or prix_net is None or prix_net <= 0:
        return None
    fn = frais_notaire(prix_net)
    total = prix_net + fn
    return round(loyer_mensuel * 12 / total * 100, 2)


def calc_yield_net(loyer_mensuel, prix_net, taxe_fonciere, charges_mensuelles):
    if None in (loyer_mensuel, prix_net, taxe_fonciere, charges_mensuelles):
        return None
    fn = frais_notaire(prix_net)
    total = prix_net + fn
    loyer_annuel = loyer_mensuel * 12
    charges_non_rec = charges_mensuelles * 12 * 0.25
    vacance = loyer_annuel * 0.5 / 12
    revenus_nets = loyer_annuel - taxe_fonciere - charges_non_rec - vacance
    return round(revenus_nets / total * 100, 2)


# ── Extraction des liens d'annonces via JavaScript ────────────────

async def get_ad_links_from_page(page) -> list:
    """
    Extrait tous les liens d'annonces depuis le DOM de la page courante.
    N'effectue AUCUNE navigation — travaille sur la page déjà chargée.
    """
    links = await page.evaluate("""() => {
        const seen = new Set();
        const out = [];
        for (const a of document.querySelectorAll('a[href]')) {
            const h = a.href;
            if (
                (h.includes('/ventes_immobilieres/') ||
                 h.includes('/locations/') ||
                 /\\/ad\\/[^/]+\\/\\d+/.test(h)) &&
                !seen.has(h)
            ) {
                seen.add(h);
                out.push(h);
            }
        }
        return out;
    }""")
    return links


# ── Scraping d'une page de résultats ─────────────────────────────

async def scrape_results_page(page) -> list:
    """
    Extrait les liens d'annonces de la page courante (déjà chargée dans Chrome).
    Gère la pagination via clic sur "page suivante" (pas de page.goto).
    """
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(500)

    all_links = []
    page_num = 1

    while True:
        links = await get_ad_links_from_page(page)
        new = [l for l in links if l not in all_links]
        all_links.extend(new)
        print(f"  → Page {page_num} : {len(new)} lien(s) trouvé(s) (total {len(all_links)})")

        next_btn = await page.query_selector(
            "[data-qa-id='pagination_next_page'], "
            "a[aria-label='Page suivante'], "
            "a[rel='next'], "
            "[data-test-id='pagination-next'], "
            "button[aria-label*='suivant'], button[aria-label*='next']"
        )
        if not next_btn:
            break

        prev_url = page.url
        try:
            await next_btn.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(1500)
        except Exception as e:
            print(f"  [WARN] Pagination page {page_num}: {e}")
            break

        if page.url == prev_url:
            break
        page_num += 1

    return [
        {
            "url": link,
            "title": BLANK,
            "price": None,
            "location": BLANK,
            "surface": None,
            "loyer": None,
            "taxe_fonciere": None,
            "charges": None,
            "nb_pieces": None,
            "description": None,
        }
        for link in all_links
    ]


# ── Scraping du détail d'une annonce ─────────────────────────────

async def scrape_listing_detail(context, url: str) -> dict:
    """
    Ouvre l'annonce dans un NOUVEL onglet, scrape, ferme l'onglet.
    La page de résultats reste intacte dans Chrome.
    """
    data = {
        "url": url,
        "title": BLANK,
        "price": None,
        "location": BLANK,
        "surface": None,
        "loyer": None,
        "taxe_fonciere": None,
        "charges": None,
        "nb_pieces": None,
        "description": BLANK,
    }
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        for sel in ["h1", "[data-qa-id='ad_title']", "[data-test-id='ad-title']"]:
            el = await page.query_selector(sel)
            if el:
                data["title"] = _text(await el.inner_text())
                break

        for sel in ["[data-qa-id='adview_price']", "[data-test-id='ad-price']", "[class*='price']"]:
            el = await page.query_selector(sel)
            if el:
                data["price"] = parse_price(_text(await el.inner_text()))
                if data["price"]:
                    break

        for sel in [
            "[data-qa-id='adview_location_informations']",
            "[data-test-id='ad-location']",
            "[class*='location']",
        ]:
            el = await page.query_selector(sel)
            if el:
                data["location"] = _text(await el.inner_text()).split("\n")[0]
                break

        for sel in [
            "[data-qa-id='adview_description_container']",
            "[data-test-id='ad-description']",
            "[class*='description']",
        ]:
            el = await page.query_selector(sel)
            if el:
                data["description"] = _text(await el.inner_text())
                break

        attr_els = await page.query_selector_all(
            "[data-qa-id='criteria_item'], [data-test-id='criteria-item'], "
            "[class*='criteria'], [class*='attribute']"
        )
        for attr_el in attr_els:
            text = _text(await attr_el.inner_text()).lower()
            if "surface" in text or "m²" in text:
                s = parse_surface(text)
                if s:
                    data["surface"] = s
            elif "pièce" in text or "piece" in text:
                m = re.search(r"(\d+)", text)
                if m:
                    data["nb_pieces"] = int(m.group(1))

        full_text = data["description"] or ""
        if data["loyer"] is None:
            data["loyer"] = parse_loyer(full_text)
        if data["taxe_fonciere"] is None:
            data["taxe_fonciere"] = parse_taxe_fonciere(full_text)
        if data["charges"] is None:
            data["charges"] = parse_charges(full_text)

    except Exception as e:
        print(f"    [WARN] scrape_listing_detail({url}): {e}")
    finally:
        await page.close()

    return data


# ── Filtre et enrichissement ──────────────────────────────────────

def enrich(listing: dict):
    price = listing.get("price")
    loyer = listing.get("loyer")

    if price is None:
        return None
    if price > MAX_PRICE:
        return None
    if loyer is None:
        return None

    fn = frais_notaire(price)
    total_cost = price + fn
    yield_brut = calc_yield_brut(loyer, price)

    if yield_brut is None or yield_brut < MIN_YIELD:
        return None

    taxe = listing.get("taxe_fonciere")
    charges = listing.get("charges")
    yield_net = calc_yield_net(loyer, price, taxe, charges)

    surface = listing.get("surface")
    price_m2 = round(price / surface, 0) if surface else None
    loyer_m2 = round(loyer / surface, 2) if surface else None

    return {
        "url":             listing.get("url", BLANK),
        "titre":           listing.get("title", BLANK),
        "ville":           listing.get("location", BLANK),
        "nb_pieces":       listing.get("nb_pieces") or BLANK,
        "surface_m2":      surface or BLANK,
        "prix_net":        price,
        "frais_notaire":   fn,
        "cout_total":      total_cost,
        "prix_m2":         price_m2 or BLANK,
        "loyer_hc":        loyer,
        "loyer_m2":        loyer_m2 or BLANK,
        "taxe_fonciere":   taxe or BLANK,
        "charges_mois":    charges or BLANK,
        "rendement_brut":  yield_brut,
        "rendement_net":   yield_net or BLANK,
        "description":     (listing.get("description") or BLANK)[:300],
        "nouveau":         False,
        "date_scraping":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ── Export HTML ───────────────────────────────────────────────────

HTML_STYLE = """<style>
body{font-family:Arial,sans-serif;font-size:13px;margin:20px;background:#f5f5f5}
h1{color:#333}
.stats{background:#fff;padding:10px 20px;border-radius:8px;margin-bottom:15px;
       box-shadow:0 1px 4px rgba(0,0,0,.1);display:inline-block}
table{border-collapse:collapse;width:100%;background:#fff;
      box-shadow:0 1px 4px rgba(0,0,0,.1);border-radius:8px;overflow:hidden}
th{background:#e63946;color:#fff;padding:8px 10px;cursor:pointer;
   white-space:nowrap;user-select:none}
th:hover{background:#c1121f}
td{padding:7px 10px;border-bottom:1px solid #eee;vertical-align:top}
tr:hover td{background:#fff8f8}
tr.nouveau td{background:#fffde7}
.badge-new{background:#ff9800;color:#fff;padding:2px 7px;border-radius:10px;
           font-size:11px;font-weight:bold;margin-left:6px}
.yield-high{color:#2d6a4f;font-weight:bold}
.yield-med{color:#e07800;font-weight:bold}
input[type=text]{padding:6px 10px;border:1px solid #ccc;border-radius:4px;
                 width:220px;margin-right:8px}
button{padding:6px 14px;border:none;border-radius:4px;cursor:pointer;
       background:#e63946;color:#fff}
button:hover{background:#c1121f}
.filters{margin-bottom:12px}
a{color:#e63946;text-decoration:none}
a:hover{text-decoration:underline}
.desc{color:#555;font-size:12px;max-width:280px}
</style>"""

HTML_SCRIPT = """<script>
let sCol=-1,sDir=1;
function sortTable(c){
  const tb=document.getElementById('tbl');
  const rows=Array.from(tb.tBodies[0].rows);
  if(sCol===c)sDir*=-1;else{sCol=c;sDir=1;}
  rows.sort((a,b)=>{
    let va=a.cells[c].dataset.val??a.cells[c].innerText;
    let vb=b.cells[c].dataset.val??b.cells[c].innerText;
    const na=parseFloat(va),nb=parseFloat(vb);
    if(!isNaN(na)&&!isNaN(nb))return sDir*(na-nb);
    return sDir*va.localeCompare(vb,'fr');
  });
  rows.forEach(r=>tb.tBodies[0].appendChild(r));
}
function filterTable(){
  const q=document.getElementById('search').value.toLowerCase();
  const minY=parseFloat(document.getElementById('minYield').value)||0;
  const maxP=parseFloat(document.getElementById('maxPrice').value)||Infinity;
  for(const r of document.getElementById('tbl').tBodies[0].rows){
    const y=parseFloat(r.cells[13].dataset.val)||0;
    const p=parseFloat(r.cells[5].dataset.val)||0;
    r.style.display=r.innerText.toLowerCase().includes(q)&&y>=minY&&p<=maxP?'':'none';
  }
}
</script>"""


def fmt(val, suffix="", decimals=None):
    if val == BLANK or val is None:
        return f'<span style="color:#bbb">{BLANK}</span>'
    if decimals is not None:
        return f"{val:.{decimals}f}{suffix}"
    if isinstance(val, int):
        return f"{val:,}{suffix}".replace(",", "\u202f")
    return f"{val}{suffix}"


def yclass(y):
    if y == BLANK or y is None:
        return ""
    return "yield-high" if float(y) >= 12 else "yield-med" if float(y) >= 10 else ""


def export_html(listings: list, path: Path) -> None:
    headers = [
        "#", "Titre", "Ville", "Pièces", "Surface",
        "Prix net", "Frais notaire", "Coût total", "Prix/m²",
        "Loyer HC", "Loyer/m²", "Taxe foncière", "Charges/mois",
        "Rdt brut", "Rdt net", "Description", "Date scraping",
    ]
    ths = "".join(f'<th onclick="sortTable({i})">{h} ⇅</th>' for i, h in enumerate(headers))

    rows = []
    for i, l in enumerate(listings, 1):
        badge = '<span class="badge-new">NOUVEAU</span>' if l.get("nouveau") else ""
        tr_cls = " class='nouveau'" if l.get("nouveau") else ""

        def c(val, suf="", dec=None, dv=None):
            dval = dv if dv is not None else (val if val != BLANK else "")
            return f'<td data-val="{dval}">{fmt(val, suf, dec)}</td>'

        yb, yn = l["rendement_brut"], l["rendement_net"]
        rows.append(f"""<tr{tr_cls}>
<td>{i}</td>
<td><a href="{l['url']}" target="_blank">{str(l['titre'])[:50]}{badge}</a></td>
<td>{l['ville']}</td>
{c(l['nb_pieces'])}{c(l['surface_m2'],' m²')}
{c(l['prix_net'],' €',dv=l['prix_net'])}{c(l['frais_notaire'],' €')}{c(l['cout_total'],' €')}
{c(l['prix_m2'],' €/m²')}{c(l['loyer_hc'],' €/mois')}{c(l['loyer_m2'],' €/m²')}
{c(l['taxe_fonciere'],' €/an')}{c(l['charges_mois'],' €/mois')}
<td data-val="{yb if yb!=BLANK else ''}"><span class="{yclass(yb)}">{fmt(yb,'%',2)}</span></td>
<td data-val="{yn if yn!=BLANK else ''}"><span class="{yclass(yn)}">{fmt(yn,'%',2)}</span></td>
<td class="desc">{str(l['description'])[:200]}</td>
<td>{l.get('date_scraping',BLANK)}</td>
</tr>""")

    total = len(listings)
    bruts = [l["rendement_brut"] for l in listings if isinstance(l["rendement_brut"], float)]
    avg = round(sum(bruts) / len(bruts), 2) if bruts else 0
    nouveaux = sum(1 for l in listings if l.get("nouveau"))

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8">
<title>LeBonCoin — Analyse immobilière</title>
{HTML_STYLE}
</head>
<body>
<h1>LeBonCoin — Analyse immobilière</h1>
<div class="stats">
<strong>{total}</strong> bien(s) &nbsp;|&nbsp;
Rdt brut moyen : <strong>{avg}%</strong> &nbsp;|&nbsp;
Nouveaux : <strong>{nouveaux}</strong> &nbsp;|&nbsp;
{datetime.now().strftime("%d/%m/%Y %H:%M")}
</div>
<div class="filters">
<input type="text" id="search" placeholder="Rechercher…" oninput="filterTable()">
<input type="text" id="minYield" placeholder="Rdt min (%)" oninput="filterTable()" style="width:120px">
<input type="text" id="maxPrice" placeholder="Prix max (€)" oninput="filterTable()" style="width:120px">
<button onclick="filterTable()">Filtrer</button>
</div>
<table id="tbl">
<thead><tr>{ths}</tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
{HTML_SCRIPT}
</body></html>"""

    path.write_text(html, encoding="utf-8")
    print(f"  HTML : {path}")


# ── Export CSV ────────────────────────────────────────────────────

def export_csv(listings: list, path: Path) -> None:
    if not listings:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(listings[0].keys()))
        writer.writeheader()
        writer.writerows(listings)
    print(f"  CSV  : {path}")


# ── Notification WhatsApp ─────────────────────────────────────────

def send_whatsapp(message: str) -> None:
    if not WA_PHONE or not WA_APIKEY:
        return
    try:
        encoded = urllib.parse.quote(message)
        url = (
            f"https://api.callmebot.com/whatsapp.php"
            f"?phone={WA_PHONE}&text={encoded}&apikey={WA_APIKEY}"
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            print(f"  WhatsApp : {'OK' if resp.status == 200 else f'HTTP {resp.status}'}")
    except Exception as e:
        print(f"  WhatsApp : erreur ({e})")


# ── Orchestration principale ──────────────────────────────────────

async def main():
    print("=" * 60)
    print(" LeBonCoin Analyzer — Phase 1 MVP (CDP)")
    print("=" * 60)
    print(f" Filtres : prix <= {MAX_PRICE:,} EUR | rendement brut >= {MIN_YIELD}%")
    print(f" CDP     : {CDP_URL}")
    print()

    seen = load_seen()

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"ERREUR : impossible de se connecter a Chrome sur {CDP_URL}")
            print(f"  -> Lancez d'abord lancer_chrome.bat")
            print(f"  -> Detail : {e}")
            sys.exit(1)

        contexts = browser.contexts
        if not contexts:
            print("ERREUR : aucun contexte de navigateur trouve.")
            await browser.close()
            sys.exit(1)

        context = contexts[0]
        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        page_type = await detect_page_type(page)
        print(f" Page detectee : {page_type} ({page.url[:80]})")
        print()

        result_urls = []

        if page_type == "saved_searches":
            print(" Recuperation des recherches sauvegardees...")
            result_urls = await get_saved_search_urls(page)
            if not result_urls:
                print(" Aucune recherche trouvee.")
                print(" Naviguez manuellement vers une page de resultats, puis appuyez sur Entree...")
                input()
                result_urls = [page.url]
            else:
                print(f" {len(result_urls)} recherche(s) trouvee(s) :")
                for u in result_urls:
                    print(f"   * {u[:80]}")

        elif page_type == "results":
            result_urls = [page.url]
            print(f" Page de resultats active : {page.url[:80]}")

        else:
            print(" Page non reconnue.")
            print(" Naviguez vers une page de resultats LeBonCoin, puis appuyez sur Entree...")
            input()
            result_urls = [page.url]

        print()

        # Scraping — pas de navigation sur la page courante
        all_raw = []
        if page_type == "results" and result_urls and page.url == result_urls[0]:
            # Page déjà chargée par l'utilisateur : extraction directe sans goto()
            print(f"Extraction sans navigation (page déjà chargée)...")
            cards = await scrape_results_page(page)
            all_raw.extend(cards)
        else:
            # Recherches sauvegardées ou changement de page nécessaire
            for target_url in result_urls:
                print(f"Navigation vers : {target_url[:80]}")
                if page.url != target_url:
                    await page.goto(target_url, wait_until="networkidle")
                    await page.wait_for_timeout(2000)
                cards = await scrape_results_page(page)
                all_raw.extend(cards)

        print(f"  {len(all_raw)} lien(s) d'annonces trouvé(s) au total")

        print(f"\nTotal brut : {len(all_raw)}")
        print("Visite des details et filtrage...\n")

        # Dédupliquer
        seen_batch = {}
        unique_raw = []
        for l in all_raw:
            if l and l.get("url") and l["url"] not in seen_batch:
                seen_batch[l["url"]] = True
                unique_raw.append(l)

        enriched = []
        new_listings = []

        for i, card in enumerate(unique_raw, 1):
            url = card.get("url", "")
            print(f"  [{i}/{len(unique_raw)}] {url[:70]}")
            detail = await scrape_listing_detail(context, url)
            merged = {**card, **{k: v for k, v in detail.items() if v is not None and v != BLANK}}
            result = enrich(merged)
            if result is None:
                print("    x Hors criteres")
                continue

            is_new = url not in seen
            result["nouveau"] = is_new
            if is_new:
                new_listings.append(result)
                seen.add(url)

            enriched.append(result)
            print(f"    OK {result['prix_net']:,} EUR | loyer {result['loyer_hc']} EUR/mois | brut {result['rendement_brut']}%")

        enriched.sort(key=lambda x: x["rendement_brut"], reverse=True)

        print(f"\n{'='*60}")
        print(f" {len(enriched)} bien(s) retenu(s) | {len(new_listings)} nouveau(x)")
        print(f"{'='*60}\n")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = OUTPUT_DIR / f"rapport_{ts}.html"
        csv_path  = OUTPUT_DIR / f"rapport_{ts}.csv"

        export_html(enriched, html_path)
        export_csv(enriched, csv_path)

        if new_listings and WA_PHONE and WA_APIKEY:
            lines = [f"LeBonCoin — {len(new_listings)} nouveau(x) bien(s) >= {MIN_YIELD}% brut :"]
            for l in new_listings[:5]:
                lines.append(f"* {l['ville']} — {l['prix_net']:,}EUR — {l['rendement_brut']}% brut")
            if len(new_listings) > 5:
                lines.append(f"...et {len(new_listings)-5} autre(s). Voir rapport HTML.")
            send_whatsapp("\n".join(lines))

        save_seen(seen)

        print(f"\nRapport HTML : {html_path.resolve()}")
        print(f"CSV          : {csv_path.resolve()}")
        print("Termine.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
