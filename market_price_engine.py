import os
import re
import requests
from bs4 import BeautifulSoup


ZENROWS_API_URL = "https://api.zenrows.com/v1/"

TARGET_URL = (
    "https://www.lacentrale.fr/"
    "occasion-voiture-modele-peugeot-208.html"
)


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = value.replace("\u202f", " ")
    return re.sub(r"\s+", " ", value).strip()


def main() -> None:
    print("=== ZENROWS LACENTRALE TEST START ===")
    print("Target:", TARGET_URL)
    print("Database writes: no")

    api_key = os.environ.get("ZENROWS_API_KEY")

    if not api_key:
        print("Status: MISSING_API_KEY")
        print("ZENROWS_API_KEY was not found.")
        print("=== ZENROWS LACENTRALE TEST END ===")
        return

    print("API key configured: yes")

    params = {
        "url": TARGET_URL,
        "apikey": api_key,
        "js_render": "true",
        "premium_proxy": "true",
        "proxy_country": "fr",
    }

    try:
        response = requests.get(
            ZENROWS_API_URL,
            params=params,
            timeout=120,
        )

    except requests.RequestException as error:
        print("Status: REQUEST_FAILED")
        print("Error type:", type(error).__name__)
        print("Error:", str(error))
        print("=== ZENROWS LACENTRALE TEST END ===")
        return

    print("ZenRows HTTP status:", response.status_code)
    print(
        "Content-Type:",
        response.headers.get("content-type"),
    )
    print("Response bytes:", len(response.content))

    if response.status_code != 200:
        print("Status: ZENROWS_ERROR")
        print(
            "Response preview:",
            clean_text(response.text)[:1200],
        )
        print("=== ZENROWS LACENTRALE TEST END ===")
        return

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    page_text = clean_text(
        soup.get_text(" ", strip=True)
    )

    page_title = ""

    if soup.title:
        page_title = clean_text(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

    lower_text = page_text.lower()

    price_matches = re.findall(
        r"\b\d{1,3}(?:[ .]\d{3})+\s*€",
        page_text,
    )

    mileage_matches = re.findall(
        r"\b\d{1,3}(?:[ .]\d{3})+\s*km\b",
        page_text,
        re.IGNORECASE,
    )

    vehicle_links = []

    for link in soup.find_all(
        "a",
        href=True,
    ):
        href = link.get("href", "")

        if "auto-occasion-annonce-" in href:
            vehicle_links.append(href)

    vehicle_links = list(
        dict.fromkeys(vehicle_links)
    )

    blocked_terms = (
        "access denied",
        "forbidden",
        "captcha",
        "please enable js",
        "verify you are human",
    )

    blocked = any(
        term in lower_text
        for term in blocked_terms
    )

    print("Page title:", page_title[:200])
    print(
        "Visible text length:",
        len(page_text),
    )
    print(
        "Price patterns found:",
        len(price_matches),
    )
    print(
        "Mileage patterns found:",
        len(mileage_matches),
    )
    print(
        "Vehicle links found:",
        len(vehicle_links),
    )
    print(
        "Blocked page detected:",
        "yes" if blocked else "no",
    )

    if (
        len(page_text) > 1000
        and len(price_matches) > 0
        and len(mileage_matches) > 0
    ):
        print("Status: OK")

    elif blocked:
        print("Status: BLOCKED")

    else:
        print("Status: NO_LISTING_DATA")

    print(
        "Text preview:",
        page_text[:1500],
    )

    if vehicle_links:
        print(
            "First vehicle link:",
            vehicle_links[0],
        )

    print("=== ZENROWS LACENTRALE TEST END ===")


if __name__ == "__main__":
    main()
