import math
import os
import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import psycopg2
import requests
from bs4 import BeautifulSoup


VERSION = "MARKET SOURCE ROUTER V11 SAFETY LOCK"
REQUEST_TIMEOUT = 35
MAX_COMPARABLES_PER_SOURCE = 25
MIN_COMPARABLES_TO_ACCEPT = 5
AUCTION_FEE_RATE = 0.11
BASE_TRANSPORT_COST = 300
BASE_OTHER_COST = 200
DUPLICATE_MILEAGE_TOLERANCE = 100
DUPLICATE_PRICE_TOLERANCE = 50
TARGET_LISTING_ID = os.environ.get("MARKET_TEST_LISTING_ID")

SOURCE_TRUST = {
    "AUTOSCOUT24": 0.95,
    "PARUVENDU": 0.85,
}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

FETCH_CACHE: Dict[str, Optional[str]] = {}

TARGET_BRAND = ""
TARGET_MODEL = ""
TARGET_YEAR = 0
TARGET_MILEAGE = 0
TARGET_FUEL = ""
TARGET_LOCATION = ""
CURRENT_AUCTION_BID = 0
CURRENT_LISTING_ID = ""


@dataclass(frozen=True)
class SearchPlan:
    name: str
    year_min: int
    year_max: int
    mileage_min: int
    mileage_max: int
    confidence_penalty: int


@dataclass(frozen=True)
class Comparable:
    source: str
    year: int
    mileage: int
    fuel: str
    raw_price: int
    adjusted_price: int
    stage: str


def clean_text(value: str) -> str:
    value = (
        value.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("\u2009", " ")
    )
    return re.sub(r"\s+", " ", value).strip()


def parse_int(value: str) -> int:
    digits = re.sub(r"\D", "", value)

    if not digits:
        raise ValueError("No digits found")

    return int(digits)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")

    return re.sub(
        r"[^a-z0-9]+",
        "-",
        ascii_value.lower().strip(),
    ).strip("-")


def normalize_fuel(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
        .strip()
    )

    if "ESSENCE" in normalized or normalized in {"PETROL", "GASOLINE"}:
        return "ESSENCE"

    if "DIESEL" in normalized:
        return "DIESEL"

    if "ELECT" in normalized:
        return "ELECTRIC"

    if "HYBR" in normalized:
        return "HYBRID"

    return normalized


def load_target_from_database() -> None:
    global TARGET_BRAND
    global TARGET_MODEL
    global TARGET_YEAR
    global TARGET_MILEAGE
    global TARGET_FUEL
    global TARGET_LOCATION
    global CURRENT_AUCTION_BID
    global CURRENT_LISTING_ID

    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing")

    connection = psycopg2.connect(database_url)

    try:
        with connection.cursor() as cursor:
            base_select = """
                SELECT
                    l.id,
                    l.brand,
                    l.model,
                    l.year,
                    l.mileage_km,
                    l.fuel_type,
                    l.location,
                    ph.price
                FROM listings AS l
                JOIN LATERAL (
                    SELECT price
                    FROM price_history
                    WHERE listing_id = l.id
                    ORDER BY recorded_at DESC
                    LIMIT 1
                ) AS ph ON TRUE
            """

            if TARGET_LISTING_ID:
                cursor.execute(
                    base_select
                    + """
                    WHERE
                        l.id = %s
                        AND l.status = 'active'
                        AND l.brand IS NOT NULL
                        AND l.model IS NOT NULL
                        AND l.year IS NOT NULL
                        AND l.mileage_km IS NOT NULL
                        AND l.fuel_type IS NOT NULL
                    LIMIT 1
                    """,
                    (TARGET_LISTING_ID,),
                )
            else:
                cursor.execute(
                    base_select
                    + """
                    WHERE
                        l.status = 'active'
                        AND l.category = 'car'
                        AND l.brand IS NOT NULL
                        AND l.model IS NOT NULL
                        AND l.year IS NOT NULL
                        AND l.mileage_km IS NOT NULL
                        AND l.fuel_type IS NOT NULL
                    ORDER BY l.updated_at DESC NULLS LAST, l.created_at DESC
                    LIMIT 1
                    """
                )

            row = cursor.fetchone()

    finally:
        connection.close()

    if not row:
        raise RuntimeError("No active vehicle with price history was found")

    (
        listing_id,
        brand,
        model,
        year,
        mileage,
        fuel,
        location,
        current_bid,
    ) = row

    CURRENT_LISTING_ID = str(listing_id)
    TARGET_BRAND = str(brand).strip().upper()
    TARGET_MODEL = str(model).strip().upper()
    TARGET_YEAR = int(year)
    TARGET_MILEAGE = int(mileage)
    TARGET_FUEL = normalize_fuel(str(fuel))
    TARGET_LOCATION = str(location or "").strip()
    CURRENT_AUCTION_BID = int(current_bid)


