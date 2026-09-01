import os
import re
import statistics
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


ZENROWS_API_URL = "https://api.zenrows.com/v1/"

TARGET_BRAND = "PEUGEOT"
TARGET_MODEL = "208"
TARGET_YEAR = 2019
TARGET_MILEAGE = 92910
TARGET_FUEL = "ESSENCE"

MIN_MILEAGE = 60000
MAX_MILEAGE = 125000

REQUEST_TIMEOUT = 35
ZENROWS_TIMEOUT = 150
MAX_COMPARABLES_PER_SOURCE = 20


@dataclass
class Comparable:
    source: str
    year: int
    mileage: int
    fuel: str
    price: int


SOURCES = [
    {
        "name": "PARUVENDU",
        "url": (
            "https://www.paruvendu.fr/a/"
            "voiture-occasion/peugeot/208/essence/"
        ),
        "use_zenrows_fallback": False,
    },
    {
        "name": "AUTOSCOUT24",
        "url": (
            "https://www.autoscout24.fr/lst/peugeot/208"
            "?atype=C&cy=F&damaged_listing=exclude&desc=0"
            "&fregfrom=2019&fregto=2019&fuel=B"
            "&kmfrom=60000&kmto=125000"
            "&sort=standard&ustate=N%2CU"
        ),
        "use_zenrows_fallback": False,
    },
    {
        "name": "LACENTRALE",
        "url": (
            "https://www.lacentrale.fr/"
            "occasion-voiture-modele-peugeot-208.html"
        ),
        "use_zenrows_fallback": True,
    },
]


HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 "
        "(Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 "
        "Safari/537.36"
    ),
}


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = value.replace("\u202f", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_int(value: str) -> int:
    return int(re.sub(r"\D", "", value))


def normalize_fuel(value: str) -> str:
    normalized = value.upper().replace("É", "E")

    if "ESSENCE" in normalized:
        return "ESSENCE"

    if "DIESEL" in normalized:
        return "DIESEL"

    if "ELECT" in normalized:
        return "ELECTRIC"

    if "HYBR" in normalized:
        return "HYBRID"

    return normalized


def fetch_direct(
    url: str,
) -> Optional[requests.Response]:

    try:
        return requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

    except requests.RequestException as error:
        print(
            "Direct request error:",
            type(error).__name__,
            str(error),
        )
        return None


def fetch_with_zenrows(
    url: str,
) -> Optional[requests.Response]:

    api_key = os.environ.get(
        "ZENROWS_API_KEY"
    )

    if not api_key:
        print(
            "ZenRows fallback unavailable: "
            "missing ZENROWS_API_KEY"
        )
        return None

    params = {
        "url": url,
        "apikey": api_key,
        "js_render": "true",
        "premium_proxy": "true",
        "proxy_country": "fr",
    }

    try:
        return requests.get(
            ZENROWS_API_URL,
            params=params,
            timeout=ZENROWS_TIMEOUT,
        )

    except requests.RequestException as error:
        print(
            "ZenRows request error:",
            type(error).__name__,
            str(error),
        )
        return None


def html_to_text(
    html: str,
) -> str:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    return clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )


