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


VERSION = "MARKET SOURCE ROUTER V7 DB"

REQUEST_TIMEOUT = 35
MAX_COMPARABLES_PER_SOURCE = 25

AUCTION_FEE_RATE = 0.11
BASE_TRANSPORT_COST = 300
BASE_OTHER_COST = 200

SOURCE_TRUST = {
    "AUTOSCOUT24": 0.95,
    "PARUVENDU": 0.85,
}

DUPLICATE_MILEAGE_TOLERANCE = 100
DUPLICATE_PRICE_TOLERANCE = 50

TARGET_LISTING_ID = os.environ.get("MARKET_TEST_LISTING_ID")

TARGET_BRAND = ""
TARGET_MODEL = ""
TARGET_YEAR = 0
TARGET_MILEAGE = 0
TARGET_FUEL = ""
TARGET_LOCATION = ""
CURRENT_AUCTION_BID = 0
CURRENT_LISTING_ID = ""

MIN_MILEAGE = 0
MAX_MILEAGE = 0


@dataclass(frozen=True)
class Comparable:
    source: str
    year: int
    mileage: int
    fuel: str
    price: int


HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 "
        "(Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = value.replace("\u202f", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_int(value: str) -> int:
    digits = re.sub(r"\D", "", value)

    if not digits:
        raise ValueError("No digits found")

    return int(digits)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower().strip()
    ascii_value = re.sub(r"[^a-z0-9]+", "-", ascii_value)
    return ascii_value.strip("-")


def normalize_fuel(value: str) -> str:
    normalized = value.upper().replace("É", "E").strip()

    if normalized in {"PETROL", "GASOLINE", "ESSENCE"} or "ESSENCE" in normalized:
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
    global MIN_MILEAGE
    global MAX_MILEAGE

    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing")

    connection = psycopg2.connect(database_url)

    try:
        with connection.cursor() as cursor:
            if TARGET_LISTING_ID:
                cursor.execute(
                    """
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
                    """
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

    mileage_margin = max(
        25000,
        min(
            50000,
            round(TARGET_MILEAGE * 0.35),
        ),
    )

    MIN_MILEAGE = max(
        0,
        TARGET_MILEAGE - mileage_margin,
    )

    MAX_MILEAGE = (
        TARGET_MILEAGE
        + mileage_margin
    )


def build_sources() -> List[dict]:
    brand_slug = slugify(TARGET_BRAND)
    model_slug = slugify(TARGET_MODEL)

    if not brand_slug or not model_slug:
        raise RuntimeError(
            "Brand or model cannot be converted to a search URL"
        )

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

    sources: List[dict] = []

    paruvendu_fuel = paruvendu_fuel_map.get(
        TARGET_FUEL
    )

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

    autoscout_fuel = autoscout_fuel_map.get(
        TARGET_FUEL
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
                    f"&fregfrom={TARGET_YEAR}"
                    f"&fregto={TARGET_YEAR}"
                    f"&fuel={quote(autoscout_fuel)}"
                    f"&kmfrom={MIN_MILEAGE}"
                    f"&kmto={MAX_MILEAGE}"
                    "&sort=standard"
                    "&ustate=N%2CU"
                ),
            }
        )

    return sources


def is_valid_comparable(
    year: int,
    mileage: int,
    fuel: str,
    price: int,
) -> bool:
    if year != TARGET_YEAR:
        return False

    if fuel != TARGET_FUEL:
        return False

    if not MIN_MILEAGE <= mileage <= MAX_MILEAGE:
        return False

    if not 1500 <= price <= 100000:
        return False

    return True


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


def html_to_text(html: str) -> str:
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


