import re
import requests
from bs4 import BeautifulSoup


TEST_URL = "https://www.lacentrale.fr/occasion-voiture-modele-peugeot-208.html"

HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.lacentrale.fr/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u202f", " ")
    return re.sub(r"\s+", " ", value).strip()


def extract_vehicle_candidates(text: str):
    pattern = re.compile(
        r"\b(20(?:0\d|1\d|2\d))\b"
        r".{0,80}?"
        r"\b(\d{1,3}(?:[ .]\d{3})+|\d{4,6})\s*km\b"
        r".{0,80}?"
        r"\b(Essence|Diesel|Hybride(?:s)?|Électrique|Electrique)\b"
        r".{0,80}?"
        r"\b(\d{1,3}(?:[ .]\d{3})+|\d{3,6})\s*€",
        re.IGNORECASE,
    )

    results = []

    for match in pattern.finditer(text):
        year = int(match.group(1))
        mileage = int(re.sub(r"\D", "", match.group(2)))
        fuel = match.group(3).upper()
        price = int(re.sub(r"\D", "", match.group(4)))

        if not (2000 <= year <= 2030):
            continue

        if not (0 <= mileage <= 1_000_000):
            continue

        if not (500 <= price <= 500_000):
            continue

        results.append(
            {
                "year": year,
                "mileage": mileage,
                "fuel": fuel,
                "price": price,
            }
        )

    return results


def main():
    print("=== LACENTRALE PUBLIC HTML TEST START ===")
    print("URL:", TEST_URL)
    print("Database writes: no")
    print("API key used: no")

    try:
        response = requests.get(
            TEST_URL,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True,
        )

    except requests.RequestException as error:
        print("Status: REQUEST_FAILED")
        print("Error type:", type(error).__name__)
        print("Error:", str(error))
        print("=== LACENTRALE PUBLIC HTML TEST END ===")
        return

    print("HTTP status:", response.status_code)
    print("Final URL:", response.url)
    print("Content-Type:", response.headers.get("content-type"))
    print("Response bytes:", len(response.content))

    soup = BeautifulSoup(response.text, "html.parser")

    if soup.title:
        page_title = clean_text(
            soup.title.get_text(" ", strip=True)
        )
    else:
        page_title = ""

    page_text = clean_text(
        soup.get_text(" ", strip=True)
    )

    print("Page title:", page_title[:200])
    print("Visible text length:", len(page_text))

    lower_text = page_text.lower()

    block_terms = (
        "access denied",
        "forbidden",
        "captcha",
        "verify you are human",
        "vérifiez que vous êtes humain",
    )

    blocked = any(
        term in lower_text
        for term in block_terms
    )

    vehicle_candidates = extract_vehicle_candidates(
        page_text
    )

    price_count = len(
        re.findall(
            r"\b\d{1,3}(?:[ .]\d{3})+\s*€",
            page_text,
        )
    )

    mileage_count = len(
        re.findall(
            r"\b\d{1,3}(?:[ .]\d{3})+\s*km\b",
            page_text,
            re.IGNORECASE,
        )
    )

    print(
        "Blocked page detected:",
        "yes" if blocked else "no",
    )

    print(
        "Price patterns found:",
        price_count,
    )

    print(
        "Mileage patterns found:",
        mileage_count,
    )

    print(
        "Vehicle candidates parsed:",
        len(vehicle_candidates),
    )

    if response.status_code == 200 and vehicle_candidates:
        print("Status: OK")

        for index, vehicle in enumerate(
            vehicle_candidates[:10],
            start=1,
        ):
            print(
                f"Vehicle {index}: "
                f"year={vehicle['year']} "
                f"mileage={vehicle['mileage']} "
                f"fuel={vehicle['fuel']} "
                f"price={vehicle['price']}"
            )

    elif response.status_code == 200 and not blocked:
        print("Status: PAGE_REACHED_NO_CANDIDATES")
        print(
            "Text preview:",
            page_text[:1200],
        )

    else:
        print("Status: BLOCKED_OR_UNAVAILABLE")
        print(
            "Text preview:",
            page_text[:1200],
        )

    print(
        "=== LACENTRALE PUBLIC HTML TEST END ==="
    )


if __name__ == "__main__":
    main()
