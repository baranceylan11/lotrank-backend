import asyncio
import re
import statistics
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import async_playwright


BASE_URL = "https://www.lacentrale.fr"

MAX_COMPARABLES = 15
MAX_SEARCH_PAGES = 5
PAGE_WAIT_MS = 3500

MODEL_ALIASES = {
    ("PEUGEOT", "208"): "peugeot-208",
}


@dataclass
class VehicleTarget:
    brand: str
    model: str
    year: int
    mileage_km: int
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None


@dataclass
class Comparable:
    year: int
    mileage_km: int
    price_eur: float
    fuel_type: Optional[str]
    transmission: Optional[str]
    source_page: int


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""

    return " ".join(
        str(value).upper().split()
    )


def slugify(value: str) -> str:
    value = value.strip().lower()

    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        value,
    )

    return value.strip("-")


def build_model_slug(
    brand: str,
    model: str,
) -> str:
    key = (
        normalize_text(brand),
        normalize_text(model),
    )

    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key]

    return (
        f"{slugify(brand)}-"
        f"{slugify(model)}"
    )


def build_search_url(
    target: VehicleTarget,
    page_number: int = 1,
) -> str:
    model_slug = build_model_slug(
        target.brand,
        target.model,
    )

    if page_number <= 1:
        return (
            f"{BASE_URL}/"
            f"occasion-voiture-modele-"
            f"{model_slug}.html"
        )

    return (
        f"{BASE_URL}/"
        f"occasion-voiture-modele-"
        f"{model_slug}-{page_number}.html"
    )


def parse_number(
    value: str,
) -> int:
    cleaned = re.sub(
        r"[^\d]",
        "",
        value,
    )

    return int(cleaned)


def normalize_fuel(
    value: Optional[str],
) -> Optional[str]:
    normalized = normalize_text(
        value
    )

    mapping = {
        "ESSENCE": "PETROL",
        "PETROL": "PETROL",
        "DIESEL": "DIESEL",
        "ELECTRIQUE": "ELECTRIC",
        "ÉLECTRIQUE": "ELECTRIC",
        "ELECTRIC": "ELECTRIC",
        "HYBRIDE": "HYBRID",
        "HYBRID": "HYBRID",
    }

    return mapping.get(
        normalized,
        normalized or None,
    )


def normalize_transmission(
    value: Optional[str],
) -> Optional[str]:
    normalized = normalize_text(
        value
    )

    mapping = {
        "AUTO": "AUTOMATIC",
        "AUTOMATIQUE": "AUTOMATIC",
        "AUTOMATIC": "AUTOMATIC",
        "MANUELLE": "MANUAL",
        "MANUAL": "MANUAL",
    }

    return mapping.get(
        normalized,
        normalized or None,
    )


