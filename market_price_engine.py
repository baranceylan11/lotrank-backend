from playwright.sync_api import sync_playwright


TEST_URL = "https://www.leboncoin.fr/ad/voitures/3253062580"


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def run_test() -> None:
    print("=== LEBONCOIN ACCESS TEST START ===")
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

            page.wait_for_timeout(10000)

            title = page.title()

            body_text = page.locator("body").inner_text(
                timeout=10000
            )

            clean_body = normalize_text(body_text)
            lower_body = clean_body.lower()

            blocked_terms = (
                "access denied",
                "forbidden",
                "captcha",
                "verify you are human",
                "vérifiez que vous êtes humain",
            )

            blocked = any(
                term in lower_body
                for term in blocked_terms
            )

            has_brand = "peugeot" in lower_body
            has_model = "208" in clean_body
            has_year = "2019" in clean_body
            has_mileage = (
                "90000 km" in lower_body
                or "90 000 km" in lower_body
            )
            has_fuel = "essence" in lower_body
            has_price = (
                "6 000 €" in clean_body
                or "6000 €" in clean_body
            )

            print("Final URL:", page.url)
            print("Page title:", title)
            print(
                "Visible text length:",
                len(clean_body),
            )
            print(
                "Blocked page detected:",
                "yes" if blocked else "no",
            )

            print(
                "Brand found:",
                "yes" if has_brand else "no",
            )
            print(
                "Model found:",
                "yes" if has_model else "no",
            )
            print(
                "Year found:",
                "yes" if has_year else "no",
            )
            print(
                "Mileage found:",
                "yes" if has_mileage else "no",
            )
            print(
                "Fuel found:",
                "yes" if has_fuel else "no",
            )
            print(
                "Price found:",
                "yes" if has_price else "no",
            )

            if (
                response
                and response.status == 200
                and has_brand
                and has_model
                and has_year
                and has_mileage
                and has_fuel
                and has_price
            ):
                print("Status: OK")

            elif blocked:
                print("Status: ACCESS_BLOCKED")

            elif response and response.status == 403:
                print("Status: HTTP_403")

            else:
                print("Status: PAGE_REACHED_NO_DATA")

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

    print("=== LEBONCOIN ACCESS TEST END ===")


if __name__ == "__main__":
    run_test()
