import re
from playwright.async_api import async_playwright


LOT_URL = "https://encheres-domaine.gouv.fr/lot/audiq7-1-doo-1.html"


def parse_domaine_text(text):
    clean = text.replace("\xa0", " ")

    def extract(pattern):
        match = re.search(pattern, clean, re.IGNORECASE)
        return match.group(1).strip() if match else None

    date_first_registration = extract(
        r"Date de 1ère mise en circulation[ \t]+(\d{2}/\d{2}/\d{4})"
    )

    mileage = extract(
        r"Kilométrage[ \t]+(\d[\d ]*)"
    )

    bid = extract(
        r"Enchère en cours\s+(\d[\d ]*)\s*€"
    )

    data = {
        "brand": extract(
            r"Marque Véhicule[ \t]+([^\n]+)"
        ),

        "model": extract(
            r"Modèle Véhicule[ \t]+([^\n]+)"
        ),

        "year": (
            int(date_first_registration[-4:])
            if date_first_registration
            else None
        ),

        "mileage_km": (
            int(mileage.replace(" ", ""))
            if mileage
            else None
        ),

        "fuel_type": extract(
            r"Energie / carburant[ \t]+([^\n]+)"
        ),

        "transmission": extract(
            r"Type de boîte[ \t]+([^\n]+)"
        ),

        "current_bid": (
            int(bid.replace(" ", ""))
            if bid
            else None
        ),

        "auction_fee_pct": 11,

        "reserved_for_pros":
            "RÉSERVÉ AUX PROS" in clean.upper(),

        "registration_certificate": extract(
            r"Certificat d'immatriculation[ \t]+([^\n]+)"
        ),

        "has_key": extract(
            r"Présence d'au moins une clé[ \t]+([^\n]+)"
        ),

        "location": extract(
            r"Dépôt\s*:\s*([^\n]+)"
        ),

        "risk_flags": []
    }

    risk_checks = {
        "mileage_not_guaranteed":
            "km non garantis",

        "missing_original_registration":
            "absence carte grise originale",

        "moldy_interior":
            "intérieur moisi",

        "particle_filter_fault":
            "filtres particules hs",

        "body_damage":
            "coups, chocs, rayures",

        "general_wear":
            "vétusté générale"
    }

    lower_text = clean.lower()

    for flag, phrase in risk_checks.items():
        if phrase in lower_text:
            data["risk_flags"].append(flag)

    return data


async def collect_domaine_lot(url):
    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        await page.goto(
            url,
            wait_until="networkidle",
            timeout=60000
        )

        text = await page.locator("body").inner_text()
        title = await page.title()

        parsed = parse_domaine_text(text)

        result = {
            "source": "Encheres du Domaine",
            "url": url,
            "page_title": title,
            "parsed": parsed,
            "contains_audi":
                "AUDI" in text.upper(),
            "contains_q7":
                "Q7" in text.upper(),
            "contains_km":
                "245200" in text.replace(" ", ""),
            "text_preview":
                text[:2000]
        }

        await browser.close()

        return result


if __name__ == "__main__":
    import asyncio

    data = asyncio.run(
        collect_domaine_lot(LOT_URL)
    )

    print(data)
