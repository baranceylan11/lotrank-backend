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
            LIST_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await page.wait_for_timeout(3000)

        links = await page.locator("a").evaluate_all(
            """
            elements => elements
                .map(a => a.getAttribute('href'))
                .filter(href => href)
            """
        )

        lot_urls = []

        for href in links:
            full_url = urljoin(LIST_URL, href)

            if (
                "/lot/" in full_url
                or "/hermes/biens-mobiliers/vehicules/" in full_url
            ):
                if full_url not in lot_urls:
                    lot_urls.append(full_url)

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
            print(
                "Risk flags:",
                parsed.get("risk_flags")
            )

        except Exception as e:
            print("ERROR:", str(e))

    print()
    print("=== DOMAINE SCANNER END ===")


if __name__ == "__main__":
    asyncio.run(main())
