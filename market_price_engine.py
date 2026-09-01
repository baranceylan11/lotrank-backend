import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


VERSION = "MARKET SOURCE ROUTER V5"

TARGET_BRAND = "PEUGEOT"
TARGET_MODEL = "208"
TARGET_YEAR = 2019
TARGET_MILEAGE = 92910
TARGET_FUEL = "ESSENCE"

CURRENT_AUCTION_BID = 3550

MIN_MILEAGE = 60000
MAX_MILEAGE = 125000

REQUEST_TIMEOUT = 35
MAX_COMPARABLES_PER_SOURCE = 25

AUCTION_FEE_RATE = 0.11
BASE_TRANSPORT_COST = 300
BASE_OTHER_COST = 200

DUPLICATE_MILEAGE_TOLERANCE = 500
DUPLICATE_PRICE_TOLERANCE = 150


@dataclass(frozen=True)
class Comparable:
    source: str
    year: int
    mileage: int
    fuel: str
    price: int


SOURCES = [
    {
        "name": "PARUVENDU",
        "url": "https://www.paruvendu.fr/a/voiture-occasion/peugeot/208/essence/",
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
    },
]


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


def fetch_direct(url: str) -> Optional[requests.Response]:
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

    return unique[:MAX_COMPARABLES_PER_SOURCE]


def extract_paruvendu_comparables(
    text: str,
) -> List[Comparable]:
    patterns = [
        re.compile(
            r"PEUGEOT\s+208"
            r".{0,320}?"
            r"\b(20\d{2})\b"
            r".{0,180}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{4,6})\s*(?:KM|KMS)\b"
            r".{0,140}?"
            r"(ESSENCE|DIESEL|HYBRIDE|ELECTRIQUE|ÉLECTRIQUE)"
            r".{0,180}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{3,6})\s*€",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{1,3}(?:[ .]\d{3})+|\d{3,6})\s*€"
            r".{0,280}?"
            r"PEUGEOT\s+208"
            r".{0,280}?"
            r"\b(20\d{2})\b"
            r".{0,180}?"
            r"(\d{1,3}(?:[ .]\d{3})+|\d{4,6})\s*(?:KM|KMS)\b"
            r".{0,140}?"
            r"(ESSENCE|DIESEL|HYBRIDE|ELECTRIQUE|ÉLECTRIQUE)",
            re.IGNORECASE,
        ),
    ]

    results: List[Comparable] = []

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

    return deduplicate_source_results(results)


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

    for pattern_index, pattern in enumerate(patterns):
        for match in pattern.finditer(text):
            try:
                if pattern_index == 0:
                    price = parse_int(match.group(1))
                    year = int(match.group(3))
                    mileage = parse_int(match.group(4))
                    fuel = normalize_fuel(match.group(5))

                else:
                    year = int(match.group(2))
                    mileage = parse_int(match.group(3))
                    fuel = normalize_fuel(match.group(4))
                    price = parse_int(match.group(5))

            except (ValueError, IndexError):
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

    return deduplicate_source_results(results)


def extract_comparables(
    source: str,
    text: str,
) -> List[Comparable]:
    if source == "PARUVENDU":
        return extract_paruvendu_comparables(text)

    if source == "AUTOSCOUT24":
        return extract_autoscout24_comparables(text)

    return []


