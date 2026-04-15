#!/usr/bin/env python3
"""
LeBonCoin Analyzer — v2 (Anti-bot renforcé + rentabilité ≥ 10%)
================================================================
1. Lancez Chrome via lancer_chrome.bat (port 9222)
2. Naviguez vers vos recherches sauvegardees OU une page de resultats
3. Lancez : python leboncoin_analyzer.py

Sortie : rapport HTML interactif + CSV + notification WhatsApp (optionnel)

Améliorations v2 :
- Stealth renforcé (canvas, WebGL, audio, timezone, hardware)
- Rotation des User-Agents Chrome réels
- Mouvements souris humains (courbes de Bézier)
- Scroll naturel à la lecture
- Cookies persistants entre sessions
- Détection captcha/blocage avec pause
- Extraction IA améliorée (loyer, rentabilité, estimation)
- Filtre strict ≥ 10% rentabilité brute
"""

import asyncio
import csv
import json
import math
import os
import random
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import anthropic as _anthropic
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ── Configuration ─────────────────────────────────────────────────
load_dotenv()

CDP_PORT        = int(os.getenv("CDP_PORT", "9222"))
MIN_YIELD       = float(os.getenv("MIN_YIELD", "10.0"))
MAX_PRICE       = int(os.getenv("MAX_PRICE", "150000"))
WA_PHONE        = os.getenv("WA_PHONE", "").strip()
WA_APIKEY       = os.getenv("WA_APIKEY", "").strip()
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY", "").strip()

CDP_URL        = f"http://localhost:{CDP_PORT}"
SEEN_FILE      = Path("seen_urls.json")
COOKIES_FILE   = Path("cookies.json")
OUTPUT_DIR     = Path(".")

BLANK = "—"


# ── User-Agents Chrome réels (rotation) ───────────────────────────

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.184 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.225 Safari/537.36",
]

_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
]


# ── Stealth JS v2 — fingerprint complet ───────────────────────────

_STEALTH_JS = """
(function() {
  'use strict';

  // 1. webdriver flag
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

  // 2. Plugins natifs
  const makePlugin = (name, desc, filename) => {
    const plugin = Object.create(Plugin.prototype);
    Object.defineProperty(plugin, 'name', {get: () => name});
    Object.defineProperty(plugin, 'description', {get: () => desc});
    Object.defineProperty(plugin, 'filename', {get: () => filename});
    Object.defineProperty(plugin, 'length', {get: () => 0});
    return plugin;
  };
  const plugins = [
    makePlugin('Chrome PDF Plugin', 'Portable Document Format', 'internal-pdf-viewer'),
    makePlugin('Chrome PDF Viewer', '', 'mhjfbmdgcfjbbpaeojofohoefgiehjai'),
    makePlugin('Native Client', '', 'internal-nacl-plugin'),
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const arr = [...plugins];
      arr.__proto__ = PluginArray.prototype;
      Object.defineProperty(arr, 'item', {value: (i) => arr[i]});
      Object.defineProperty(arr, 'namedItem', {value: (n) => arr.find(p => p.name === n)});
      return arr;
    }
  });

  // 3. Langues françaises
  Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR', 'fr', 'en-US', 'en']});

  // 4. Chrome runtime object
  if (!window.chrome) {
    window.chrome = {
      runtime: {
        connect: () => {},
        sendMessage: () => {},
        onMessage: {addListener: () => {}, removeListener: () => {}},
        id: 'chrome-extension'
      },
      loadTimes: () => ({
        requestTime: Date.now() / 1000 - Math.random() * 0.3,
        startLoadTime: Date.now() / 1000 - Math.random() * 0.5,
        commitLoadTime: Date.now() / 1000 - Math.random() * 0.2,
        finishDocumentLoadTime: Date.now() / 1000,
        finishLoadTime: Date.now() / 1000 + Math.random() * 0.1,
        firstPaintTime: Date.now() / 1000,
        firstPaintAfterLoadTime: 0,
        navigationType: 'Other',
        wasFetchedViaSpdy: false,
        wasNpnNegotiated: true,
        npnNegotiatedProtocol: 'h2',
        wasAlternateProtocolAvailable: false,
        connectionInfo: 'h2'
      }),
      csi: () => ({
        startE: Date.now(),
        onloadT: Date.now() + Math.floor(Math.random() * 500 + 200),
        pageT: Math.random() * 1000 + 500,
        tran: 15
      }),
      app: {}
    };
  }

  // 5. Permissions API
  try {
    const origQuery = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = (params) => {
      if (params.name === 'notifications') {
        return Promise.resolve({state: Notification.permission, onchange: null});
      }
      return origQuery(params);
    };
  } catch(e) {}

  // 6. Supprimer les propriétés CDP
  ['cdc_adoQpoasnfa76pfcZLmcfl_Array',
   'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
   'cdc_adoQpoasnfa76pfcZLmcfl_Symbol',
   '__playwright_target__',
   '__pw_manual',
   '__PW_inspect'].forEach(k => { try { delete window[k]; } catch(e) {} });

  // 7. Canvas fingerprint réaliste (bruit minimal)
  const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type) {
    if (type === 'image/png' && this.width === 1 && this.height === 1) {
      return origToDataURL.apply(this, arguments);
    }
    const ctx = this.getContext('2d');
    if (ctx) {
      const imgData = ctx.getImageData(0, 0, 1, 1);
      imgData.data[0] = Math.max(0, imgData.data[0] - 1);
      ctx.putImageData(imgData, 0, 0);
    }
    return origToDataURL.apply(this, arguments);
  };

  // 8. WebGL fingerprint
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel(R) Iris(TM) Plus Graphics 640';
    return getParameter.apply(this, [parameter]);
  };
  try {
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel(R) Iris(TM) Plus Graphics 640';
      return getParameter2.apply(this, [parameter]);
    };
  } catch(e) {}

  // 9. AudioContext fingerprint
  try {
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function(channel) {
      const data = origGetChannelData.call(this, channel);
      if (data.length > 100) {
        data[0] = data[0] + 0.0000001 * (Math.random() - 0.5);
      }
      return data;
    };
  } catch(e) {}

  // 10. Navigator hardware concurrency réaliste
  Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
  Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
  Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
  Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});

  // 11. Screen réaliste (correspond au viewport choisi)
  Object.defineProperty(screen, 'colorDepth', {get: () => 24});
  Object.defineProperty(screen, 'pixelDepth', {get: () => 24});

  // 12. Timezone Europe/Paris
  const origDateGetTimezoneOffset = Date.prototype.getTimezoneOffset;
  Date.prototype.getTimezoneOffset = function() { return -60; };

  // 13. Battery API simulée
  if (navigator.getBattery) {
    navigator.getBattery = () => Promise.resolve({
      charging: true, chargingTime: 0,
      dischargingTime: Infinity, level: 0.98,
      addEventListener: () => {}, removeEventListener: () => {}
    });
  }

})();
"""


