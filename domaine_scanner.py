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
DAILY_CREATE_LIMIT = 100

SOURCE_ID = "aab590a1-85f9-41f5-8c0e-0822b12ed776"

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

STRONG_MAINSTREAM_MODELS = {
    "PEUGEOT": {"3008", "5008", "508"},
    "RENAULT": {"AUSTRAL", "ESPACE", "KOLEOS", "RAFALE", "TALISMAN"},
    "CITROEN": {"C5", "C5 AIRCROSS", "C6"},
    "VOLKSWAGEN": {"ARTEON", "PASSAT", "TIGUAN", "TOUAREG"},
    "SKODA": {"KODIAQ", "SUPERB"},
    "FORD": {"EXPLORER", "KUGA", "MUSTANG"},
    "TOYOTA": {"HIGHLANDER", "LAND CRUISER", "RAV4"},
    "HYUNDAI": {"IONIQ 5", "IONIQ 6", "SANTA FE", "TUCSON"},
    "KIA": {"EV6", "SORENTO", "SPORTAGE"},
    "NISSAN": {"ARIYA", "QASHQAI", "X-TRAIL"},
    "MAZDA": {"CX-5", "CX-60", "CX-80"},
}

SEVERE_RISKS = {
    "missing_original_registration",
    "moldy_interior",
    "particle_filter_fault",
    "general_wear",
}


def normalize_text(value):
    if not value:
        return ""

    return " ".join(str(value).upper().split())


def matches_strong_mainstream_model(brand, model):
    brand = normalize_text(brand)
    model = normalize_text(model)

    target_models = STRONG_MAINSTREAM_MODELS.get(
        brand,
        set(),
    )

    for target in target_models:
        if target in model:
            return True

    return False


def calculate_opportunity_score(parsed):
    brand = normalize_text(parsed.get("brand"))
    model = normalize_text(parsed.get("model"))
    year = parsed.get("year")
    mileage = parsed.get("mileage_km")
    current_bid = parsed.get("current_bid")
    risk_flags = parsed.get("risk_flags", []) or []

    score = 0
    reasons = []

    is_premium = brand in PREMIUM_BRANDS
    is_strong_mainstream = matches_strong_mainstream_model(
        brand,
        model,
    )

    if is_premium:
        score += 35
        reasons.append("premium segment +35")
    elif is_strong_mainstream:
        score += 25
        reasons.append("strong mainstream segment +25")
    else:
        score += 5
        reasons.append("standard segment +5")

    if current_bid is None:
        score += 5
        reasons.append("missing live bid +5")
    elif current_bid <= 2500:
        score += 30
        reasons.append("very low bid +30")
    elif current_bid <= 5000:
        score += 24
        reasons.append("low bid +24")
    elif current_bid <= 8000:
        score += 16
        reasons.append("medium bid +16")
    elif current_bid <= 12000:
        score += 8
        reasons.append("high bid +8")
    else:
        reasons.append("very high bid +0")

    if year is None:
        reasons.append("missing year +0")
    elif year >= 2020:
        score += 15
        reasons.append("year 2020+ +15")
    elif year >= 2015:
        score += 12
        reasons.append("year 2015+ +12")
    elif year >= 2010:
        score += 8
        reasons.append("year 2010+ +8")
    else:
        score += 3
        reasons.append("older vehicle +3")

    if mileage is None:
        reasons.append("missing mileage +0")
    elif mileage <= 50000:
        score += 12
        reasons.append("very low mileage +12")
    elif mileage <= 100000:
        score += 10
        reasons.append("low mileage +10")
    elif mileage <= 160000:
        score += 6
        reasons.append("medium mileage +6")
    elif mileage <= 220000:
        score += 2
        reasons.append("high mileage +2")
    else:
        score -= 5
        reasons.append("very high mileage -5")

    for risk in risk_flags:
        if risk in SEVERE_RISKS:
            score -= 8
            reasons.append(
                f"severe risk {risk} -8"
            )
        else:
            score -= 3
            reasons.append(
                f"risk {risk} -3"
            )

    score = max(
        0,
        min(100, score),
    )

    return score, reasons


def opportunity_prefilter(parsed):
    score, reasons = calculate_opportunity_score(
        parsed
    )

    if score >= 60:
        return "HIGH_PRIORITY", score, reasons

    if score >= 45:
        return "WATCHLIST", score, reasons

    return "FILTERED", score, reasons


def get_today_created_count():
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"]
    )

    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM listings
        WHERE source_id = %s
          AND created_at >= CURRENT_DATE
        """,
        (SOURCE_ID,),
    )

    count = cur.fetchone()[0]

    cur.close()
    conn.close()

    return count


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
    ) or []

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

    created_today = get_today_created_count()

    print(
        "Created today:",
        created_today,
    )

    print(
        "Daily create limit:",
        DAILY_CREATE_LIMIT,
    )

    if created_today >= DAILY_CREATE_LIMIT:
        print(
            "DAILY LIMIT REACHED - scanner stopped"
        )
        return

    remaining_daily_slots = (
        DAILY_CREATE_LIMIT - created_today
    )

    lot_urls = await discover_vehicle_lots()

    created = 0
    skipped = 0
    high_priority = 0
    watchlist = 0
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

            status, score, reasons = (
                opportunity_prefilter(
                    parsed
                )
            )

            print(
                "Opportunity score:",
                score,
            )

            print(
                "Opportunity status:",
                status,
            )

            print(
                "Opportunity reasons:",
                " | ".join(reasons),
            )

            if status == "FILTERED":
                filtered += 1

                print(
                    "FILTERED - low opportunity score"
                )

                continue

            if status == "HIGH_PRIORITY":
                high_priority += 1

            elif status == "WATCHLIST":
                watchlist += 1

            database_result = save_listing(
                url,
                parsed,
            )

            if (
                database_result["action"]
                == "CREATED"
            ):
                created += 1
                remaining_daily_slots -= 1

                print(
                    "Remaining daily slots:",
                    remaining_daily_slots,
                )

                if remaining_daily_slots <= 0:
                    print(
                        "DAILY LIMIT REACHED - stopping scan"
                    )
                    break

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
        "High priority:",
        high_priority,
    )

    print(
        "Watchlist:",
        watchlist,
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
        "Daily limit:",
        DAILY_CREATE_LIMIT,
    )

    print(
        "Created before this run:",
        created_today,
    )

    print(
        "Created this run:",
        created,
    )

    print(
        "=== DOMAINE SCANNER END ==="
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
