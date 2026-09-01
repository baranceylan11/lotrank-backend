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

        links = await page.locator("a").evaluate_all(
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
                href
            )

            if "/lot/" in full_url:

                if full_url not in lot_urls:
                    lot_urls.append(full_url)

        await browser.close()

        print(
            "Vehicle lots found:",
            len(lot_urls)
        )

        return lot_urls[:MAX_LOTS_PER_RUN]


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

    # Duplicate kontrolü
    cur.execute(
        """
        SELECT id
        FROM listings
        WHERE jump_url = %s
        LIMIT 1
        """,
        (url,)
    )

    existing = cur.fetchone()

    if existing:

        listing_id = str(existing[0])

        print(
            "SKIPPED - already exists:",
            listing_id
        )

        cur.close()
        conn.close()

        return {
            "action": "SKIPPED",
            "listing_id": listing_id
        }

    brand = parsed.get("brand")
    model = parsed.get("model")

    title = " ".join(
        x for x in [brand, model]
        if x
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
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
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
            url
        )
    )

    listing_id = str(
        cur.fetchone()[0]
    )

    risk_flags = parsed.get(
        "risk_flags",
        []
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
                risk
            )
        )

        saved_risks += 1

    conn.commit()

    cur.close()
    conn.close()

    print(
        "CREATED listing:",
        listing_id
    )

    print(
        "Risk flags saved:",
        saved_risks
    )

    return {
        "action": "CREATED",
        "listing_id": listing_id,
        "risk_flags_saved": saved_risks
    }


async def main():

    print(
        "=== DOMAINE SCANNER START ==="
    )

    lot_urls = await discover_vehicle_lots()

    created = 0
    skipped = 0
    errors = 0

    for index, url in enumerate(
        lot_urls,
        start=1
    ):

        print()
        print("==============================")
        print("LOT", index)
        print("URL:", url)

        try:

            result = await collect_domaine_lot(
                url
            )

            parsed = result.get(
                "parsed",
                {}
            )

            print(
                "Title:",
                result.get("page_title")
            )

            print(
                "Brand:",
                parsed.get("brand")
            )

            print(
                "Model:",
                parsed.get("model")
            )

            print(
                "Year:",
                parsed.get("year")
            )

            print(
                "Mileage:",
                parsed.get("mileage_km")
            )

            print(
                "Current bid:",
                parsed.get("current_bid")
            )

            print(
                "Location:",
                parsed.get("location")
            )

            database_result = save_listing(
                url,
                parsed
            )

            if (
                database_result["action"]
                == "CREATED"
            ):
                created += 1

            else:
                skipped += 1

        except Exception as e:

            errors += 1

            print(
                "LOT ERROR:",
                str(e)
            )

    print()
    print("===== SCAN SUMMARY =====")
    print("Created:", created)
    print("Skipped:", skipped)
    print("Errors:", errors)

    print(
        "=== DOMAINE SCANNER END ==="
    )


if __name__ == "__main__":
    asyncio.run(main())
