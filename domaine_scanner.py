import asyncio
from urllib.parse import urljoin

from playwright.async_api import async_playwright

from domaine_collector import collect_domaine_lot


LIST_URL = (
    "https://encheres-domaine.gouv.fr/"
    "hermes/biens-mobiliers/vehicules/vehicules-tourisme"
)

MAX_LOTS_PER_RUN = 20


async def discover_vehicle_lots():
    print("STEP 1 - browser starting")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        print("STEP 2 - browser started")

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
        )

        page = await context.new_page()

        print("STEP 3 - opening list page")
        print("URL:", LIST_URL)

        try:
            response = await page.goto(
                LIST_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            if response:
                print("STEP 4 - HTTP status:", response.status)
            else:
                print("STEP 4 - no response object")

        except Exception as e:
            print("PAGE OPEN ERROR:", str(e))
            await browser.close()
            return []

        print("STEP 5 - waiting for page content")

        await page.wait_for_timeout(5000)

        print("STEP 6 - reading links")

        try:
            links = await page.locator("a").evaluate_all(
                """
                elements => elements
                    .map(a => a.getAttribute('href'))
                    .filter(href => href)
                """
            )
        except Exception as e:
            print("LINK READ ERROR:", str(e))
            await browser.close()
            return []

        print("STEP 7 - total links:", len(links))

        lot_urls = []

        for href in links:
            full_url = urljoin(LIST_URL, href)

            if "/lot/" in full_url:
                if full_url not in lot_urls:
                    lot_urls.append(full_url)

        print("STEP 8 - lot links found:", len(lot_urls))

        await browser.close()

        return lot_urls[:MAX_LOTS_PER_RUN]


async def main():
    print("=== DOMAINE SCANNER START ===")

    lot_urls = await discover_vehicle_lots()

    print("Vehicle lots found:", len(lot_urls))

    for index, url in enumerate(lot_urls, start=1):

        print()
        print("================================")
        print("LOT", index)
        print("URL:", url)

        try:
            result = await collect_domaine_lot(url)

            parsed = result.get("parsed", {})

            print("Title:", result.get("page_title"))
            print("Brand:", parsed.get("brand"))
            print("Model:", parsed.get("model"))
            print("Year:", parsed.get("year"))
            print("Mileage:", parsed.get("mileage_km"))
            print("Current bid:", parsed.get("current_bid"))
            print("Location:", parsed.get("location"))
            print("Risk flags:", parsed.get("risk_flags"))

        except Exception as e:
            print("LOT ERROR:", str(e))

    print()
    print("=== DOMAINE SCANNER END ===")


if __name__ == "__main__":
    asyncio.run(main())