async def apply_stealth(context, ua: str = None, viewport: dict = None) -> None:
    """
    Stealth v2 : init script + headers complets + UA + viewport.
    """
    chosen_ua = ua or random.choice(_USER_AGENTS)
    chosen_vp = viewport or random.choice(_VIEWPORTS)

    await context.add_init_script(_STEALTH_JS)

    # Version Chrome extraite du UA
    ver_match = re.search(r"Chrome/(\d+)", chosen_ua)
    chrome_ver = ver_match.group(1) if ver_match else "124"

    await context.set_extra_http_headers({
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "sec-ch-ua": f'"Google Chrome";v="{chrome_ver}", "Chromium";v="{chrome_ver}", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"10.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": chosen_ua,
        "DNT": "1",
    })

    print(f"  [Stealth] UA: Chrome/{chrome_ver} | Viewport: {chosen_vp['width']}x{chosen_vp['height']}")


# ── Mouvements souris humains ─────────────────────────────────────

async def human_mouse_move(page, target_x: int = None, target_y: int = None) -> None:
    """
    Déplace la souris vers une cible en simulant une courbe de Bézier humaine.
    Si pas de cible, mouvement aléatoire naturel.
    """
    vp = page.viewport_size or {"width": 1366, "height": 768}
    w, h = vp["width"], vp["height"]

    # Position de départ aléatoire
    start_x = random.randint(100, w - 100)
    start_y = random.randint(100, h - 100)

    end_x = target_x or random.randint(200, w - 200)
    end_y = target_y or random.randint(200, h - 200)

    # Points de contrôle Bézier
    cp1x = start_x + (end_x - start_x) * random.uniform(0.2, 0.4) + random.randint(-80, 80)
    cp1y = start_y + (end_y - start_y) * random.uniform(0.1, 0.3) + random.randint(-80, 80)
    cp2x = start_x + (end_x - start_x) * random.uniform(0.6, 0.8) + random.randint(-80, 80)
    cp2y = start_y + (end_y - start_y) * random.uniform(0.7, 0.9) + random.randint(-80, 80)

    steps = random.randint(15, 30)
    for i in range(steps + 1):
        t = i / steps
        # Courbe de Bézier cubique
        x = (1-t)**3*start_x + 3*(1-t)**2*t*cp1x + 3*(1-t)*t**2*cp2x + t**3*end_x
        y = (1-t)**3*start_y + 3*(1-t)**2*t*cp1y + 3*(1-t)*t**2*cp2y + t**3*end_y
        await page.mouse.move(int(x), int(y))
        await page.wait_for_timeout(random.randint(10, 35))