def build_search_plans() -> List[SearchPlan]:
    strict_margin = max(
        25000,
        min(50000, round(TARGET_MILEAGE * 0.35)),
    )

    wide_margin = max(
        50000,
        min(90000, round(TARGET_MILEAGE * 0.75)),
    )

    broad_margin = max(
        70000,
        min(120000, round(TARGET_MILEAGE * 1.00)),
    )

    return [
        SearchPlan(
            "STRICT",
            TARGET_YEAR,
            TARGET_YEAR,
            max(0, TARGET_MILEAGE - strict_margin),
            TARGET_MILEAGE + strict_margin,
            0,
        ),
        SearchPlan(
            "WIDE_MILEAGE",
            TARGET_YEAR,
            TARGET_YEAR,
            max(0, TARGET_MILEAGE - wide_margin),
            TARGET_MILEAGE + wide_margin,
            10,
        ),
        SearchPlan(
            "YEAR_FALLBACK",
            TARGET_YEAR - 1,
            TARGET_YEAR + 1,
            max(0, TARGET_MILEAGE - broad_margin),
            TARGET_MILEAGE + broad_margin,
            20,
        ),
    ]


def build_sources(plan: SearchPlan) -> List[dict]:
    brand_slug = slugify(TARGET_BRAND)
    model_slug = slugify(TARGET_MODEL)

    if not brand_slug or not model_slug:
        raise RuntimeError("Brand or model cannot be converted to a search URL")

    paruvendu_fuel_map = {
        "ESSENCE": "essence",
        "DIESEL": "diesel",
        "HYBRID": "hybride",
        "ELECTRIC": "electrique",
    }

    autoscout_fuel_map = {
        "ESSENCE": "B",
        "DIESEL": "D",
        "HYBRID": "2",
        "ELECTRIC": "E",
    }

    sources = []

    paruvendu_fuel = paruvendu_fuel_map.get(TARGET_FUEL)
    autoscout_fuel = autoscout_fuel_map.get(TARGET_FUEL)

    if paruvendu_fuel:
        sources.append(
            {
                "name": "PARUVENDU",
                "url": (
                    "https://www.paruvendu.fr/a/voiture-occasion/"
                    f"{quote(brand_slug)}/"
                    f"{quote(model_slug)}/"
                    f"{quote(paruvendu_fuel)}/"
                ),
            }
        )

    if autoscout_fuel:
        sources.append(
            {
                "name": "AUTOSCOUT24",
                "url": (
                    f"https://www.autoscout24.fr/lst/"
                    f"{quote(brand_slug)}/"
                    f"{quote(model_slug)}"
                    "?atype=C"
                    "&cy=F"
                    "&damaged_listing=exclude"
                    "&desc=0"
                    f"&fregfrom={plan.year_min}"
                    f"&fregto={plan.year_max}"
                    f"&fuel={quote(autoscout_fuel)}"
                    f"&kmfrom={plan.mileage_min}"
                    f"&kmto={plan.mileage_max}"
                    "&sort=standard"
                    "&ustate=N%2CU"
                ),
            }
        )

    return sources


def is_reasonable_mileage(year: int, mileage: int) -> bool:
    if mileage < 0 or mileage > 500000:
        return False

    vehicle_age = max(0, 2026 - year)

    if vehicle_age >= 5 and mileage < 1000:
        return False

    return True


def is_valid_comparable(
    year: int,
    mileage: int,
    fuel: str,
    price: int,
    plan: SearchPlan,
) -> bool:
    return (
        plan.year_min <= year <= plan.year_max
        and fuel == TARGET_FUEL
        and plan.mileage_min <= mileage <= plan.mileage_max
        and is_reasonable_mileage(year, mileage)
        and 1500 <= price <= 100000
    )


def normalize_comparable_price(
    raw_price: int,
    year: int,
    mileage: int,
) -> int:
    year_adjustment = (TARGET_YEAR - year) * 0.04

    mileage_adjustment = (
        (mileage - TARGET_MILEAGE) / 10000.0
    ) * 0.015

    total_adjustment = max(
        -0.15,
        min(0.15, year_adjustment + mileage_adjustment),
    )

    return max(
        1,
        int(round(raw_price * (1.0 + total_adjustment))),
    )


