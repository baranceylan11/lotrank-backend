from playwright.async_api import async_playwright


LOT_URL = "https://encheres-domaine.gouv.fr/lot/audiq7-1-doo-1.html"


async def collect_domaine_lot(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        await page.goto(url, wait_until="networkidle", timeout=60000)

        text = await page.locator("body").inner_text()
        title = await page.title()

        result = {
            "source": "Encheres du Domaine",
            "url": url,
            "page_title": title,
            "contains_audi": "AUDI" in text.upper(),
            "contains_q7": "Q7" in text.upper(),
            "contains_km": "245200" in text.replace(" ", ""),
            "text_preview": text[:2000]
        }

        await browser.close()
        return result
