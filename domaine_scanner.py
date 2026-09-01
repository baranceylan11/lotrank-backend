import asyncio
import os
from urllib.parse import urljoin

import psycopg2
from playwright.async_api import async_playwright

from domaine_collector import collect_domaine_lot

LIST_URL = (
    "https://encheres-domaine.gouv.fr/"
    "hermes/biens-mobiliers/vehicules/vehicules-tourisme"
)

MAX_LOTS_PER_RUN = 20

SOURCE_ID = "aab590a1-85f9-41f5-8c0e-0822b12ed776"


# ---------------------------------------------------------
# OPPORTUNITY PREFILTER
# Goal:
# - Filter out most low-end vehicles
# - Prioritize mid-range and premium vehicles
# - Keep potentially profitable mainstream models
# ---------------------------------------------------------

PREMIUM_BRANDS = {
    "AUDI",
    "BMW",
    "MERCEDES",
    "MERCEDES-BENZ",
    "PORSCHE",
    "VOLVO",
    "LEXUS",
    "TESLA",
    "LAND ROVER",
    "RANGE ROVER",
    "JAGUAR",
    "ALFA ROMEO",
    "MASERATI",
    "CUPRA",
}

MAINSTREAM_TARGET_MODELS = {
    "PEUGEOT": {
        "3008",
        "5008",
        "508",
    },
    "RENAULT": {
        "AUSTRAL",
        "ESPACE",
        "KOLEOS",
        "RAFALE",
        "TALISMAN",
    },
    "CITROEN": {
        "C5",
        "C5 AIRCROSS",
        "C6",
    },
    "VOLKSWAGEN": {
        "ARTEON",
        "PASSAT",
        "TIGUAN",
        "TOUAREG",
    },
    "SKODA": {
        "KODIAQ",
        "SUPERB",
    },
    "FORD": {
        "EXPLORER",
        "KUGA",
        "MUSTANG",
    },
    "TOYOTA": {
        "HIGHLANDER",
        "LAND CRUISER",
        "RAV4",
    },
    "HYUNDAI": {
        "IONIQ 5",
        "IONIQ 6",
        "SANTA FE",
        "TUCSON",
    },
    "KIA": {
        "EV6",
        "SORENTO",
        "SPORTAGE",
    },
    "NISSAN": {
        "ARIYA",
        "QASHQAI",
        "X-TRAIL",
    },
    "MAZDA": {
        "CX-5",
        "CX-60",
        "CX-80",
    },
}


def normalize_text(value):
    if not value:
        return ""

    return " ".join(
        str(value).upper().split()
    )


def matches_target_model(brand, model):
    brand = normalize_text(brand)
    model = normalize_text(model)

    target_models = MAINSTREAM_TARGET_MODELS.get(
        brand,
        set(),
    )

    for target in target_models:
        if target in model:
            return True

    return False


def opportunity_prefilter(parsed):
    """
    Returns:
        (True, reason)  -> candidate for detailed processing
        (False, reason) -> filtered out
    """

    brand = normalize_text(
        parsed.get("brand")
    )

    model = normalize_text(
        parsed.get("model")
    )

    year = parsed.get("year")
    mileage = parsed.get("mileage_km")
    current_bid = parsed.get("current_bid")

    risk_flags = parsed.get(
        "risk_flags",
        [],
    )

    if not brand or not model:
        return False, "missing brand or model"

    is_premium = brand in PREMIUM_BRANDS

    is_selected_mainstream = matches_target_model(
        brand,
        model,
    )

    if not is_premium and not is_selected_mainstream:
        return False, "low or standard segment"

    # Age filter.
    if year:
        if is_premium and year < 2010:
            return False, "premium vehicle is too old"

        if is_selected_mainstream and year < 2014:
            return False, "mid-range vehicle is too old"

    # Mileage filter.
    if mileage:
        if is_premium and mileage > 220000:
            return False, "premium vehicle mileage too high"

        if is_selected_mainstream and mileage > 180000:
            return False, "mid-range vehicle mileage too high"

    # Severe risk filter.
    severe_risks = {
        "missing_original_registration",
        "moldy_interior",
        "particle_filter_fault",
        "general_wear",
    }

    severe_count = sum(
        1
        for flag in risk_flags
        if flag in severe_risks
    )

    if severe_count >= 3:
        return False, "too many severe risk flags"

    # Do not reject the vehicle only because a live bid is missing.
    # Real market valuation will be added later.
    if current_bid is None:
        return True, "target segment - waiting for bid data"

    return True, "mid or premium opportunity candidate"


