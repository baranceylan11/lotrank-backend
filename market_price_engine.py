from playwright.sync_api import sync_playwright


TEST_URL = (
    "https://www.lacentrale.fr/"
    "occasion-voiture-modele-peugeot-208.html"
)


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def run_test() -> None:
    print("=== LACENTRALE JAVASCRIPT TEST START ===")
    print("URL:", TEST_URL)
    print("Database writes: no")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            locale="fr-FR",
            timezone_id="Europe/Paris",
            viewport={
                "width": 1440,
                "height": 1000,
            },
            user_agent=(
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/124.0.0.0 "
                "Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            response = page.goto(
                TEST_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            print(
                "Initial HTTP status:",
                response.status if response else None,
            )

            page.wait_for_timeout(12000)

            title = page.title()
            body_text = page.locator("body").inner_text(
                timeout=10000
            )

            clean_body = normalize_text(body_text)
            lower_body = clean_body.lower()

            price_count = clean_body.count("€")
            mileage_count = lower_body.count("km")

            vehicle_links = page.locator(
                'a[href*="auto-occasion-annonce"]'
            ).count()

            js_gate = (
                "please enable js" in lower_body
                or "enable javascript" in lower_body
            )

            forbidden = (
                "access denied" in lower_body
                or "forbidden" in lower_body
            )

            print("Final URL:", page.url)
            print("Page title:", title)
            print(
                "Visible text length:",
                len(clean_body),
            )
            print(
                "Price symbols found:",
                price_count,
            )
            print(
                "Mileage references found:",
                mileage_count,
            )
            print(
                "Vehicle links found:",
                vehicle_links,
            )
            print(
                "JavaScript gate detected:",
                "yes" if js_gate else "no",
            )
            print(
                "Access block detected:",
                "yes" if forbidden else "no",
            )

            if vehicle_links > 0:
                print("Status: OK")
            elif (
                len(clean_body) > 3000
                and price_count > 0
                and mileage_count > 0
            ):
                print("Status: CONTENT_VISIBLE")
            elif js_gate:
                print("Status: JS_GATE_NOT_CLEARED")
            elif forbidden:
                print("Status: ACCESS_BLOCKED")
            else:
                print("Status: PAGE_LOADED_NO_LISTINGS")

            print(
                "Text preview:",
                clean_body[:1500],
            )

        except Exception as error:
            print("Status: ERROR")
            print(
                "Error type:",
                type(error).__name__,
            )
            print("Error:", str(error))

        finally:
            context.close()
            browser.close()

    print("=== LACENTRALE JAVASCRIPT TEST END ===")


if __name__ == "__main__":
    run_test()