def deduplicate_source_results(
    items: List[Comparable],
) -> List[Comparable]:
    unique: List[Comparable] = []
    seen = set()

    for item in items:
        key = (
            item.source,
            item.year,
            item.mileage,
            item.fuel,
            item.price,
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique[
        :MAX_COMPARABLES_PER_SOURCE
    ]


def extract_paruvendu_comparables(
    text: str,
) -> List[Comparable]:
    identity = (
        re.escape(TARGET_BRAND)
        + r"\s+"
        + re.escape(TARGET_MODEL)
    )

    patterns = [
        re.compile(
            identity
            + r".{0,320}?"
            + r"\b(20\d{2})\b"
            + r".{0,180}?"
            + r"(\d{1,3}(?:[ .]\d{3})+|\d{4,6})\s*(?:KM|KMS)\b"
            + r".{0,140}?"
            + r"(ESSENCE|DIESEL|HYBRIDE|ELECTRIQUE|ÉLECTRIQUE)"
            + r".{0,180}?"
            + r"(\d{1,3}(?:[ .]\d{3})+|\d{3,6})\s*€",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{1,3}(?:[ .]\d{3})+|\d{3,6})\s*€"
            + r".{0,280}?"
            + identity
            + r".{0,280}?"
            + r"\b(20\d{2})\b"
            + r".{0,180}?"
            + r"(\d{1,3}(?:[ .]\d{3})+|\d{4,6})\s*(?:KM|KMS)\b"
            + r".{0,140}?"
            + r"(ESSENCE|DIESEL|HYBRIDE|ELECTRIQUE|ÉLECTRIQUE)",
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

            if is_valid_comparable(
                year,
                mileage,
                fuel,
                price,
            ):
                results.append(
                    Comparable(
                        source="PARUVENDU",
                        year=year,
                        mileage=mileage,
                        fuel=fuel,
                        price=price,
                    )
                )

    return deduplicate_source_results(
        results
    )


def extract_autoscout24_comparables(
    text: str,
) -> List[Comparable]:
    patterns = [
        re.compile(
            r"€\s*(\d{1,3}(?:[ .]\d{3})+|\d{3,6})"
            r".{0,180}?"
            r"(\d{2})/(20\d{2})"
            r".{0,140}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{1,6})\s*km"
            r".{0,120}?"
            r"(Essence|Diesel|Hybride|Electrique|Électrique)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{2})/(20\d{2})"
            r".{0,140}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{1,6})\s*km"
            r".{0,120}?"
            r"(Essence|Diesel|Hybride|Electrique|Électrique)"
            r".{0,180}?"
            r"€\s*(\d{1,3}(?:[ .]\d{3})+|\d{3,6})",
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
                    price = parse_int(
                        match.group(1)
                    )
                    year = int(
                        match.group(3)
                    )
                    mileage = parse_int(
                        match.group(4)
                    )
                    fuel = normalize_fuel(
                        match.group(5)
                    )

                else:
                    year = int(
                        match.group(2)
                    )
                    mileage = parse_int(
                        match.group(3)
                    )
                    fuel = normalize_fuel(
                        match.group(4)
                    )
                    price = parse_int(
                        match.group(5)
                    )

            except (
                ValueError,
                IndexError,
            ):
                continue

            if is_valid_comparable(
                year,
                mileage,
                fuel,
                price,
            ):
                results.append(
                    Comparable(
                        source="AUTOSCOUT24",
                        year=year,
                        mileage=mileage,
                        fuel=fuel,
                        price=price,
                    )
                )

    return deduplicate_source_results(
        results
    )


def extract_comparables(
    source: str,
    text: str,
) -> List[Comparable]:
    if source == "PARUVENDU":
        return extract_paruvendu_comparables(
            text
        )

    if source == "AUTOSCOUT24":
        return extract_autoscout24_comparables(
            text
        )

    return []


def remove_cross_source_duplicates(
    items: List[Comparable],
) -> Tuple[List[Comparable], int]:
    kept: List[Comparable] = []
    removed = 0

    for candidate in items:
        duplicate_found = False

        for existing in kept:
            if candidate.source == existing.source:
                continue

            same_identity = (
                candidate.year
                == existing.year
                and candidate.fuel
                == existing.fuel
            )

            extremely_close_mileage = (
                abs(
                    candidate.mileage
                    - existing.mileage
                )
                <= DUPLICATE_MILEAGE_TOLERANCE
            )

            extremely_close_price = (
                abs(
                    candidate.price
                    - existing.price
                )
                <= DUPLICATE_PRICE_TOLERANCE
            )

            if (
                same_identity
                and extremely_close_mileage
                and extremely_close_price
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
        item.price
        for item in items
    ]

    median_price = statistics.median(
        prices
    )

    absolute_deviations = [
        abs(
            price
            - median_price
        )
        for price in prices
    ]

    mad = statistics.median(
        absolute_deviations
    )

    if mad <= 0:
        return items, 0

    filtered = [
        item
        for item in items
        if (
            0.6745
            * abs(
                item.price
                - median_price
            )
            / mad
        )
        <= 3.5
    ]

    return (
        filtered,
        len(items) - len(filtered),
    )


def percentile(
    values: List[int],
    percentile_value: float,
) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)

    if len(ordered) == 1:
        return float(
            ordered[0]
        )

    position = (
        (len(ordered) - 1)
        * percentile_value
    )

    lower_index = math.floor(
        position
    )

    upper_index = math.ceil(
        position
    )

    if lower_index == upper_index:
        return float(
            ordered[lower_index]
        )

    lower_value = ordered[
        lower_index
    ]

    upper_value = ordered[
        upper_index
    ]

    fraction = (
        position
        - lower_index
    )

    return (
        lower_value
        + (
            upper_value
            - lower_value
        )
        * fraction
    )


def calculate_robust_market_spread(
    items: List[Comparable],
    center_price: int,
) -> float:
    if (
        not items
        or center_price <= 0
    ):
        return 1.0

    prices = [
        item.price
        for item in items
    ]

    p10 = percentile(
        prices,
        0.10,
    )

    p90 = percentile(
        prices,
        0.90,
    )

    if p90 < p10:
        return 1.0

    return (
        p90 - p10
    ) / center_price


def calculate_source_weighted_market_price(
    items: List[Comparable],
) -> Tuple[int, Dict[str, float]]:
    if not items:
        return 0, {}

    source_groups: Dict[
        str,
        List[Comparable],
    ] = {}

    for item in items:
        source_groups.setdefault(
            item.source,
            [],
        ).append(item)

    source_values: List[
        Tuple[int, float]
    ] = []

    source_weights: Dict[
        str,
        float
    ] = {}

    for source, source_items in source_groups.items():
        prices = sorted(
            item.price
            for item in source_items
        )

        source_median = int(
            statistics.median(
                prices
            )
        )

        sample_count = len(
            source_items
        )

        trust = SOURCE_TRUST.get(
            source,
            0.75,
        )

        sample_factor = min(
            1.0,
            math.sqrt(
                sample_count
            ) / 5.0,
        )

        weight = (
            trust
            * sample_factor
        )

        source_weights[
            source
        ] = weight

        source_values.append(
            (
                source_median,
                weight,
            )
        )

    total_weight = sum(
        weight
        for _, weight
        in source_values
    )

    if total_weight <= 0:
        prices = [
            item.price
            for item in items
        ]

        return (
            int(
                statistics.median(
                    prices
                )
            ),
            source_weights,
        )

    weighted_value = (
        sum(
            price * weight
            for price, weight
            in source_values
        )
        / total_weight
    )

    return (
        int(
            round(
                weighted_value
            )
        ),
        source_weights,
    )


def calculate_confidence(
    items: List[Comparable],
    robust_spread: float,
) -> int:
    if not items:
        return 0

    source_count = len(
        {
            item.source
            for item in items
        }
    )

    comparable_count = len(
        items
    )

    count_score = min(
        50,
        comparable_count * 5,
    )

    source_score = min(
        30,
        source_count * 15,
    )

    if robust_spread <= 0.20:
        spread_score = 20

    elif robust_spread <= 0.35:
        spread_score = 15

    elif robust_spread <= 0.50:
        spread_score = 10

    else:
        spread_score = 5

    return min(
        100,
        int(
            count_score
            + source_score
            + spread_score
        ),
    )


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

    if confidence < 70:
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
        round(
            safe_sale_value
            * profit_rate
        ),
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

    available_for_bid_and_fee = (
        safe_sale_value
        - fixed_costs
    )

    if available_for_bid_and_fee <= 0:
        return 0

    maximum_bid = (
        available_for_bid_and_fee
        / (
            1
            + AUCTION_FEE_RATE
        )
    )

    return max(
        0,
        math.floor(
            maximum_bid
        ),
    )


def calculate_acquisition_cost(
    bid: int,
    costs: Dict[str, int],
) -> int:
    auction_fee = round(
        bid
        * AUCTION_FEE_RATE
    )

    return (
        bid
        + auction_fee
        + costs["transport_cost"]
        + costs["repair_reserve"]
        + costs["other_cost"]
    )


def calculate_bid_status(
    current_bid: int,
    lotrank_max: int,
) -> str:
    if lotrank_max <= 0:
        return "AVOID"

    remaining = (
        lotrank_max
        - current_bid
    )

    if remaining < 0:
        return "MAX_EXCEEDED"

    if remaining <= (
        lotrank_max * 0.05
    ):
        return "NEAR_MAX"

    return "BID_ROOM_AVAILABLE"


def process_source(
    source: dict,
) -> Tuple[List[Comparable], bool]:
    name = source["name"]
    url = source["url"]

    print(
        f"===== SOURCE {name} ====="
    )

    print(
        "Host:",
        urlparse(
            url
        ).netloc,
    )

    response = fetch_direct(
        url
    )

    if response is None:
        print(
            "Source status: UNAVAILABLE"
        )

        return [], False

    print(
        "Direct HTTP status:",
        response.status_code,
    )

    print(
        "Direct response bytes:",
        len(
            response.content
        ),
    )

    if (
        response.status_code != 200
        or len(
            response.content
        ) <= 1000
    ):
        print(
            "Source status: UNAVAILABLE"
        )

        return [], False

    text = html_to_text(
        response.text
    )

    print(
        "Fetch method: DIRECT"
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

        for item in comparables[:8]:
            print(
                f"Comparable: "
                f"{item.year} | "
                f"{item.mileage} km | "
                f"{item.fuel} | "
                f"{item.price} EUR"
            )

        return (
            comparables,
            True,
        )

    print(
        "Source status: REACHED_NO_COMPARABLES"
    )

    print(
        "Text preview:",
        text[:900],
    )

    return [], True


def main() -> None:
    print(
        f"=== {VERSION} START ==="
    )

    print(
        "Database target mode: enabled"
    )

    print(
        "Database writes: no"
    )

    print(
        "Paid proxy fallback: disabled"
    )

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

    print(
        "Mileage window:",
        MIN_MILEAGE,
        "-",
        MAX_MILEAGE,
    )

    sources = build_sources()

    if not sources:
        print(
            "Status: UNSUPPORTED_FUEL_FOR_MARKET_SOURCES"
        )

        print(
            f"=== {VERSION} END ==="
        )

        return

    all_comparables: List[
        Comparable
    ] = []

    reached_sources = 0

    for source in sources:
        comparables, reached = process_source(
            source
        )

        if reached:
            reached_sources += 1

        all_comparables.extend(
            comparables
        )

    exact_unique: List[
        Comparable
    ] = []

    exact_seen = set()

    for item in all_comparables:
        key = (
            item.source,
            item.year,
            item.mileage,
            item.fuel,
            item.price,
        )

        if key in exact_seen:
            continue

        exact_seen.add(
            key
        )

        exact_unique.append(
            item
        )

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

    print(
        "===== DUPLICATE FILTER ====="
    )

    print(
        "Exact unique comparables:",
        len(exact_unique),
    )

    print(
        "Cross-source duplicates removed:",
        duplicate_removed,
    )

    print(
        "Duplicate rule:",
        "same year/fuel + <=100 km + <=50 EUR",
    )

    print(
        "Comparables after duplicate filter:",
        len(
            cross_source_unique
        ),
    )

    print(
        "===== DUPLICATE FILTER END ====="
    )

    print(
        "===== OUTLIER FILTER ====="
    )

    print(
        "Outliers removed:",
        outliers_removed,
    )

    print(
        "Outlier method: MAD"
    )

    print(
        "Comparables after outlier filter:",
        len(filtered),
    )

    print(
        "===== OUTLIER FILTER END ====="
    )

    print(
        "===== ROUTER SUMMARY ====="
    )

    source_names = sorted(
        {
            item.source
            for item in filtered
        }
    )

    print(
        "Sources reached:",
        reached_sources,
    )

    print(
        "Sources with comparables:",
        len(source_names),
    )

    print(
        "Raw comparables:",
        len(
            all_comparables
        ),
    )

    if not filtered:
        print(
            "Status: NO_MARKET_DATA"
        )

        print(
            "===== ROUTER SUMMARY END ====="
        )

        print(
            f"=== {VERSION} END ==="
        )

        return

    prices = sorted(
        item.price
        for item in filtered
    )

    low_price = min(
        prices
    )

    raw_median_price = int(
        statistics.median(
            prices
        )
    )

    high_price = max(
        prices
    )

    (
        weighted_market_price,
        source_weights,
    ) = calculate_source_weighted_market_price(
        filtered
    )

    robust_spread = (
        calculate_robust_market_spread(
            filtered,
            weighted_market_price,
        )
    )

    average_mileage = int(
        statistics.mean(
            item.mileage
            for item in filtered
        )
    )

    confidence = calculate_confidence(
        filtered,
        robust_spread,
    )

    print(
        "Status: OK"
    )

    print(
        "Low market price:",
        low_price,
    )

    print(
        "Raw median market price:",
        raw_median_price,
    )

    print(
        "Weighted market price:",
        weighted_market_price,
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
        "Robust market spread:",
        f"{round(robust_spread * 100)}%",
    )

    print(
        "Confidence:",
        confidence,
    )

    print(
        "Sources used:",
        ", ".join(
            source_names
        ),
    )

    print(
        "Source weighting:",
        "SAMPLE_SIZE_X_TRUST",
    )

    for source in source_names:
        print(
            f"Weight {source}:",
            f"{source_weights.get(source, 0.0):.3f}",
        )

    print(
        "===== ROUTER SUMMARY END ====="
    )

    safe_sale_value = (
        weighted_market_price
    )

    dynamic_costs = (
        calculate_dynamic_costs(
            safe_sale_value,
            confidence,
            robust_spread,
        )
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
        f"{dynamic_costs['market_spread_percent']}%",
    )

    print(
        "Transport cost:",
        dynamic_costs[
            "transport_cost"
        ],
    )

    print(
        "Repair reserve:",
        dynamic_costs[
            "repair_reserve"
        ],
    )

    print(
        "Other costs:",
        dynamic_costs[
            "other_cost"
        ],
    )

    print(
        "Target profit rate:",
        f"{dynamic_costs['profit_rate_percent']}%",
    )

    print(
        "Target profit:",
        dynamic_costs[
            "target_profit"
        ],
    )

    print(
        "Risk reserve:",
        dynamic_costs[
            "risk_reserve"
        ],
    )

    print(
        "===== DYNAMIC COST ENGINE END ====="
    )

    lotrank_max = calculate_lotrank_max(
        safe_sale_value,
        dynamic_costs,
    )

    auction_fee_at_current_bid = round(
        CURRENT_AUCTION_BID
        * AUCTION_FEE_RATE
    )

    acquisition_cost = (
        calculate_acquisition_cost(
            CURRENT_AUCTION_BID,
            dynamic_costs,
        )
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
        auction_fee_at_current_bid,
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