def fetch_html_cached(url: str) -> Optional[str]:
    if url in FETCH_CACHE:
        print("Fetch cache: HIT")
        return FETCH_CACHE[url]

    try:
        response = requests.get(
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
        FETCH_CACHE[url] = None
        return None

    print("Direct HTTP status:", response.status_code)
    print("Direct response bytes:", len(response.content))

    if response.status_code != 200 or len(response.content) <= 1000:
        FETCH_CACHE[url] = None
        return None

    FETCH_CACHE[url] = response.text
    return response.text


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return clean_text(soup.get_text(" ", strip=True))


def deduplicate_source_results(
    items: List[Comparable],
) -> List[Comparable]:
    unique = []
    seen = set()

    for item in items:
        key = (
            item.source,
            item.year,
            item.mileage,
            item.fuel,
            item.raw_price,
        )

        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique[:MAX_COMPARABLES_PER_SOURCE]


def extract_price_values(text: str) -> List[int]:
    patterns = [
        r"€\s*(\d{1,3}(?:[ .,'’]\d{3})+|\d{4,6})",
        r"(\d{1,3}(?:[ .,'’]\d{3})+|\d{4,6})\s*€",
        r"EUR\s*(\d{1,3}(?:[ .,'’]\d{3})+|\d{4,6})",
        r"(\d{1,3}(?:[ .,'’]\d{3})+|\d{4,6})\s*EUR",
    ]

    values = []

    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                value = parse_int(match.group(1))
            except ValueError:
                continue

            if 1500 <= value <= 100000:
                values.append(value)

    return list(dict.fromkeys(values))


def extract_mileage_values(text: str) -> List[int]:
    pattern = re.compile(
        r"(\d{1,3}(?:[ .,'’]\d{3})+|\d{1,6})\s*km\b",
        re.IGNORECASE,
    )

    values = []

    for match in pattern.finditer(text):
        try:
            value = parse_int(match.group(1))
        except ValueError:
            continue

        if 0 <= value <= 500000:
            values.append(value)

    return list(dict.fromkeys(values))


def extract_year_values(text: str) -> List[int]:
    values = []

    month_year_pattern = re.compile(
        r"\b(?:0?[1-9]|1[0-2])\s*/\s*(20[0-2]\d)\b"
    )

    for match in month_year_pattern.finditer(text):
        values.append(int(match.group(1)))

    standalone_pattern = re.compile(r"\b(20[0-2]\d)\b")

    for match in standalone_pattern.finditer(text):
        value = int(match.group(1))

        if value not in values:
            values.append(value)

    return values


def extract_fuel_values(text: str) -> List[str]:
    pattern = re.compile(
        r"\b("
        r"Essence|Diesel|Hybride|Hybrid|"
        r"Electrique|Électrique|Electric"
        r")\b",
        re.IGNORECASE,
    )

    values = []

    for match in pattern.finditer(text):
        fuel = normalize_fuel(match.group(1))

        if fuel not in values:
            values.append(fuel)

    return values


def parse_autoscout_card_fields(
    card_text: str,
    plan: SearchPlan,
) -> Tuple[Optional[Tuple[int, int, str, int]], str]:
    text = clean_text(card_text)
    lower_text = text.lower()

    if TARGET_BRAND.lower() not in lower_text:
        return None, "brand_missing"

    if TARGET_MODEL.lower() not in lower_text:
        return None, "model_missing"

    prices = extract_price_values(text)
    mileages = extract_mileage_values(text)
    years = extract_year_values(text)
    fuels = extract_fuel_values(text)

    if not prices:
        return None, "price_missing"

    if not mileages:
        return None, "mileage_missing"

    if not years:
        return None, "year_missing"

    if not fuels:
        return None, "fuel_missing"

    matching_years = [
        year
        for year in years
        if plan.year_min <= year <= plan.year_max
    ]

    if not matching_years:
        return None, "year_outside_plan"

    matching_fuels = [
        fuel
        for fuel in fuels
        if fuel == TARGET_FUEL
    ]

    if not matching_fuels:
        return None, "fuel_mismatch"

    year = min(
        matching_years,
        key=lambda value: abs(value - TARGET_YEAR),
    )

    matching_mileages = [
        mileage
        for mileage in mileages
        if (
            plan.mileage_min <= mileage <= plan.mileage_max
            and is_reasonable_mileage(year, mileage)
        )
    ]

    if not matching_mileages:
        return None, "mileage_outside_plan"

    mileage = min(
        matching_mileages,
        key=lambda value: abs(value - TARGET_MILEAGE),
    )

    fuel = matching_fuels[0]

    valid_prices = [
        price
        for price in prices
        if 1500 <= price <= 100000
    ]

    if not valid_prices:
        return None, "price_outside_range"

    price = valid_prices[0]

    if not is_valid_comparable(
        year,
        mileage,
        fuel,
        price,
        plan,
    ):
        return None, "final_validation_failed"

    return (
        year,
        mileage,
        fuel,
        price,
    ), "ok"


def extract_autoscout_cards(
    soup: BeautifulSoup,
) -> List[str]:
    candidates = []
    seen_text = set()

    selectors = [
        "article",
        '[data-testid*="list-item"]',
        '[data-testid*="listing"]',
        '[data-testid*="search-result"]',
        '[class*="ListItem"]',
        '[class*="list-item"]',
        '[class*="listing"]',
        '[class*="result"]',
    ]

    for selector in selectors:
        for node in soup.select(selector):
            text = clean_text(
                node.get_text(" ", strip=True)
            )

            if len(text) < 60 or len(text) > 4000:
                continue

            lower_text = text.lower()

            if TARGET_BRAND.lower() not in lower_text:
                continue

            if TARGET_MODEL.lower() not in lower_text:
                continue

            if "km" not in lower_text:
                continue

            if "€" not in text and "eur" not in lower_text:
                continue

            if text in seen_text:
                continue

            seen_text.add(text)
            candidates.append(text)

    return candidates


def extract_paruvendu_comparables(
    html: str,
    plan: SearchPlan,
) -> List[Comparable]:
    text = html_to_text(html)

    identity = (
        re.escape(TARGET_BRAND)
        + r"\s+"
        + re.escape(TARGET_MODEL)
    )

    patterns = [
        re.compile(
            identity
            + r".{0,400}?\b(20\d{2})\b"
            + r".{0,220}?(\d{1,3}(?:[ .,'’]\d{3})+|\d{4,6})\s*(?:KM|KMS)\b"
            + r".{0,180}?(ESSENCE|DIESEL|HYBRIDE|ELECTRIQUE|ÉLECTRIQUE)"
            + r".{0,220}?(\d{1,3}(?:[ .,'’]\d{3})+|\d{3,6})\s*€",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{1,3}(?:[ .,'’]\d{3})+|\d{3,6})\s*€"
            + r".{0,350}?"
            + identity
            + r".{0,350}?\b(20\d{2})\b"
            + r".{0,220}?(\d{1,3}(?:[ .,'’]\d{3})+|\d{4,6})\s*(?:KM|KMS)\b"
            + r".{0,180}?(ESSENCE|DIESEL|HYBRIDE|ELECTRIQUE|ÉLECTRIQUE)",
            re.IGNORECASE,
        ),
    ]

    results = []

    for pattern_index, pattern in enumerate(patterns):
        for match in pattern.finditer(text):
            try:
                if pattern_index == 0:
                    year = int(match.group(1))
                    mileage = parse_int(match.group(2))
                    fuel = normalize_fuel(match.group(3))
                    price = parse_int(match.group(4))
                else:
                    price = parse_int(match.group(1))
                    year = int(match.group(2))
                    mileage = parse_int(match.group(3))
                    fuel = normalize_fuel(match.group(4))

            except (ValueError, IndexError):
                continue

            if is_valid_comparable(
                year,
                mileage,
                fuel,
                price,
                plan,
            ):
                results.append(
                    Comparable(
                        "PARUVENDU",
                        year,
                        mileage,
                        fuel,
                        price,
                        normalize_comparable_price(
                            price,
                            year,
                            mileage,
                        ),
                        plan.name,
                    )
                )

    return deduplicate_source_results(results)


def extract_autoscout24_comparables(
    html: str,
    plan: SearchPlan,
) -> List[Comparable]:
    soup = BeautifulSoup(html, "html.parser")
    cards = extract_autoscout_cards(soup)

    print(
        "AutoScout candidate cards:",
        len(cards),
    )

    results = []
    rejection_counts: Dict[str, int] = {}

    for card_text in cards:
        parsed, reason = parse_autoscout_card_fields(
            card_text,
            plan,
        )

        if not parsed:
            rejection_counts[reason] = (
                rejection_counts.get(reason, 0) + 1
            )
            continue

        year, mileage, fuel, price = parsed

        results.append(
            Comparable(
                "AUTOSCOUT24",
                year,
                mileage,
                fuel,
                price,
                normalize_comparable_price(
                    price,
                    year,
                    mileage,
                ),
                plan.name,
            )
        )

    if rejection_counts:
        summary = ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(rejection_counts.items())
        )

        print(
            "AutoScout rejected cards:",
            summary,
        )
    else:
        print("AutoScout rejected cards: 0")

    return deduplicate_source_results(results)


def extract_comparables(
    source: str,
    html: str,
    plan: SearchPlan,
) -> List[Comparable]:
    if source == "PARUVENDU":
        return extract_paruvendu_comparables(
            html,
            plan,
        )

    if source == "AUTOSCOUT24":
        return extract_autoscout24_comparables(
            html,
            plan,
        )

    return []


def remove_cross_source_duplicates(
    items: List[Comparable],
) -> Tuple[List[Comparable], int]:
    kept = []
    removed = 0

    for candidate in items:
        duplicate_found = False

        for existing in kept:
            if candidate.source == existing.source:
                continue

            if (
                candidate.year == existing.year
                and candidate.fuel == existing.fuel
                and abs(
                    candidate.mileage - existing.mileage
                ) <= DUPLICATE_MILEAGE_TOLERANCE
                and abs(
                    candidate.raw_price - existing.raw_price
                ) <= DUPLICATE_PRICE_TOLERANCE
            ):
                duplicate_found = True
                removed += 1
                break

        if not duplicate_found:
            kept.append(candidate)

    return kept, removed


def remove_price_outliers(
    items: List[Comparable],
) -> Tuple[List[Comparable], int]:
    if len(items) < 5:
        return items, 0

    prices = [
        item.adjusted_price
        for item in items
    ]

    median_price = statistics.median(prices)

    deviations = [
        abs(price - median_price)
        for price in prices
    ]

    mad = statistics.median(deviations)

    if mad <= 0:
        return items, 0

    filtered = [
        item
        for item in items
        if (
            0.6745
            * abs(item.adjusted_price - median_price)
            / mad
        ) <= 3.5
    ]

    return (
        filtered,
        len(items) - len(filtered),
    )


def percentile(
    values: List[int],
    fraction: float,
) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)

    if len(ordered) == 1:
        return float(ordered[0])

    position = (
        len(ordered) - 1
    ) * fraction

    lower_index = math.floor(position)
    upper_index = math.ceil(position)

    if lower_index == upper_index:
        return float(ordered[lower_index])

    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]

    return (
        lower_value
        + (upper_value - lower_value)
        * (position - lower_index)
    )