def extract_comparables(
    source: str,
    text: str,
) -> List[Comparable]:

    patterns = [
        re.compile(
            r"PEUGEOT\s+208"
            r".{0,260}?"
            r"\b(20\d{2})\b"
            r".{0,120}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{4,6})"
            r"\s*(?:KM|KMS)\b"
            r".{0,100}?"
            r"(ESSENCE|DIESEL|HYBRIDE|"
            r"ELECTRIQUE|ÉLECTRIQUE)"
            r".{0,120}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{3,6})"
            r"\s*€",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{1,3}(?:[ .]\d{3})+|\d{3,6})"
            r"\s*€"
            r".{0,220}?"
            r"PEUGEOT\s+208"
            r".{0,220}?"
            r"\b(20\d{2})\b"
            r".{0,140}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{4,6})"
            r"\s*(?:KM|KMS)\b"
            r".{0,100}?"
            r"(ESSENCE|DIESEL|HYBRIDE|"
            r"ELECTRIQUE|ÉLECTRIQUE)",
            re.IGNORECASE,
        ),
    ]

    results: List[Comparable] = []

    for pattern_index, pattern in enumerate(
        patterns
    ):
        for match in pattern.finditer(text):

            try:
                if pattern_index == 0:
                    year = int(
                        match.group(1)
                    )
                    mileage = parse_int(
                        match.group(2)
                    )
                    fuel = normalize_fuel(
                        match.group(3)
                    )
                    price = parse_int(
                        match.group(4)
                    )

                else:
                    price = parse_int(
                        match.group(1)
                    )
                    year = int(
                        match.group(2)
                    )
                    mileage = parse_int(
                        match.group(3)
                    )
                    fuel = normalize_fuel(
                        match.group(4)
                    )

            except (
                ValueError,
                IndexError,
            ):
                continue

            if year != TARGET_YEAR:
                continue

            if fuel != TARGET_FUEL:
                continue

            if not (
                MIN_MILEAGE
                <= mileage
                <= MAX_MILEAGE
            ):
                continue

            if not (
                1500
                <= price
                <= 100000
            ):
                continue

            results.append(
                Comparable(
                    source=source,
                    year=year,
                    mileage=mileage,
                    fuel=fuel,
                    price=price,
                )
            )

    unique: List[Comparable] = []
    seen = set()

    for item in results:

        key = (
            item.source,
            item.year,
            item.mileage,
            item.fuel,
            item.price,
        )

        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique[
        :MAX_COMPARABLES_PER_SOURCE
    ]


def remove_price_outliers(
    items: List[Comparable],
) -> List[Comparable]:

    if len(items) < 4:
        return items

    prices = sorted(
        item.price
        for item in items
    )

    midpoint = len(prices) // 2

    q1 = statistics.median(
        prices[:midpoint]
    )

    upper_half = prices[
        (len(prices) + 1) // 2 :
    ]

    q3 = statistics.median(
        upper_half
    )

    iqr = q3 - q1

    if iqr <= 0:
        return items

    lower_limit = (
        q1 - 1.5 * iqr
    )

    upper_limit = (
        q3 + 1.5 * iqr
    )

    return [
        item
        for item in items
        if (
            lower_limit
            <= item.price
            <= upper_limit
        )
    ]


def calculate_confidence(
    items: List[Comparable],
    successful_sources: int,
) -> int:

    count = len(items)

    if count == 0:
        return 0

    count_score = min(
        60,
        count * 6,
    )

    source_score = min(
        30,
        successful_sources * 10,
    )

    prices = [
        item.price
        for item in items
    ]

    median_price = statistics.median(
        prices
    )

    if median_price <= 0:
        spread_score = 0

    else:
        spread = (
            max(prices)
            - min(prices)
        ) / median_price

        if spread <= 0.20:
            spread_score = 10

        elif spread <= 0.35:
            spread_score = 7

        elif spread <= 0.50:
            spread_score = 4

        else:
            spread_score = 1

    return min(
        100,
        int(
            count_score
            + source_score
            + spread_score
        ),
    )


