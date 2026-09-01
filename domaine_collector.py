import re
from playwright.async_api import async_playwright


LOT_URL = "https://encheres-domaine.gouv.fr/lot/audiq7-1-doo-1.html"


def parse_domaine_text(text):
    clean = text.replace("\xa0", " ")

    def extract(pattern):
        match = re.search(pattern, clean, re.IGNORECASE)
        return match.group(1).strip() if match else None

    bid = extract(r"Enchère en cours\s+(\d[\d ]*)\s*€")
    mileage = extract(r"Kilométrage\s+(\d[\d ]*)")
    first_date = extract(
        r"Date de 1ère mise en circulation\s+(\d{2}/\d{2}/\d{4})"
    )

    risk_flags = []

    checks = {
        "mileage_not_guaranteed": "km non garantis",
        "missing_original_registration": "absence carte grise originale",
        "moldy_interior": "intérieur moisi",
        "particle_filter_fault": "filtres particules hs",
        "body_damage": "coups, chocs, rayures",
        "general_wear": "vétusté générale",
    }

    lower_text = clean.lower()

    for flag, phrase in checks.items():
        if phrase in lower_text:
            risk_flags.append(flag)

    return {
        "brand": extract(r"Marque Véhicule\s+([^\n]+)"),
        "model": extract(r"Modèle Véhicule\s+([^\n]+)"),
        "year": int(first_date[-4:]) if first_date else None,
        "mileage_km": int(mileage.replace(" ", "")) if mileage else None,
        "fuel_type": extract(r"Energie / carburant\s+([^\n]+)"),
        "transmission": extract(r"Type de boîte\s+([^\n]+)"),
        "current_bid": int(bid.replace(" ", "")) if bid else None,
        "auction_fee_pct": 11,
        "reserved_for_pros": "RÉSERVÉ AUX PROS" in clean.upper(),
        "registration_certificate": extract(
            r"Certificat d'immatriculation\s+([^\n]+)"
        ),
        "has_key": extract(
            r"Présence d'au moins une clé\s+([^\n]+)"
        ),
        "location": extract(r"Dépôt\s*:\s*([^\n]+)"),
        "risk_flags": risk_flags,
    }


async def collect_domaine_lot(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
        )

        page = await context.new_page()

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        try:
            await page.wait_for_selector(
                "text=AUDI Q7",
                timeout=20000
            )
        except:
            pass

        await page.wait_for_timeout(3000)

        text = await page.locator("body").inner_text()

        if not text.strip():
            html = await page.content()
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text)

        parsed = parse_domaine_text(text)

        result = {
            "status": "ok",
            "source": "Encheres du Domaine",
            "url": url,
            "page_title": await page.title(),
            "parsed": parsed,
            "text_length": len(text),
            "contains_audi": "AUDI" in text.upper(),
            "contains_q7": "Q7" in text.upper(),
            "contains_km": "245200" in text.replace(" ", ""),
            "text_preview": text[:1500],
        }

        await browser.close()

        return result
