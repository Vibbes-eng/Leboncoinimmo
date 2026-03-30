# LeBonCoin — Analyseur de Rentabilité Locative

Scanne automatiquement toutes vos recherches sauvegardées LeBonCoin et filtre les meilleurs investissements locatifs.

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
```

## Lancement

```bash
python leboncoin_analyzer.py
```

## Ce que fait le script

1. Se connecte à votre compte LeBonCoin
2. Récupère vos 6 recherches sauvegardées (Favoris → Recherches)
3. Parcourt toutes les pages de résultats
4. **Retient uniquement** les annonces avec un loyer mentionné explicitement
5. Calcule la **rentabilité brute** et la **rentabilité nette estimée**
6. Filtre par budget max (150 000 €) et rendement minimum (10%)
7. Génère un rapport HTML interactif + export CSV
8. Notifie les nouvelles annonces par **WhatsApp** (optionnel, gratuit via CallMeBot)

## Rapport HTML

Le rapport HTML permet de :
- Trier par rentabilité brute, nette, loyer, prix
- Filtrer par ville/texte
- Voir en priorité les biens **déjà loués** (badge vert)

## Configuration WhatsApp (gratuit)

1. Envoyez le message `I allow callmebot to send me messages` au **+34 644 44 06 66** sur WhatsApp
2. Vous recevrez votre `apikey` automatiquement
3. Remplissez `WA_PHONE` et `WA_APIKEY` dans `.env`

## Calcul rentabilité nette

```
Loyer annuel net = Loyer × 12
  − charges copropriété/entretien (15% du loyer annuel)
  − taxe foncière (~1 mois de loyer)
  − vacance locative (~0.5 mois)

Rentabilité nette = Loyer annuel net / Prix achat × 100
```

Ces hypothèses sont configurables dans `.env`.