def calculate_robust_market_spread(
    items: List[Comparable],
    center_price: int,
) -> float:
    if not items or center_price <= 0:
        return 1.0

    prices = [
        item.adjusted_price
        for item in items
    ]

    p10 = percentile(prices, 0.10)
    p90 = percentile(prices, 0.90)

    if p90 < p10:
        return 1.0

    return (p90 - p10) / center_price


def calculate_source_weighted_market_price(
    items: List[Comparable],
) -> Tuple[int, Dict[str, float]]:
    if not items:
        return 0, {}

    groups: Dict[str, List[Comparable]] = {}

    for item in items:
        groups.setdefault(
            item.source,
            [],
        ).append(item)

    values = []
    weights: Dict[str, float] = {}

    for source, source_items in groups.items():
        source_median = int(
            statistics.median(
                [
                    item.adjusted_price
                    for item in source_items
                ]
            )
        )

        sample_factor = min(
            1.0,
            math.sqrt(len(source_items)) / 5.0,
        )

        weight = (
            SOURCE_TRUST.get(source, 0.75)
            * sample_factor
        )

        weights[source] = weight

        values.append(
            (
                source_median,
                weight,
            )
        )

    total_weight = sum(
        weight
        for _, weight in values
    )

    if total_weight <= 0:
        return (
            int(
                statistics.median(
                    [
                        item.adjusted_price
                        for item in items
                    ]
                )
            ),
            weights,
        )

    weighted_market_price = int(
        round(
            sum(
                price * weight
                for price, weight in values
            )
            / total_weight
        )
    )

    return weighted_market_price, weights