async def human_scroll(page, direction: str = "down") -> None:
    """Scroll naturel avec accélération/décélération."""
    total = random.randint(300, 800)
    chunks = random.randint(4, 9)
    per_chunk = total // chunks
    for _ in range(chunks):
        delta = per_chunk + random.randint(-30, 30)
        await page.mouse.wheel(0, delta if direction == "down" else -delta)
        await page.wait_for_timeout(random.randint(80, 220))


def human_delay(min_ms: int = 800, max_ms: int = 2500) -> float:
    """Délai aléatoire avec distribution non uniforme (plus probable vers le milieu)."""
    # Distribution bêta pour simuler les réactions humaines
    beta_sample = random.betavariate(2, 3)
    return int(min_ms + beta_sample * (max_ms - min_ms))


# ── Persistance cookies ───────────────────────────────────────────

async def save_cookies(context) -> None:
    try:
        cookies = await context.cookies()
        COOKIES_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  [Cookies] Sauvegarde échouée : {e}")


async def load_cookies(context) -> None:
    if not COOKIES_FILE.exists():
        return
    try:
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        # Filtrer les cookies expirés
        now = datetime.now().timestamp()
        valid = [c for c in cookies if c.get("expires", 0) == -1 or c.get("expires", 0) > now]
        if valid:
            await context.add_cookies(valid)
            print(f"  [Cookies] {len(valid)} cookie(s) restauré(s)")
    except Exception as e:
        print(f"  [Cookies] Chargement échoué : {e}")


# ── Détection captcha/blocage ─────────────────────────────────────

async def detect_and_handle_block(page) -> bool:
    """
    Détecte les pages Cloudflare, captcha, ou blocage.
    Retourne True si bloqué (avec pause pour résolution manuelle).
    """
    url = page.url
    title = await page.title()
    content = await page.content()

    block_signals = [
        "captcha" in url.lower(),
        "challenge" in url.lower(),
        "cloudflare" in content.lower() and "checking" in title.lower(),
        "just a moment" in title.lower(),
        "veuillez patienter" in title.lower(),
        "accès refusé" in title.lower(),
        "access denied" in title.lower(),
        'id="challenge-form"' in content,
        "cf-browser-verification" in content,
        "__cf_chl" in content,
        "robot" in content.lower() and "vérifier" in content.lower(),
        "recaptcha" in content.lower(),
    ]

    if any(block_signals):
        print("\n  ⚠️  BLOCAGE DÉTECTÉ (captcha/Cloudflare)")
        print("  → Résolvez le captcha manuellement dans Chrome")
        print("  → Appuyez sur ENTRÉE quand c'est fait...")
        input()
        await page.wait_for_load_state("networkidle")
        # Sauvegarder les cookies après résolution
        return True

    return False


# ── Persistance URLs vues ─────────────────────────────────────────

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


# ── Parsing ───────────────────────────────────────────────────────

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


# ── Extraction IA v2 — prompt enrichi pour loyer + rentabilité ───

