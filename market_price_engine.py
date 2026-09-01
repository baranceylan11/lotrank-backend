import os
import re
import statistics
import requests
from bs4 import BeautifulSoup


ZENROWS_API_URL = "https://api.zenrows.com/v1/"

TARGET_URL = (
    "https://www.lacentrale.fr/"
    "occasion-voiture-modele-peugeot-208.html"
)

TARGET_YEAR = 2019
TARGET_MILEAGE = 92910
TARGET_FUEL = "ESSENCE"

MIN_MILEAGE = 60000
MAX_MILEAGE = 125000


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = value.replace("\u202f", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_number(value: str) -> int:
    return int(re.sub(r"\D", "", value))


def extract_comparables(text: str) -> list:
    pattern = re.compile(
        r"PEUGEOT\s+208"
        r".{0,180}?"
        r"\b(20\d{2})\b"
        r"\s+"
        r"(?:Manuelle|Auto|Automatique)"
        r"\s+"
        r"(\d{1,3}(?:\s\d{3})+|\d{4,6})\s*km"
        r"\s+"
        r"(Essence|Diesel|Électrique|Electrique|Hybride)"
        r"\s+"
        r"(\d{1,3}(?:\s\d{3})+|\d{3,6})\s*€",
        re.IGNORECASE,
    )

    results = []

    for match in pattern.finditer(text):
        year = int(match.group(1))
        mileage = parse_number(match.group(2))
        fuel = match.group(3).upper()
        price = parse_number(match.group(4))

        if year != TARGET_YEAR:
            continue

        if fuel not in ("ESSENCE",):
            continue

        if not MIN_MILEAGE <= mileage <= MAX_MILEAGE:
            continue

        if not 1000 <= price <= 100000:
            continue

        results.append(
            {
                "year": year,
                "mileage": mileage,
                "fuel": fuel,
                "price": price,
            }
        )

    unique = []
    seen = set()

    for item in results:
        key = (
            item["year"],
            item["mileage"],
            item["fuel"],
            item["price"],
        )

        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def main() -> None:
    print("=== LACENTRALE MARKET ENGINE START ===")
    print("Target vehicle: PEUGEOT 208")
    print("Target year:", TARGET_YEAR)
    print("Target mileage:", TARGET_MILEAGE)
    print("Target fuel:", TARGET_FUEL)
    print("Database writes: no")

    api_key = os.environ.get("ZENROWS_API_KEY")

    if not api_key:
        print("Status: MISSING_API_KEY")
        print("=== LACENTRALE MARKET ENGINE END ===")
        return

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
        print("Error:", str(error))
        print("=== LACENTRALE MARKET ENGINE END ===")
        return

    print("ZenRows HTTP status:", response.status_code)
    print("Response bytes:", len(response.content))

    if response.status_code != 200:
        print("Status: ZENROWS_ERROR")
        print(
            "Response preview:",
            clean_text(response.text)[:1000],
        )
        print("=== LACENTRALE MARKET ENGINE END ===")
        return

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    page_text = clean_text(
        soup.get_text(" ", strip=True)
    )

    comparables = extract_comparables(page_text)

    print("Comparables found:", len(comparables))

    for index, vehicle in enumerate(
        comparables,
        start=1,
    ):
        print(
            f"Comparable {index}: "
            f"{vehicle['year']} | "
            f"{vehicle['mileage']} km | "
            f"{vehicle['fuel']} | "
            f"{vehicle['price']} EUR"
        )

    if not comparables:
        print("Status: NO_COMPARABLES")
        print("=== LACENTRALE MARKET ENGINE END ===")
        return

    prices = sorted(
        item["price"]
        for item in comparables
    )

    low_price = min(prices)
    median_price = int(statistics.median(prices))
    high_price = max(prices)

    average_mileage = int(
        statistics.mean(
            item["mileage"]
            for item in comparables
        )
    )

    print("===== MARKET SUMMARY =====")
    print("Status: OK")
    print("Comparables:", len(comparables))
    print("Low market price:", low_price)
    print("Median market price:", median_price)
    print("High market price:", high_price)
    print("Average mileage:", average_mileage)
    print("===== MARKET SUMMARY END =====")

    print("=== LACENTRALE MARKET ENGINE END ===")


if __name__ == "__main__":
    main()