def calculate_confidence(
    items: List[Comparable],
    robust_spread: float,
    stage_penalty: int,
) -> int:
    if not items:
        return 0

    count_score = min(
        50,
        len(items) * 5,
    )

    source_score = min(
        30,
        len(
            {
                item.source
                for item in items
            }
        ) * 15,
    )

    if robust_spread <= 0.20:
        spread_score = 20
    elif robust_spread <= 0.35:
        spread_score = 15
    elif robust_spread <= 0.50:
        spread_score = 10
    else:
        spread_score = 5

    confidence = (
        count_score
        + source_score
        + spread_score
        - stage_penalty
    )

    return max(
        0,
        min(100, int(confidence)),
    )


def calculate_market_haircut(
    source_count: int,
    selected_stage: str,
    confidence: int,
    robust_spread: float,
) -> float:
    haircut = 0.0

    if source_count <= 1:
        haircut += 0.03

    if selected_stage == "WIDE_MILEAGE":
        haircut += 0.03
    elif selected_stage == "YEAR_FALLBACK":
        haircut += 0.06

    if confidence < 60:
        haircut += 0.03
    elif confidence < 70:
        haircut += 0.02

    if robust_spread > 0.50:
        haircut += 0.03
    elif robust_spread > 0.35:
        haircut += 0.02

    return min(0.15, haircut)