def process_source(
    source: dict,
) -> tuple[List[Comparable], bool]:

    name = source["name"]
    url = source["url"]

    print(
        f"===== SOURCE {name} ====="
    )

    print(
        "Host:",
        urlparse(url).netloc,
    )

    response = fetch_direct(url)
    method = "DIRECT"

    if response is not None:
        print(
            "Direct HTTP status:",
            response.status_code,
        )

        print(
            "Direct response bytes:",
            len(response.content),
        )

    direct_ok = (
        response is not None
        and response.status_code == 200
    )

    if (
        not direct_ok
        and source.get(
            "use_zenrows_fallback"
        )
    ):
        print(
            "Direct access failed. "
            "Trying ZenRows fallback once."
        )

        response = fetch_with_zenrows(
            url
        )

        method = "ZENROWS"

        if response is not None:
            print(
                "ZenRows HTTP status:",
                response.status_code,
            )

            print(
                "ZenRows response bytes:",
                len(response.content),
            )

    if (
        response is None
        or response.status_code != 200
    ):
        print(
            "Source status: UNAVAILABLE"
        )

        return [], False

    text = html_to_text(
        response.text
    )

    print(
        "Fetch method:",
        method,
    )

    print(
        "Visible text length:",
        len(text),
    )

    comparables = extract_comparables(
        name,
        text,
    )

    print(
        "Comparables parsed:",
        len(comparables),
    )

    if comparables:

        print(
            "Source status: OK"
        )

        for item in comparables[:5]:

            print(
                f"Comparable: "
                f"{item.year} | "
                f"{item.mileage} km | "
                f"{item.fuel} | "
                f"{item.price} EUR"
            )

        return comparables, True

    print(
        "Source status: "
        "REACHED_NO_COMPARABLES"
    )

    print(
        "Text preview:",
        text[:700],
    )

    return [], True


def main() -> None:

    print(
        "=== MARKET SOURCE ROUTER V1 START ==="
    )

    print(
        "Target vehicle: PEUGEOT 208"
    )

    print(
        "Target year:",
        TARGET_YEAR,
    )

    print(
        "Target mileage:",
        TARGET_MILEAGE,
    )

    print(
        "Target fuel:",
        TARGET_FUEL,
    )

    print(
        "Mileage window:",
        MIN_MILEAGE,
        "-",
        MAX_MILEAGE,
    )

    print(
        "Database writes: no"
    )

    all_comparables: List[
        Comparable
    ] = []

    successful_sources = 0

    for source in SOURCES:

        comparables, reached = (
            process_source(
                source
            )
        )

        if reached:
            successful_sources += 1

        all_comparables.extend(
            comparables
        )

    deduplicated: List[
        Comparable
    ] = []

    seen = set()

    for item in all_comparables:

        key = (
            item.year,
            item.mileage,
            item.fuel,
            item.price,
        )

        if key not in seen:
            seen.add(key)
            deduplicated.append(
                item
            )

    filtered = remove_price_outliers(
        deduplicated
    )

    print(
        "===== ROUTER SUMMARY ====="
    )

    print(
        "Sources reached:",
        successful_sources,
    )

    print(
        "Raw comparables:",
        len(all_comparables),
    )

    print(
        "Unique comparables:",
        len(deduplicated),
    )

    print(
        "Comparables after "
        "outlier filter:",
        len(filtered),
    )

    if not filtered:

        print(
            "Status: NO_MARKET_DATA"
        )

        print(
            "===== ROUTER SUMMARY END ====="
        )

        print(
            "=== MARKET SOURCE ROUTER V1 END ==="
        )

        return

    prices = sorted(
        item.price
        for item in filtered
    )

    low_price = min(prices)

    median_price = int(
        statistics.median(
            prices
        )
    )

    high_price = max(prices)

    average_mileage = int(
        statistics.mean(
            item.mileage
            for item in filtered
        )
    )

    confidence = calculate_confidence(
        filtered,
        successful_sources,
    )

    print(
        "Status: OK"
    )

    print(
        "Low market price:",
        low_price,
    )

    print(
        "Median market price:",
        median_price,
    )

    print(
        "High market price:",
        high_price,
    )

    print(
        "Average mileage:",
        average_mileage,
    )

    print(
        "Confidence:",
        confidence,
    )

    source_names = sorted(
        {
            item.source
            for item in filtered
        }
    )

    print(
        "Sources used:",
        ", ".join(
            source_names
        ),
    )

    print(
        "===== ROUTER SUMMARY END ====="
    )

    print(
        "=== MARKET SOURCE ROUTER V1 END ==="
    )


if __name__ == "__main__":
    main()