_AI_PROMPT = """\
Tu analyses une annonce immobilière française pour un investisseur cherchant une rentabilité brute ≥ {min_yield}%.

Prix affiché dans l'annonce : {price_hint}
Surface approximative : {surface_hint}
Localisation : {location_hint}

Description complète :
{description}

RÈGLES CRITIQUES D'EXTRACTION :
1. Prix de VENTE ≠ loyer — ne jamais confondre.
2. "740 x 12 = 8 880€" → loyer = 740€/mois (x12 = annualisation).
3. CC = charges comprises → loyer_cc. HC = hors charges → loyer_hc.
4. Si loyer_cc et charges connues : loyer_hc = loyer_cc - charges.
5. Immeuble de rapport / plusieurs lots : additionne TOUS les loyers.
6. Cherche aussi : "rapport locatif", "déjà loué", "bail en cours", "revenus locatifs".
7. Si description parle de loyer potentiel/estimé/marché → loyer_potentiel (ne pas mettre dans loyer_hc).
8. Ne JAMAIS inventer de données manquantes → mettre null.
9. Si plusieurs loyers listés (ex: lot 1: 450€, lot 2: 380€) → les additionner pour loyer_hc.

CALCUL RENTABILITÉ (informatif seulement — pas pour inventer des données) :
- Rentabilité brute = (loyer_hc * 12) / (prix + frais_notaire) * 100
- Frais de notaire anciens ≈ 8% du prix
- Si rentabilité calculée < {min_yield}%, signaler dans notes.

Retourne UNIQUEMENT ce JSON (sans texte autour) :
{{
  "loyer_hc": <entier mensuel HC, ou null>,
  "loyer_cc": <entier mensuel CC, ou null>,
  "charges_mois": <charges mensuelles entier, ou null>,
  "taxe_fonciere": <taxe foncière annuelle entier, ou null>,
  "loyer_potentiel": <loyer marché estimé si mentionné, ou null>,
  "nb_lots": <nombre de logements (immeuble de rapport), sinon 1>,
  "loyer_detail": <liste des loyers individuels si >1 lot, sinon null>,
  "loyer_source": "<'hc'|'cc'|'cc_moins_charges'|'calcule'|'potentiel'|'inconnu'>",
  "deja_loue": <true si déjà occupé par un locataire, false sinon>,
  "bail_type": "<'vide'|'meuble'|'commercial'|'inconnu'>",
  "rentabilite_brute_estimee": <float ou null — calculé si données suffisantes>,
  "notes": "<explication courte en 1-2 lignes>"
}}"""


async def ai_extract_financials(description: str, price: int = None,
                                 surface: float = None, location: str = None) -> dict:
    if not ANTHROPIC_KEY or not description or description == BLANK:
        return {}

    price_hint    = f"{price:,}€".replace(",", " ") if price else "non précisé"
    surface_hint  = f"{surface} m²" if surface else "non précisée"
    location_hint = location or "non précisée"

    prompt = _AI_PROMPT.format(
        min_yield=MIN_YIELD,
        price_hint=price_hint,
        surface_hint=surface_hint,
        location_hint=location_hint,
        description=description[:3000],
    )

    try:
        client = _anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"    [AI] Erreur : {e}")

    return {}


# ── Fallbacks regex ───────────────────────────────────────────────