def calculate_dynamic_costs(
    safe_sale_value: int,
    confidence: int,
    robust_spread: float,
) -> Dict[str, int]:
    repair_reserve = 350

    if TARGET_MILEAGE >= 100000:
        repair_reserve += 150

    if TARGET_MILEAGE >= 120000:
        repair_reserve += 200

    if robust_spread > 0.50:
        repair_reserve += 150

    risk_reserve = 300

    if confidence < 60:
        risk_reserve += 700
    elif confidence < 70:
        risk_reserve += 500
    elif confidence < 80:
        risk_reserve += 350
    elif confidence < 90:
        risk_reserve += 150

    if robust_spread > 0.50:
        risk_reserve += 200
    elif robust_spread > 0.35:
        risk_reserve += 100

    if safe_sale_value < 7000:
        profit_rate = 0.16
    elif safe_sale_value < 15000:
        profit_rate = 0.18
    else:
        profit_rate = 0.20

    target_profit = max(
        1200,
        round(safe_sale_value * profit_rate),
    )

    return {
        "transport_cost": BASE_TRANSPORT_COST,
        "repair_reserve": repair_reserve,
        "other_cost": BASE_OTHER_COST,
        "target_profit": target_profit,
        "risk_reserve": risk_reserve,
        "profit_rate_percent": round(
            profit_rate * 100
        ),
        "market_spread_percent": round(
            robust_spread * 100
        ),
    }


def calculate_lotrank_max(
    safe_sale_value: int,
    costs: Dict[str, int],
) -> int:
    fixed_costs = (
        costs["transport_cost"]
        + costs["repair_reserve"]
        + costs["other_cost"]
        + costs["target_profit"]
        + costs["risk_reserve"]
    )

    available = safe_sale_value - fixed_costs

    if available <= 0:
        return 0

    return max(
        0,
        math.floor(
            available
            / (1 + AUCTION_FEE_RATE)
        ),
    )


def calculate_acquisition_cost(
    bid: int,
    costs: Dict[str, int],
) -> int:
    return (
        bid
        + round(bid * AUCTION_FEE_RATE)
        + costs["transport_cost"]
        + costs["repair_reserve"]
        + costs["other_cost"]
    )


def calculate_bid_status(
    current_bid: int,
    lotrank_max: int,
    confidence: int,
) -> str:
    if confidence < 60:
        return "LOW_CONFIDENCE"

    if lotrank_max <= 0:
        return "AVOID"

    remaining = lotrank_max - current_bid

    if remaining < 0:
        return "MAX_EXCEEDED"

    if remaining <= lotrank_max * 0.05:
        return "NEAR_MAX"

    return "BID_ROOM_AVAILABLE"


def process_source(
    source: dict,
    plan: SearchPlan,
) -> Tuple[List[Comparable], bool]:
    name = source["name"]
    url = source["url"]

    print(
        f"===== SOURCE {name} / {plan.name} ====="
    )

    print(
        "Host:",
        urlparse(url).netloc,
    )

    html = fetch_html_cached(url)

    if html is None:
        print("Source status: UNAVAILABLE")
        return [], False

    print("Fetch method: DIRECT_OR_CACHE")

    print(
        "Visible text length:",
        len(html_to_text(html)),
    )

    comparables = extract_comparables(
        name,
        html,
        plan,
    )

    print(
        "Comparables parsed:",
        len(comparables),
    )

    if comparables:
        print("Source status: OK")

        for item in comparables[:8]:
            print(
                f"Comparable: "
                f"{item.year} | "
                f"{item.mileage} km | "
                f"{item.fuel} | "
                f"raw {item.raw_price} EUR | "
                f"adjusted {item.adjusted_price} EUR"
            )

        return comparables, True

    print(
        "Source status: REACHED_NO_COMPARABLES"
    )

    return [], True


def run_search_plan(
    plan: SearchPlan,
) -> Tuple[List[Comparable], int]:
    print("===== ADAPTIVE SEARCH PLAN =====")
    print("Stage:", plan.name)

    print(
        "Year range:",
        plan.year_min,
        "-",
        plan.year_max,
    )

    print(
        "Mileage range:",
        plan.mileage_min,
        "-",
        plan.mileage_max,
    )

    print(
        "Confidence penalty:",
        plan.confidence_penalty,
    )

    print(
        "===== ADAPTIVE SEARCH PLAN END ====="
    )

    all_comparables = []
    reached_sources = 0

    for source in build_sources(plan):
        comparables, reached = process_source(
            source,
            plan,
        )

        if reached:
            reached_sources += 1

        all_comparables.extend(comparables)

    return all_comparables, reached_sources