def remove_cross_source_duplicates(
    items: List[Comparable],
) -> Tuple[List[Comparable], int]:
    kept: List[Comparable] = []
    removed = 0

    ordered = sorted(
        items,
        key=lambda item: (
            abs(item.mileage - TARGET_MILEAGE),
            item.price,
        ),
    )

    for candidate in ordered:
        duplicate_found = False

        for existing in kept:
            if candidate.source == existing.source:
                continue

            same_identity = (
                candidate.year == existing.year
                and candidate.fuel == existing.fuel
            )

            close_mileage = (
                abs(candidate.mileage - existing.mileage)
                <= DUPLICATE_MILEAGE_TOLERANCE
            )

            close_price = (
                abs(candidate.price - existing.price)
                <= DUPLICATE_PRICE_TOLERANCE
            )

            if same_identity and close_mileage and close_price:
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

    median_price = statistics.median(prices)

    absolute_deviations = [
        abs(price - median_price)
        for price in prices
    ]

    mad = statistics.median(
        absolute_deviations
    )

    mad_filtered = items

    if mad > 0:
        mad_limit = 3.5

        mad_filtered = [
            item
            for item in items
            if (
                0.6745
                * abs(item.price - median_price)
                / mad
            )
            <= mad_limit
        ]

    if len(mad_filtered) < 5:
        removed = len(items) - len(mad_filtered)
        return mad_filtered, removed

    filtered_prices = sorted(
        item.price
        for item in mad_filtered
    )

    midpoint = len(filtered_prices) // 2

    lower_half = filtered_prices[:midpoint]
    upper_half = filtered_prices[(len(filtered_prices) + 1) // 2:]

    q1 = statistics.median(lower_half)
    q3 = statistics.median(upper_half)
    iqr = q3 - q1

    if iqr <= 0:
        removed = len(items) - len(mad_filtered)
        return mad_filtered, removed

    lower_limit = q1 - 1.5 * iqr
    upper_limit = q3 + 1.5 * iqr

    final_items = [
        item
        for item in mad_filtered
        if lower_limit <= item.price <= upper_limit
    ]

    removed = len(items) - len(final_items)

    return final_items, removed


def calculate_equal_source_weighted_median(
    items: List[Comparable],
) -> int:
    if not items:
        return 0

    counts = Counter(
        item.source
        for item in items
    )

    weighted_items = []

    for item in items:
        source_count = counts[item.source]

        if source_count <= 0:
            continue

        weight = 1.0 / source_count

        weighted_items.append(
            (
                item.price,
                weight,
            )
        )

    weighted_items.sort(
        key=lambda entry: entry[0]
    )

    total_weight = sum(
        weight
        for _, weight in weighted_items
    )

    halfway = total_weight / 2
    cumulative = 0.0

    for price, weight in weighted_items:
        cumulative += weight

        if cumulative >= halfway:
            return int(price)

    return int(
        weighted_items[-1][0]
    )


def calculate_confidence(
    items: List[Comparable],
) -> int:
    if not items:
        return 0

    source_count = len(
        {
            item.source
            for item in items
        }
    )

    comparable_count = len(items)

    count_score = min(
        50,
        comparable_count * 5,
    )

    source_score = min(
        30,
        source_count * 15,
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
            spread_score = 20

        elif spread <= 0.35:
            spread_score = 15

        elif spread <= 0.50:
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
    items: List[Comparable],
) -> Dict[str, int]:
    prices = [
        item.price
        for item in items
    ]

    if prices and safe_sale_value > 0:
        market_spread = (
            max(prices)
            - min(prices)
        ) / safe_sale_value
    else:
        market_spread = 1.0

    repair_reserve = 350

    if TARGET_MILEAGE >= 100000:
        repair_reserve += 150

    if TARGET_MILEAGE >= 120000:
        repair_reserve += 200

    if market_spread > 0.50:
        repair_reserve += 150

    risk_reserve = 300

    if confidence < 70:
        risk_reserve += 500

    elif confidence < 80:
        risk_reserve += 350

    elif confidence < 90:
        risk_reserve += 150

    if market_spread > 0.50:
        risk_reserve += 200

    elif market_spread > 0.35:
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

    transport_cost = BASE_TRANSPORT_COST
    other_cost = BASE_OTHER_COST

    return {
        "transport_cost": transport_cost,
        "repair_reserve": repair_reserve,
        "other_cost": other_cost,
        "target_profit": target_profit,
        "risk_reserve": risk_reserve,
        "profit_rate_percent": round(
            profit_rate * 100
        ),
        "market_spread_percent": round(
            market_spread * 100
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
        / (1 + AUCTION_FEE_RATE)
    )

    return max(
        0,
        math.floor(maximum_bid),
    )


def calculate_acquisition_cost(
    bid: int,
    costs: Dict[str, int],
) -> int:
    auction_fee = round(
        bid * AUCTION_FEE_RATE
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

    near_max_limit = (
        lotrank_max * 0.05
    )

    if remaining <= near_max_limit:
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
        urlparse(url).netloc,
    )

    response = fetch_direct(url)

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
        len(response.content),
    )

    if (
        response.status_code != 200
        or len(response.content) <= 1000
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

        return comparables, True

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
        f"Target vehicle: "
        f"{TARGET_BRAND} {TARGET_MODEL}"
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
        "Current auction bid:",
        CURRENT_AUCTION_BID,
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

    print(
        "Paid proxy fallback: disabled"
    )

    all_comparables: List[Comparable] = []
    reached_sources = 0

    for source in SOURCES:
        comparables, reached = process_source(
            source
        )

        if reached:
            reached_sources += 1

        all_comparables.extend(
            comparables
        )

    exact_unique: List[Comparable] = []
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

        exact_seen.add(key)
        exact_unique.append(item)

    cross_source_unique, duplicate_removed = (
        remove_cross_source_duplicates(
            exact_unique
        )
    )

    filtered, outliers_removed = (
        remove_price_outliers(
            cross_source_unique
        )
    )

    source_names = sorted(
        {
            item.source
            for item in filtered
        }
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
        "Comparables after duplicate filter:",
        len(cross_source_unique),
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
        "Comparables after outlier filter:",
        len(filtered),
    )

    print(
        "===== OUTLIER FILTER END ====="
    )

    print(
        "===== ROUTER SUMMARY ====="
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
        len(all_comparables),
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

    low_price = min(prices)

    raw_median_price = int(
        statistics.median(prices)
    )

    balanced_median_price = (
        calculate_equal_source_weighted_median(
            filtered
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
        filtered
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
        "Balanced median market price:",
        balanced_median_price,
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

    print(
        "Sources used:",
        ", ".join(source_names),
    )

    print(
        "Source weighting: EQUAL_TOTAL_WEIGHT"
    )

    print(
        "===== ROUTER SUMMARY END ====="
    )

    safe_sale_value = (
        balanced_median_price
    )

    dynamic_costs = (
        calculate_dynamic_costs(
            safe_sale_value,
            confidence,
            filtered,
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
        dynamic_costs["transport_cost"],
    )

    print(
        "Repair reserve:",
        dynamic_costs["repair_reserve"],
    )

    print(
        "Other costs:",
        dynamic_costs["other_cost"],
    )

    print(
        "Target profit rate:",
        f"{dynamic_costs['profit_rate_percent']}%",
    )

    print(
        "Target profit:",
        dynamic_costs["target_profit"],
    )

    print(
        "Risk reserve:",
        dynamic_costs["risk_reserve"],
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