async def discover_vehicle_lots():
    print("Opening Domaine vehicle list...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True
        )

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
            LIST_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(5000)

        links = await page.locator(
            "a"
        ).evaluate_all(
            """
            elements => elements
                .map(a => a.getAttribute('href'))
                .filter(href => href)
            """
        )

        lot_urls = []

        for href in links:
            full_url = urljoin(
                LIST_URL,
                href,
            )

            if (
                "/lot/" in full_url
                and full_url not in lot_urls
            ):
                lot_urls.append(
                    full_url
                )

        await browser.close()

        print(
            "Vehicle lots found:",
            len(lot_urls),
        )

        return lot_urls[
            :MAX_LOTS_PER_RUN
        ]


def normalize_fuel(value):
    if not value:
        return None

    value_lower = value.lower()

    if value_lower == "gazole":
        return "DIESEL"

    if value_lower == "essence":
        return "PETROL"

    return value


def normalize_transmission(value):
    if not value:
        return None

    value_lower = value.lower()

    if "automatique" in value_lower:
        return "automatic"

    if "manuelle" in value_lower:
        return "manual"

    return value


def save_listing(url, parsed):
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"]
    )

    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM listings
        WHERE jump_url = %s
        LIMIT 1
        """,
        (url,),
    )

    existing = cur.fetchone()

    if existing:
        listing_id = str(
            existing[0]
        )

        print(
            "SKIPPED - already exists:",
            listing_id,
        )

        cur.close()
        conn.close()

        return {
            "action": "SKIPPED",
            "listing_id": listing_id,
        }

    brand = parsed.get("brand")
    model = parsed.get("model")

    title = " ".join(
        value
        for value in [
            brand,
            model,
        ]
        if value
    )

    fuel_type = normalize_fuel(
        parsed.get("fuel_type")
    )

    transmission = normalize_transmission(
        parsed.get("transmission")
    )

    cur.execute(
        """
        INSERT INTO listings (
            source_id,
            raw_listing_id,
            title,
            category,
            brand,
            model,
            year,
            mileage_km,
            fuel_type,
            transmission,
            location,
            status,
            jump_url
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            SOURCE_ID,
            None,
            title or "Domaine Vehicle",
            "car",
            brand,
            model,
            parsed.get("year"),
            parsed.get("mileage_km"),
            fuel_type,
            transmission,
            parsed.get("location"),
            "active",
            url,
        ),
    )

    listing_id = str(
        cur.fetchone()[0]
    )

    risk_flags = parsed.get(
        "risk_flags",
        [],
    )

    saved_risks = 0

    for risk in risk_flags:
        cur.execute(
            """
            INSERT INTO risk_flags (
                listing_id,
                flag_type,
                description
            )
            VALUES (%s, %s, %s)
            """,
            (
                listing_id,
                risk,
                risk,
            ),
        )

        saved_risks += 1

    conn.commit()
    cur.close()
    conn.close()

    print(
        "CREATED listing:",
        listing_id,
    )

    print(
        "Risk flags saved:",
        saved_risks,
    )

    return {
        "action": "CREATED",
        "listing_id": listing_id,
        "risk_flags_saved": saved_risks,
    }


async def main():
    print(
        "=== DOMAINE SCANNER START ==="
    )

    lot_urls = await discover_vehicle_lots()

    created = 0
    skipped = 0
    filtered = 0
    errors = 0

    for index, url in enumerate(
        lot_urls,
        start=1,
    ):
        print()
        print(
            "=============================="
        )
        print(
            "LOT",
            index,
        )
        print(
            "URL:",
            url,
        )

        try:
            result = await collect_domaine_lot(
                url
            )

            parsed = result.get(
                "parsed",
                {},
            )

            print(
                "Title:",
                result.get("page_title"),
            )

            print(
                "Brand:",
                parsed.get("brand"),
            )

            print(
                "Model:",
                parsed.get("model"),
            )

            print(
                "Year:",
                parsed.get("year"),
            )

            print(
                "Mileage:",
                parsed.get("mileage_km"),
            )

            print(
                "Current bid:",
                parsed.get("current_bid"),
            )

            print(
                "Location:",
                parsed.get("location"),
            )

            is_candidate, reason = opportunity_prefilter(
                parsed
            )

            print(
                "Opportunity filter:",
                reason,
            )

            if not is_candidate:
                filtered += 1

                print(
                    "FILTERED - not target segment"
                )

                continue

            database_result = save_listing(
                url,
                parsed,
            )

            if (
                database_result["action"]
                == "CREATED"
            ):
                created += 1
            else:
                skipped += 1

        except Exception as error:
            errors += 1

            print(
                "LOT ERROR:",
                str(error),
            )

    print()
    print(
        "===== SCAN SUMMARY ====="
    )

    print(
        "Created:",
        created,
    )

    print(
        "Skipped:",
        skipped,
    )

    print(
        "Filtered:",
        filtered,
    )

    print(
        "Errors:",
        errors,
    )

    print(
        "=== DOMAINE SCANNER END ==="
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