def _regex_fallback_loyer(text: str):
    if not text:
        return None
    patterns = [
        r"loyer\s+hors\s+charges[^\d]*(\d[\d\s]{2,5})",
        r"loyer\s+hc[^\d]*(\d[\d\s]{2,5})",
        r"loyer[^\d]*(\d{3,4})\s*[€e](?:\s*/\s*mois)?",
        r"(\d{3,4})\s*[€e]\s*/\s*mois",
        r"rapport\s+locatif[^\d]*(\d[\d\s]{2,5})[€e]",
        r"revenus?\s+locatifs?[^\d]*(\d[\d\s]{2,5})[€e]",
        r"loue[ée]?\s+\w*[^\d]*(\d{3,4})\s*[€e]",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = int(re.sub(r"\s", "", m.group(1)))
                if 200 <= val <= 5000:  # fourchette réaliste pour un loyer
                    return val
            except ValueError:
                pass
    return None


def _regex_fallback_taxe(text: str):
    if not text:
        return None
    m = re.search(r"taxe\s+fonci[eè]re[^\d]*(\d[\d\s]*)\s*[€e]", text, re.IGNORECASE)
    if m:
        try:
            return int(re.sub(r"\s", "", m.group(1)))
        except ValueError:
            pass
    return None


def _regex_fallback_charges(text: str):
    if not text:
        return None
    patterns = [
        r"charges[^\d]*(\d{2,3})\s*[€e]\s*/\s*mois",
        r"charges\s+mensuelles[^\d]*(\d{2,3})",
        r"charges\s*:\s*(\d{2,3})\s*[€e]",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return int(re.sub(r"\s", "", m.group(1)))
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


def loyer_min_pour_rendement(prix_net: int, target_yield: float = None) -> int:
    """Calcule le loyer mensuel minimum pour atteindre le rendement cible."""
    target = target_yield or MIN_YIELD
    fn = frais_notaire(prix_net)
    total = prix_net + fn
    return math.ceil(total * target / 100 / 12)


# ── Extraction des liens d'annonces ──────────────────────────────

async def get_ad_links_from_page(page) -> list:
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
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(human_delay(800, 1500))

    # Scroll naturel pour simuler la lecture
    await human_scroll(page, "down")
    await page.wait_for_timeout(human_delay(400, 800))
    await human_scroll(page, "down")
    await page.wait_for_timeout(human_delay(300, 600))

    all_links = []
    page_num = 1

    while True:
        # Mouvement de souris naturel
        await human_mouse_move(page)

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
            # Scroll vers le bouton avant de cliquer
            await next_btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(human_delay(400, 900))
            await next_btn.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(human_delay(1500, 3000))
        except Exception as e:
            print(f"  [WARN] Pagination page {page_num}: {e}")
            break

        if page.url == prev_url:
            break

        # Vérifier blocage après navigation
        await detect_and_handle_block(page)
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
        "deja_loue": False,
        "bail_type": "inconnu",
        "loyer_potentiel": None,
    }
    page = await context.new_page()
    try:
        await page.wait_for_timeout(human_delay(700, 1800))

        # Mouvement souris avant navigation
        await human_mouse_move(page)

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(human_delay(1000, 2500))

        # Vérifier le blocage
        blocked = await detect_and_handle_block(page)
        if blocked:
            # Réessayer après résolution manuelle
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(human_delay(1500, 3000))

        # Scroll naturel pour simuler la lecture de l'annonce
        await human_scroll(page, "down")
        await page.wait_for_timeout(human_delay(500, 1200))
        await human_scroll(page, "down")
        await page.wait_for_timeout(human_delay(300, 800))

        # Titre
        for sel in ["h1", "[data-qa-id='ad_title']", "[data-test-id='ad-title']"]:
            el = await page.query_selector(sel)
            if el:
                data["title"] = _text(await el.inner_text())
                break

        # Prix
        for sel in [
            "[data-qa-id='adview_price']",
            "[data-test-id='ad-price']",
            "[class*='price']",
            "span[class*='Price']",
        ]:
            el = await page.query_selector(sel)
            if el:
                data["price"] = parse_price(_text(await el.inner_text()))
                if data["price"]:
                    break

        # Localisation
        for sel in [
            "[data-qa-id='adview_location_informations']",
            "[data-test-id='ad-location']",
            "[class*='location']",
            "[class*='Location']",
        ]:
            el = await page.query_selector(sel)
            if el:
                data["location"] = _text(await el.inner_text()).split("\n")[0]
                break

        # Description
        for sel in [
            "[data-qa-id='adview_description_container']",
            "[data-test-id='ad-description']",
            "[class*='description']",
            "[class*='Description']",
            "div[itemprop='description']",
        ]:
            el = await page.query_selector(sel)
            if el:
                data["description"] = _text(await el.inner_text())
                break

        # Attributs (surface, pièces)
        attr_els = await page.query_selector_all(
            "[data-qa-id='criteria_item'], [data-test-id='criteria-item'], "
            "[class*='criteria'], [class*='attribute'], [class*='Criteria']"
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

        # ── Extraction IA (prioritaire) ──────────────────────────────
        ai = await ai_extract_financials(
            full_text,
            data.get("price"),
            data.get("surface"),
            data.get("location"),
        )
        if ai:
            loyer_hc     = ai.get("loyer_hc")
            loyer_cc     = ai.get("loyer_cc")
            charges_ai   = ai.get("charges_mois")
            loyer_pot    = ai.get("loyer_potentiel")

            if loyer_hc:
                data["loyer"] = loyer_hc
            elif loyer_cc and charges_ai:
                data["loyer"] = loyer_cc - charges_ai
            elif loyer_cc:
                data["loyer"] = loyer_cc

            # Si pas de loyer courant mais loyer potentiel mentionné
            if data["loyer"] is None and loyer_pot:
                data["loyer"] = loyer_pot
                ai["loyer_source"] = "potentiel"

            if ai.get("taxe_fonciere"):
                data["taxe_fonciere"] = ai["taxe_fonciere"]
            if charges_ai:
                data["charges"] = charges_ai

            data["nb_lots"]        = ai.get("nb_lots", 1) or 1
            data["loyer_detail"]   = ai.get("loyer_detail")
            data["loyer_source"]   = ai.get("loyer_source", "inconnu")
            data["ai_notes"]       = ai.get("notes", "")
            data["deja_loue"]      = ai.get("deja_loue", False)
            data["bail_type"]      = ai.get("bail_type", "inconnu")
            data["loyer_potentiel"] = loyer_pot

            if data["loyer"]:
                src = data["loyer_source"]
                rdt = ai.get("rentabilite_brute_estimee", "")
                print(f"    [AI] loyer={data['loyer']}€/mois ({src})"
                      + (f" | rdt≈{rdt}%" if rdt else "")
                      + (f" | {data['nb_lots']} lots" if data["nb_lots"] > 1 else "")
                      + (f" | {str(data['ai_notes'])[:60]}" if data["ai_notes"] else ""))
        else:
            # ── Fallback regex ────────────────────────────────────────
            if data["loyer"] is None:
                data["loyer"] = _regex_fallback_loyer(full_text)
            if data["taxe_fonciere"] is None:
                data["taxe_fonciere"] = _regex_fallback_taxe(full_text)
            if data["charges"] is None:
                data["charges"] = _regex_fallback_charges(full_text)
            data["nb_lots"]      = 1
            data["loyer_detail"] = None
            data["loyer_source"] = "regex"
            data["ai_notes"]     = ""

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
        # Calculer le loyer minimum nécessaire pour infomer l'utilisateur
        loyer_min = loyer_min_pour_rendement(price)
        print(f"    → Loyer non trouvé. Loyer min pour {MIN_YIELD}% brut : {loyer_min}€/mois")
        return None

    fn         = frais_notaire(price)
    total_cost = price + fn
    yield_brut = calc_yield_brut(loyer, price)

    if yield_brut is None or yield_brut < MIN_YIELD:
        if yield_brut is not None:
            loyer_min = loyer_min_pour_rendement(price)
            print(f"    → Rdt brut {yield_brut}% < {MIN_YIELD}% (loyer min : {loyer_min}€/mois)")
        return None

    taxe      = listing.get("taxe_fonciere")
    charges   = listing.get("charges")
    yield_net = calc_yield_net(loyer, price, taxe, charges)

    surface   = listing.get("surface")
    price_m2  = round(price / surface, 0) if surface else None
    loyer_m2  = round(loyer / surface, 2) if surface else None

    nb_lots      = listing.get("nb_lots", 1) or 1
    loyer_detail = listing.get("loyer_detail")
    loyer_source = listing.get("loyer_source", BLANK)
    ai_notes     = listing.get("ai_notes", "")
    deja_loue    = listing.get("deja_loue", False)
    bail_type    = listing.get("bail_type", "inconnu")

    source_labels = {
        "hc":              "HC extrait",
        "cc_moins_charges":"CC − charges",
        "cc":              "CC (charges incluses)",
        "calcule":         "Calculé",
        "potentiel":       "Potentiel estimé",
        "regex":           "Regex (sans IA)",
        "inconnu":         BLANK,
    }
    loyer_label = source_labels.get(loyer_source, loyer_source)

    lots_str = BLANK
    if loyer_detail and isinstance(loyer_detail, list) and len(loyer_detail) > 1:
        lots_str = " + ".join(f"{v}€" for v in loyer_detail) + f" = {loyer}€"

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
        "nb_lots":         nb_lots if nb_lots > 1 else BLANK,
        "loyer_hc":        loyer,
        "loyer_detail":    lots_str,
        "loyer_source":    loyer_label,
        "loyer_m2":        loyer_m2 or BLANK,
        "taxe_fonciere":   taxe or BLANK,
        "charges_mois":    charges or BLANK,
        "rendement_brut":  yield_brut,
        "rendement_net":   yield_net or BLANK,
        "deja_loue":       "Oui" if deja_loue else "Non",
        "bail_type":       bail_type if bail_type != "inconnu" else BLANK,
        "ai_notes":        ai_notes[:120] if ai_notes else BLANK,
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
.badge-loue{background:#2d6a4f;color:#fff;padding:2px 7px;border-radius:10px;
            font-size:11px;font-weight:bold;margin-left:4px}
.yield-top{color:#1a5c35;font-weight:bold;font-size:14px}
.yield-high{color:#2d6a4f;font-weight:bold}
.yield-med{color:#e07800;font-weight:bold}
input[type=text]{padding:6px 10px;border:1px solid #ccc;border-radius:4px;
                 width:220px;margin-right:8px}
button{padding:6px 14px;border:none;border-radius:4px;cursor:pointer;
       background:#e63946;color:#fff}
button:hover{background:#c1121f}
.filters{margin-bottom:12px;background:#fff;padding:12px;border-radius:8px;
         box-shadow:0 1px 4px rgba(0,0,0,.1)}
a{color:#e63946;text-decoration:none}
a:hover{text-decoration:underline}
.desc{color:#555;font-size:12px;max-width:280px}
.badge-potentiel{background:#9b59b6;color:#fff;padding:1px 5px;border-radius:8px;font-size:10px}
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
  const onlyLoue=document.getElementById('onlyLoue').checked;
  for(const r of document.getElementById('tbl').tBodies[0].rows){
    const y=parseFloat(r.cells[16].dataset.val)||0;
    const p=parseFloat(r.cells[5].dataset.val)||0;
    const loue=r.cells[18].innerText.includes('Oui');
    const match=r.innerText.toLowerCase().includes(q)&&y>=minY&&p<=maxP;
    r.style.display=(match&&(!onlyLoue||loue))?'':'none';
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
    yf = float(y)
    if yf >= 15:
        return "yield-top"
    if yf >= 12:
        return "yield-high"
    if yf >= 10:
        return "yield-med"
    return ""


def export_html(listings: list, path: Path) -> None:
    headers = [
        "#", "Titre", "Ville", "Pièces", "Surface",
        "Prix net", "Frais notaire", "Coût total", "Prix/m²",
        "Lots", "Loyer HC", "Détail loyers", "Source loyer", "Loyer/m²",
        "Taxe foncière", "Charges/mois",
        "Rdt brut ≥10%", "Rdt net",
        "Déjà loué", "Type bail",
        "Notes IA", "Description", "Date scraping",
    ]
    ths = "".join(f'<th onclick="sortTable({i})">{h} ⇅</th>' for i, h in enumerate(headers))

    rows = []
    for i, l in enumerate(listings, 1):
        badge_new  = '<span class="badge-new">NOUVEAU</span>' if l.get("nouveau") else ""
        badge_loue = '<span class="badge-loue">LOUÉ</span>' if l.get("deja_loue") == "Oui" else ""
        tr_cls = " class='nouveau'" if l.get("nouveau") else ""

        def c(val, suf="", dec=None, dv=None):
            dval = dv if dv is not None else (val if val != BLANK else "")
            return f'<td data-val="{dval}">{fmt(val, suf, dec)}</td>'

        yb, yn = l["rendement_brut"], l["rendement_net"]

        src = l.get("loyer_source", BLANK)
        src_colors = {
            "HC extrait": "#2d6a4f",
            "CC − charges": "#1a6ba0",
            "CC (charges incluses)": "#e07800",
            "Potentiel estimé": "#9b59b6",
            "Regex (sans IA)": "#888",
        }
        src_color = src_colors.get(src, "#555")
        src_badge = f'<span style="font-size:10px;color:{src_color};font-weight:bold">{src}</span>' if src != BLANK else fmt(BLANK)

        rows.append(f"""<tr{tr_cls}>
<td>{i}</td>
<td><a href="{l['url']}" target="_blank">{str(l['titre'])[:50]}{badge_new}{badge_loue}</a></td>
<td>{l['ville']}</td>
{c(l['nb_pieces'])}{c(l['surface_m2'],' m²')}
{c(l['prix_net'],' €',dv=l['prix_net'])}{c(l['frais_notaire'],' €')}{c(l['cout_total'],' €')}
{c(l['prix_m2'],' €/m²')}
{c(l['nb_lots'])}
{c(l['loyer_hc'],' €/mois')}
<td class="desc">{l.get('loyer_detail', BLANK) if l.get('loyer_detail',BLANK)!=BLANK else fmt(BLANK)}</td>
<td>{src_badge}</td>
{c(l['loyer_m2'],' €/m²')}
{c(l['taxe_fonciere'],' €/an')}{c(l['charges_mois'],' €/mois')}
<td data-val="{yb if yb!=BLANK else ''}"><span class="{yclass(yb)}">{fmt(yb,'%',2)}</span></td>
<td data-val="{yn if yn!=BLANK else ''}"><span class="{yclass(yn)}">{fmt(yn,'%',2)}</span></td>
<td>{l.get('deja_loue', BLANK)}</td>
<td>{l.get('bail_type', BLANK)}</td>
<td class="desc" style="font-size:11px;color:#666">{l.get('ai_notes',BLANK) if l.get('ai_notes',BLANK)!=BLANK else fmt(BLANK)}</td>
<td class="desc">{str(l['description'])[:200]}</td>
<td>{l.get('date_scraping',BLANK)}</td>
</tr>""")

    total  = len(listings)
    bruts  = [l["rendement_brut"] for l in listings if isinstance(l["rendement_brut"], float)]
    avg    = round(sum(bruts) / len(bruts), 2) if bruts else 0
    top    = max(bruts) if bruts else 0
    nouveaux = sum(1 for l in listings if l.get("nouveau"))
    loues    = sum(1 for l in listings if l.get("deja_loue") == "Oui")

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8">
<title>LeBonCoin — Analyse immobilière ≥{MIN_YIELD}% brut</title>
{HTML_STYLE}
</head>
<body>
<h1>LeBonCoin — Biens rentables ≥ {MIN_YIELD}% brut</h1>
<div class="stats">
<strong>{total}</strong> bien(s) &nbsp;|&nbsp;
Rdt brut moyen : <strong>{avg}%</strong> &nbsp;|&nbsp;
Meilleur : <strong class="yield-top">{top}%</strong> &nbsp;|&nbsp;
Nouveaux : <strong>{nouveaux}</strong> &nbsp;|&nbsp;
Déjà loués : <strong>{loues}</strong> &nbsp;|&nbsp;
{datetime.now().strftime("%d/%m/%Y %H:%M")}
</div>
<div class="filters">
<input type="text" id="search" placeholder="Rechercher…" oninput="filterTable()">
<input type="text" id="minYield" placeholder="Rdt min (%)" oninput="filterTable()" style="width:120px" value="{MIN_YIELD}">
<input type="text" id="maxPrice" placeholder="Prix max (€)" oninput="filterTable()" style="width:130px" value="{MAX_PRICE}">
<label style="margin-left:12px"><input type="checkbox" id="onlyLoue" onchange="filterTable()"> Déjà loué uniquement</label>
<button onclick="filterTable()" style="margin-left:8px">Filtrer</button>
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
    print(" LeBonCoin Analyzer v2 — Anti-bot renforcé")
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

        # Charger les cookies persistants
        await load_cookies(context)

        # Stealth avec UA et viewport aléatoires
        ua  = random.choice(_USER_AGENTS)
        vp  = random.choice(_VIEWPORTS)
        await apply_stealth(context, ua, vp)

        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        # Vérifier si la page est bloquée au démarrage
        await detect_and_handle_block(page)

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

        # Scraping
        all_raw = []
        if page_type == "results" and result_urls and page.url == result_urls[0]:
            print(f"Extraction sans navigation (page deja chargee)...")
            cards = await scrape_results_page(page)
            all_raw.extend(cards)
        else:
            for target_url in result_urls:
                print(f"Navigation vers : {target_url[:80]}")
                if page.url != target_url:
                    await page.goto(target_url, wait_until="networkidle")
                    await page.wait_for_timeout(human_delay(2000, 4000))
                    await detect_and_handle_block(page)
                cards = await scrape_results_page(page)
                all_raw.extend(cards)

        # Sauvegarder les cookies après scraping de la liste
        await save_cookies(context)

        print(f"  {len(all_raw)} lien(s) d'annonces trouve(s) au total")
        print(f"\nTotal brut : {len(all_raw)}")
        print("Visite des details et filtrage...\n")

        # Dédupliquer
        seen_batch = {}
        unique_raw = []
        for l in all_raw:
            if l and l.get("url") and l["url"] not in seen_batch:
                seen_batch[l["url"]] = True
                unique_raw.append(l)

        enriched     = []
        new_listings = []

        for i, card in enumerate(unique_raw, 1):
            url = card.get("url", "")
            print(f"  [{i}/{len(unique_raw)}] {url[:70]}")

            # Pause humaine longue toutes les 8 annonces
            if i > 1 and i % 8 == 0:
                pause = random.randint(4000, 9000)
                print(f"    [pause {pause//1000}s — comportement humain]")
                await page.wait_for_timeout(pause)

            # Pause aléatoire très longue toutes les 25 annonces (simuler une pause café)
            if i > 1 and i % 25 == 0:
                pause = random.randint(15000, 30000)
                print(f"    [pause longue {pause//1000}s — simulation pause]")
                await page.wait_for_timeout(pause)

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
            loue_str = " [LOUE]" if result.get("deja_loue") == "Oui" else ""
            print(f"    OK {result['prix_net']:,} EUR | loyer {result['loyer_hc']} EUR/mois | brut {result['rendement_brut']}%{loue_str}")

        # Sauvegarder les cookies à la fin
        await save_cookies(context)

        enriched.sort(key=lambda x: x["rendement_brut"], reverse=True)

        print(f"\n{'='*60}")
        print(f" {len(enriched)} bien(s) retenu(s) >= {MIN_YIELD}% brut | {len(new_listings)} nouveau(x)")
        print(f"{'='*60}\n")

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = OUTPUT_DIR / f"rapport_{ts}.html"
        csv_path  = OUTPUT_DIR / f"rapport_{ts}.csv"

        export_html(enriched, html_path)
        export_csv(enriched, csv_path)

        if new_listings and WA_PHONE and WA_APIKEY:
            lines = [f"LeBonCoin — {len(new_listings)} nouveau(x) bien(s) >= {MIN_YIELD}% brut :"]
            for l in new_listings[:5]:
                loue = " [LOUE]" if l.get("deja_loue") == "Oui" else ""
                lines.append(f"* {l['ville']} — {l['prix_net']:,}EUR — {l['rendement_brut']}% brut{loue}")
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
