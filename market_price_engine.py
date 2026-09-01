import asyncio
import re
import statistics
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright


BASE_URL = "https://www.lacentrale.fr"

MAX_COMPARABLES = 15
MAX_DETAIL_LINKS = 30

SEARCH_WAIT_MS = 4000
DETAIL_WAIT_MS = 1500

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
    url: str
    title: Optional[str]
    year: int
    mileage_km: int
    price_eur: float
    fuel_type: Optional[str]
    transmission: Optional[str]


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
) -> str:
    model_slug = build_model_slug(
        target.brand,
        target.model,
    )

    return (
        f"{BASE_URL}/"
        f"occasion-voiture-modele-"
        f"{model_slug}.html"
    )


def parse_price(
    text: str,
) -> Optional[float]:
    matches = re.findall(
        (
            r"(\d{1,3}"
            r"(?:[ \u00a0\u202f]\d{3})+"
            r"|\d{4,6})"
            r"\s*€"
        ),
        text,
    )

    for match in matches:
        raw = re.sub(
            r"[ \u00a0\u202f]",
            "",
            match,
        )

        try:
            price = float(raw)
        except ValueError:
            continue

        if (
            500
            <= price
            <= 500000
        ):
            return price

    return None


def parse_year(
    text: str,
) -> Optional[int]:
    match = re.search(
        r"\bAnnée\s+(19\d{2}|20\d{2})\b",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    return int(
        match.group(1)
    )


def parse_mileage(
    text: str,
) -> Optional[int]:
    mileage_section = re.search(
        (
            r"Kilométrage"
            r".{0,500}?"
            r"(\d{1,3}"
            r"(?:[ \u00a0\u202f]\d{3})+"
            r"|\d{4,6})"
            r"\s*km"
        ),
        text,
        re.IGNORECASE
        | re.DOTALL,
    )

    if mileage_section:
        raw = re.sub(
            r"[ \u00a0\u202f]",
            "",
            mileage_section.group(1),
        )

        try:
            return int(raw)
        except ValueError:
            pass

    matches = re.findall(
        (
            r"(\d{1,3}"
            r"(?:[ \u00a0\u202f]\d{3})+"
            r"|\d{4,6})"
            r"\s*km\b"
        ),
        text,
        re.IGNORECASE,
    )

    for match in matches:
        raw = re.sub(
            r"[ \u00a0\u202f]",
            "",
            match,
        )

        try:
            mileage = int(raw)
        except ValueError:
            continue

        if (
            100
            <= mileage
            <= 1000000
        ):
            return mileage

    return None


def parse_fuel(
    text: str,
) -> Optional[str]:
    match = re.search(
        (
            r"Énergie\s+"
            r"(Essence|Diesel|"
            r"Électrique|Electrique|"
            r"Hybride)"
        ),
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    value = normalize_text(
        match.group(1)
    )

    mapping = {
        "ESSENCE": "PETROL",
        "DIESEL": "DIESEL",
        "ELECTRIQUE": "ELECTRIC",
        "ÉLECTRIQUE": "ELECTRIC",
        "HYBRIDE": "HYBRID",
    }

    return mapping.get(
        value,
        value,
    )


def parse_transmission(
    text: str,
) -> Optional[str]:
    match = re.search(
        (
            r"Boîte de vitesse\s+"
            r"(Automatique|Manuelle|Auto)"
        ),
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    value = normalize_text(
        match.group(1)
    )

    if value in {
        "AUTOMATIQUE",
        "AUTO",
    }:
        return "AUTOMATIC"

    if value == "MANUELLE":
        return "MANUAL"

    return value


def normalize_target_fuel(
    value: Optional[str],
) -> Optional[str]:
    normalized = normalize_text(
        value
    )

    mapping = {
        "PETROL": "PETROL",
        "ESSENCE": "PETROL",
        "DIESEL": "DIESEL",
        "ELECTRIC": "ELECTRIC",
        "ELECTRIQUE": "ELECTRIC",
        "ÉLECTRIQUE": "ELECTRIC",
        "HYBRID": "HYBRID",
        "HYBRIDE": "HYBRID",
    }

    return mapping.get(
        normalized,
        normalized or None,
    )


def normalize_target_transmission(
    value: Optional[str],
) -> Optional[str]:
    normalized = normalize_text(
        value
    )

    mapping = {
        "AUTOMATIC": "AUTOMATIC",
        "AUTOMATIQUE": "AUTOMATIC",
        "AUTO": "AUTOMATIC",
        "MANUAL": "MANUAL",
        "MANUELLE": "MANUAL",
    }

    return mapping.get(
        normalized,
        normalized or None,
    )


def is_similar(
    target: VehicleTarget,
    comparable: Comparable,
) -> bool:
    year_difference = abs(
        comparable.year
        - target.year
    )

    if year_difference > 1:
        return False

    mileage_tolerance = max(
        25000,
        int(
            target.mileage_km
            * 0.30
        ),
    )

    mileage_difference = abs(
        comparable.mileage_km
        - target.mileage_km
    )

    if (
        mileage_difference
        > mileage_tolerance
    ):
        return False

    target_fuel = (
        normalize_target_fuel(
            target.fuel_type
        )
    )

    if (
        target_fuel
        and comparable.fuel_type
        and target_fuel
        != comparable.fuel_type
    ):
        return False

    target_transmission = (
        normalize_target_transmission(
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

        low_market_price = quartiles[0]
        high_market_price = quartiles[2]

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


async def collect_detail_links(
    page,
    search_url: str,
) -> list[str]:
    print(
        "Opening market search:",
        search_url,
    )

    await page.goto(
        search_url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(
        SEARCH_WAIT_MS
    )

    all_hrefs = await page.locator(
        "a"
    ).evaluate_all(
        """
        elements => elements
            .map(
                element =>
                    element.href ||
                    element.getAttribute('href')
            )
            .filter(Boolean)
        """
    )

    links = []

    for href in all_hrefs:
        if (
            "auto-occasion-annonce-"
            not in href
        ):
            continue

        full_url = urljoin(
            BASE_URL,
            href,
        )

        if full_url in links:
            continue

        links.append(
            full_url
        )

        if (
            len(links)
            >= MAX_DETAIL_LINKS
        ):
            break

    print(
        "Candidate detail links:",
        len(links),
    )

    if links:
        print(
            "First candidate:",
            links[0],
        )

    return links


async def collect_comparable(
    page,
    url: str,
) -> Optional[Comparable]:
    await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(
        DETAIL_WAIT_MS
    )

    body = page.locator(
        "body"
    )

    text = await body.inner_text()

    title = await page.title()

    price = parse_price(
        text
    )

    year = parse_year(
        text
    )

    mileage = parse_mileage(
        text
    )

    fuel = parse_fuel(
        text
    )

    transmission = (
        parse_transmission(
            text
        )
    )

    print(
        "Parsed:",
        year,
        mileage,
        price,
        fuel,
        transmission,
    )

    if (
        price is None
        or year is None
        or mileage is None
    ):
        return None

    return Comparable(
        url=url,
        title=title,
        year=year,
        mileage_km=mileage,
        price_eur=price,
        fuel_type=fuel,
        transmission=transmission,
    )


async def get_market_price(
    target: VehicleTarget,
) -> dict:
    search_url = build_search_url(
        target
    )

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

        try:
            detail_links = (
                await collect_detail_links(
                    page,
                    search_url,
                )
            )

            comparables = []

            for index, url in enumerate(
                detail_links,
                start=1,
            ):
                if (
                    len(comparables)
                    >= MAX_COMPARABLES
                ):
                    break

                try:
                    comparable = (
                        await collect_comparable(
                            page,
                            url,
                        )
                    )

                    if comparable is None:
                        print(
                            "Rejected:",
                            "missing data",
                        )
                        continue

                    if not is_similar(
                        target,
                        comparable,
                    ):
                        print(
                            "Rejected:",
                            "not similar",
                        )
                        continue

                    comparables.append(
                        comparable
                    )

                    print(
                        "Accepted comparable",
                        len(comparables),
                        "-",
                        comparable.year,
                        comparable.mileage_km,
                        "km",
                        comparable.price_eur,
                        "EUR",
                    )

                except Exception as error:
                    print(
                        "Comparable error",
                        index,
                        ":",
                        str(error),
                    )

            summary = (
                calculate_market_summary(
                    comparables
                )
            )

            summary[
                "search_url"
            ] = search_url

            summary[
                "comparables"
            ] = [
                {
                    "url": item.url,
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
                }
                for item in comparables
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
        "Search URL:",
        result.get(
            "search_url"
        ),
    )

    print(
        "=== MARKET PRICE ENGINE END ==="
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