def extract_cards_from_text(
    text: str,
    page_number: int,
) -> list[Comparable]:
    normalized = (
        text
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    )

    pattern = re.compile(
        r"\b(?P<year>19\d{2}|20\d{2})\s+"
        r"(?P<transmission>"
        r"Manuelle|Auto|Automatique"
        r")\s+"
        r"(?P<mileage>"
        r"\d{1,3}(?: \d{3})+|\d{4,6}"
        r")\s*km\s+"
        r"(?P<fuel>"
        r"Essence|Diesel|Électrique|"
        r"Electrique|Hybride"
        r")\s+"
        r"(?P<price>"
        r"\d{1,3}(?: \d{3})+|\d{3,6}"
        r")\s*€",
        re.IGNORECASE,
    )

    results = []

    for match in pattern.finditer(
        normalized
    ):
        try:
            year = int(
                match.group("year")
            )

            mileage = parse_number(
                match.group("mileage")
            )

            price = float(
                parse_number(
                    match.group("price")
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if not (
            1950
            <= year
            <= 2035
        ):
            continue

        if not (
            100
            <= mileage
            <= 1000000
        ):
            continue

        if not (
            500
            <= price
            <= 500000
        ):
            continue

        results.append(
            Comparable(
                year=year,
                mileage_km=mileage,
                price_eur=price,
                fuel_type=normalize_fuel(
                    match.group("fuel")
                ),
                transmission=(
                    normalize_transmission(
                        match.group(
                            "transmission"
                        )
                    )
                ),
                source_page=page_number,
            )
        )

    return results


def is_similar(
    target: VehicleTarget,
    comparable: Comparable,
) -> bool:
    if (
        abs(
            comparable.year
            - target.year
        )
        > 1
    ):
        return False

    mileage_tolerance = max(
        25000,
        int(
            target.mileage_km
            * 0.30
        ),
    )

    if (
        abs(
            comparable.mileage_km
            - target.mileage_km
        )
        > mileage_tolerance
    ):
        return False

    target_fuel = normalize_fuel(
        target.fuel_type
    )

    if (
        target_fuel
        and comparable.fuel_type
        and target_fuel
        != comparable.fuel_type
    ):
        return False

    target_transmission = (
        normalize_transmission(
            target.transmission
        )
    )

    if (
        target_transmission
        and comparable.transmission
        and target_transmission
        != comparable.transmission
    ):
        return False

    return True


def deduplicate_comparables(
    comparables: list[Comparable],
) -> list[Comparable]:
    seen = set()
    unique = []

    for item in comparables:
        key = (
            item.year,
            item.mileage_km,
            int(
                item.price_eur
            ),
            item.fuel_type,
            item.transmission,
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique


def remove_price_outliers(
    comparables: list[Comparable],
) -> list[Comparable]:
    if len(comparables) < 4:
        return comparables

    prices = sorted(
        item.price_eur
        for item in comparables
    )

    quartiles = statistics.quantiles(
        prices,
        n=4,
        method="inclusive",
    )

    q1 = quartiles[0]
    q3 = quartiles[2]

    iqr = q3 - q1

    lower_bound = (
        q1
        - (1.5 * iqr)
    )

    upper_bound = (
        q3
        + (1.5 * iqr)
    )

    filtered = [
        item
        for item in comparables
        if (
            lower_bound
            <= item.price_eur
            <= upper_bound
        )
    ]

    return filtered or comparables


def calculate_market_summary(
    comparables: list[Comparable],
) -> dict:
    cleaned = remove_price_outliers(
        comparables
    )

    prices = sorted(
        item.price_eur
        for item in cleaned
    )

    if not prices:
        return {
            "status": "NO_COMPARABLES",
            "count": 0,
        }

    median_price = statistics.median(
        prices
    )

    if len(prices) >= 4:
        quartiles = statistics.quantiles(
            prices,
            n=4,
            method="inclusive",
        )

        low_market_price = (
            quartiles[0]
        )

        high_market_price = (
            quartiles[2]
        )

    else:
        low_market_price = min(
            prices
        )

        high_market_price = max(
            prices
        )

    return {
        "status": "OK",
        "count": len(prices),
        "low_market_price": round(
            low_market_price,
            2,
        ),
        "median_market_price": round(
            median_price,
            2,
        ),
        "high_market_price": round(
            high_market_price,
            2,
        ),
        "min_price": round(
            min(prices),
            2,
        ),
        "max_price": round(
            max(prices),
            2,
        ),
    }


async def collect_page_text(
    page,
    url: str,
) -> str:
    print(
        "Opening market page:",
        url,
    )

    await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(
        PAGE_WAIT_MS
    )

    return await page.locator(
        "body"
    ).inner_text()


async def get_market_price(
    target: VehicleTarget,
) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120.0.0.0 "
                "Safari/537.36"
            ),
            locale="fr-FR",
        )

        page = await context.new_page()

        accepted = []
        total_cards = 0
        pages_scanned = 0

        try:
            for page_number in range(
                1,
                MAX_SEARCH_PAGES + 1,
            ):
                search_url = (
                    build_search_url(
                        target,
                        page_number,
                    )
                )

                try:
                    text = (
                        await collect_page_text(
                            page,
                            search_url,
                        )
                    )

                except Exception as error:
                    print(
                        "Market page error:",
                        page_number,
                        str(error),
                    )

                    continue

                pages_scanned += 1

                cards = (
                    extract_cards_from_text(
                        text,
                        page_number,
                    )
                )

                total_cards += len(
                    cards
                )

                print(
                    "Cards parsed:",
                    len(cards),
                )

                for comparable in cards:
                    if not is_similar(
                        target,
                        comparable,
                    ):
                        continue

                    accepted.append(
                        comparable
                    )

                    accepted = (
                        deduplicate_comparables(
                            accepted
                        )
                    )

                    print(
                        "Accepted:",
                        comparable.year,
                        comparable.mileage_km,
                        "km",
                        comparable.price_eur,
                        "EUR",
                        comparable.fuel_type,
                        comparable.transmission,
                    )

                    if (
                        len(accepted)
                        >= MAX_COMPARABLES
                    ):
                        break

                if (
                    len(accepted)
                    >= MAX_COMPARABLES
                ):
                    break

            summary = (
                calculate_market_summary(
                    accepted
                )
            )

            summary[
                "total_cards_parsed"
            ] = total_cards

            summary[
                "pages_scanned"
            ] = pages_scanned

            summary[
                "comparables"
            ] = [
                {
                    "year": item.year,
                    "mileage_km": (
                        item.mileage_km
                    ),
                    "price_eur": (
                        item.price_eur
                    ),
                    "fuel_type": (
                        item.fuel_type
                    ),
                    "transmission": (
                        item.transmission
                    ),
                    "source_page": (
                        item.source_page
                    ),
                }
                for item in accepted
            ]

            return summary

        finally:
            await browser.close()


async def main():
    target = VehicleTarget(
        brand="PEUGEOT",
        model="208",
        year=2019,
        mileage_km=92910,
        fuel_type="PETROL",
        transmission=None,
    )

    print(
        "=== MARKET PRICE ENGINE START ==="
    )

    print(
        "Target:",
        target,
    )

    result = await get_market_price(
        target
    )

    print()

    print(
        "===== MARKET SUMMARY ====="
    )

    print(
        "Status:",
        result.get(
            "status"
        ),
    )

    print(
        "Pages scanned:",
        result.get(
            "pages_scanned"
        ),
    )

    print(
        "Cards parsed:",
        result.get(
            "total_cards_parsed"
        ),
    )

    print(
        "Comparables:",
        result.get(
            "count"
        ),
    )

    print(
        "Low market price:",
        result.get(
            "low_market_price"
        ),
    )

    print(
        "Median market price:",
        result.get(
            "median_market_price"
        ),
    )

    print(
        "High market price:",
        result.get(
            "high_market_price"
        ),
    )

    print(
        "=== MARKET PRICE ENGINE END ==="
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