def main() -> None:
    print(
        f"=== {VERSION} START ==="
    )

    print("Database target mode: enabled")
    print("Database writes: no")
    print("Paid proxy fallback: disabled")

    load_target_from_database()

    print(
        "Listing ID:",
        CURRENT_LISTING_ID,
    )

    print(
        f"Target vehicle: "
        f"{TARGET_BRAND} "
        f"{TARGET_MODEL}"
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
        "Target location:",
        TARGET_LOCATION,
    )

    print(
        "Current auction bid:",
        CURRENT_AUCTION_BID,
    )

    selected_plan: Optional[SearchPlan] = None
    selected_comparables: List[Comparable] = []
    selected_reached_sources = 0

    best_plan: Optional[SearchPlan] = None
    best_comparables: List[Comparable] = []
    best_reached_sources = 0

    overall_reached_sources = 0

    for plan in build_search_plans():
        (
            plan_comparables,
            plan_reached_sources,
        ) = run_search_plan(plan)

        overall_reached_sources = max(
            overall_reached_sources,
            plan_reached_sources,
        )

        if (
            len(plan_comparables)
            > len(best_comparables)
        ):
            best_plan = plan
            best_comparables = plan_comparables
            best_reached_sources = plan_reached_sources

        elif (
            len(plan_comparables)
            == len(best_comparables)
            and plan_reached_sources
            > best_reached_sources
        ):
            best_plan = plan
            best_comparables = plan_comparables
            best_reached_sources = plan_reached_sources

        if (
            len(plan_comparables)
            >= MIN_COMPARABLES_TO_ACCEPT
        ):
            selected_plan = plan
            selected_comparables = plan_comparables
            selected_reached_sources = (
                plan_reached_sources
            )
            break

        print(
            f"Fallback activated after "
            f"{plan.name}: only "
            f"{len(plan_comparables)} "
            f"usable comparables"
        )

    if (
        selected_plan is None
        and best_plan is not None
        and best_comparables
    ):
        selected_plan = best_plan
        selected_comparables = best_comparables
        selected_reached_sources = (
            best_reached_sources
        )

        print(
            "Sparse fallback accepted:",
            len(selected_comparables),
            "comparables",
        )

    if selected_plan is None:
        print("===== ROUTER SUMMARY =====")

        print(
            "Sources reached:",
            overall_reached_sources,
        )

        print("Sources with comparables: 0")
        print("Raw comparables: 0")
        print("Status: NO_MARKET_DATA")

        print(
            "===== ROUTER SUMMARY END ====="
        )

        print(
            f"=== {VERSION} END ==="
        )
        return

    print(
        "===== ADAPTIVE SEARCH RESULT ====="
    )

    print(
        "Selected stage:",
        selected_plan.name,
    )

    print(
        "Stage confidence penalty:",
        selected_plan.confidence_penalty,
    )

    print(
        "Raw comparables selected:",
        len(selected_comparables),
    )

    print(
        "===== ADAPTIVE SEARCH RESULT END ====="
    )

    exact_unique = []
    seen = set()

    for item in selected_comparables:
        key = (
            item.source,
            item.year,
            item.mileage,
            item.fuel,
            item.raw_price,
        )

        if key not in seen:
            seen.add(key)
            exact_unique.append(item)

    (
        cross_source_unique,
        duplicate_removed,
    ) = remove_cross_source_duplicates(
        exact_unique
    )

    (
        filtered,
        outliers_removed,
    ) = remove_price_outliers(
        cross_source_unique
    )

    print("===== DUPLICATE FILTER =====")

    print(
        "Exact unique comparables:",
        len(exact_unique),
    )

    print(
        "Cross-source duplicates removed:",
        duplicate_removed,
    )

    print(
        "Duplicate rule: "
        "same year/fuel + <=100 km + <=50 EUR"
    )

    print(
        "Comparables after duplicate filter:",
        len(cross_source_unique),
    )

    print(
        "===== DUPLICATE FILTER END ====="
    )

    print("===== OUTLIER FILTER =====")

    print(
        "Outliers removed:",
        outliers_removed,
    )

    print(
        "Outlier method: "
        "MAD on adjusted prices"
    )

    print(
        "Comparables after outlier filter:",
        len(filtered),
    )

    print(
        "===== OUTLIER FILTER END ====="
    )

    print("===== ROUTER SUMMARY =====")

    source_names = sorted(
        {
            item.source
            for item in filtered
        }
    )

    print(
        "Sources reached:",
        selected_reached_sources,
    )

    print(
        "Sources with comparables:",
        len(source_names),
    )

    print(
        "Raw comparables:",
        len(selected_comparables),
    )

    if not filtered:
        print("Status: NO_MARKET_DATA")

        print(
            "===== ROUTER SUMMARY END ====="
        )

        print(
            f"=== {VERSION} END ==="
        )
        return

    raw_prices = sorted(
        item.raw_price
        for item in filtered
    )

    adjusted_prices = sorted(
        item.adjusted_price
        for item in filtered
    )

    (
        weighted_market_price,
        source_weights,
    ) = calculate_source_weighted_market_price(
        filtered
    )

    robust_spread = calculate_robust_market_spread(
        filtered,
        weighted_market_price,
    )

    confidence = calculate_confidence(
        filtered,
        robust_spread,
        selected_plan.confidence_penalty,
    )

    market_haircut = calculate_market_haircut(
        len(source_names),
        selected_plan.name,
        confidence,
        robust_spread,
    )

    safe_sale_value = max(
        0,
        int(
            round(
                weighted_market_price
                * (1.0 - market_haircut)
            )
        ),
    )

    print("Status: OK")

    print(
        "Selected search stage:",
        selected_plan.name,
    )

    print(
        "Low adjusted market price:",
        min(adjusted_prices),
    )

    print(
        "Raw median market price:",
        int(statistics.median(raw_prices)),
    )

    print(
        "Adjusted median market price:",
        int(
            statistics.median(
                adjusted_prices
            )
        ),
    )

    print(
        "Weighted market price:",
        weighted_market_price,
    )

    print(
        "Market safety haircut:",
        f"{round(market_haircut * 100)}%",
    )

    print(
        "Safe sale value:",
        safe_sale_value,
    )

    print(
        "High adjusted market price:",
        max(adjusted_prices),
    )

    print(
        "Average comparable mileage:",
        int(
            statistics.mean(
                item.mileage
                for item in filtered
            )
        ),
    )

    print(
        "Robust market spread:",
        f"{round(robust_spread * 100)}%",
    )

    print(
        "Confidence:",
        confidence,
    )

    print(
        "Sources used:",
        ", ".join(source_names),
    )

    print(
        "Source weighting: SAMPLE_SIZE_X_TRUST"
    )

    print(
        "Price normalization: "
        "YEAR_4_PERCENT_AND_"
        "MILEAGE_1_5_PERCENT_PER_10K_"
        "CAP_15_PERCENT"
    )

    print(
        "Safety rule: "
        "SINGLE_SOURCE_AND_FALLBACK_"
        "MARKET_HAIRCUT_ENABLED"
    )

    for source in source_names:
        print(
            f"Weight {source}:",
            f"{source_weights.get(source, 0.0):.3f}",
        )

    print(
        "===== ROUTER SUMMARY END ====="
    )

    costs = calculate_dynamic_costs(
        safe_sale_value,
        confidence,
        robust_spread,
    )

    print(
        "===== DYNAMIC COST ENGINE ====="
    )

    print(
        "Safe sale value:",
        safe_sale_value,
    )

    print(
        "Market spread:",
        f"{costs['market_spread_percent']}%",
    )

    print(
        "Transport cost:",
        costs["transport_cost"],
    )

    print(
        "Repair reserve:",
        costs["repair_reserve"],
    )

    print(
        "Other costs:",
        costs["other_cost"],
    )

    print(
        "Target profit rate:",
        f"{costs['profit_rate_percent']}%",
    )

    print(
        "Target profit:",
        costs["target_profit"],
    )

    print(
        "Risk reserve:",
        costs["risk_reserve"],
    )

    print(
        "===== DYNAMIC COST ENGINE END ====="
    )

    lotrank_max = calculate_lotrank_max(
        safe_sale_value,
        costs,
    )

    auction_fee = round(
        CURRENT_AUCTION_BID
        * AUCTION_FEE_RATE
    )

    acquisition_cost = calculate_acquisition_cost(
        CURRENT_AUCTION_BID,
        costs,
    )

    estimated_net_profit = (
        safe_sale_value
        - acquisition_cost
    )

    remaining_bid_room = (
        lotrank_max
        - CURRENT_AUCTION_BID
    )

    bid_status = calculate_bid_status(
        CURRENT_AUCTION_BID,
        lotrank_max,
        confidence,
    )

    print(
        "===== LOTRANK BID ENGINE ====="
    )

    print(
        "Auction fee rate:",
        f"{AUCTION_FEE_RATE * 100:.1f}%",
    )

    print(
        "Auction fee at current bid:",
        auction_fee,
    )

    print(
        "Current acquisition cost:",
        acquisition_cost,
    )

    print(
        "Estimated net profit at current bid:",
        estimated_net_profit,
    )

    print(
        "LotRank Max:",
        lotrank_max,
    )

    print(
        "Remaining bid room:",
        remaining_bid_room,
    )

    print(
        "Bid status:",
        bid_status,
    )

    print(
        "===== LOTRANK BID ENGINE END ====="
    )

    print(
        f"=== {VERSION} END ==="
    )


if __name__ == "__main__":
    main()
