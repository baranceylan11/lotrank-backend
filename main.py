import os
import psycopg2

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="LotRank Backend")


# =========================================================
# BASIC ENDPOINTS
# =========================================================

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "lotrank-backend"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


# =========================================================
# DATABASE TEST
# =========================================================

@app.get("/db-test")
def db_test():
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = conn.cursor()

        cur.execute("SELECT 1;")
        result = cur.fetchone()

        cur.close()
        conn.close()

        return {
            "status": "ok",
            "database": "connected",
            "result": result[0]
        }

    except Exception as e:
        return {
            "status": "error",
            "database": "not_connected",
            "detail": str(e)
        }


# =========================================================
# LISTINGS
# =========================================================

@app.get("/listings")
def get_listings():
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = conn.cursor()

        cur.execute("""
            SELECT
                id,
                raw_listing_id,
                source_id,
                title,
                category,
                brand,
                model,
                year,
                mileage_km,
                fuel_type,
                transmission,
                location,
                status,
                closing_at,
                jump_url,
                created_at,
                updated_at
            FROM listings
            ORDER BY created_at DESC
            LIMIT 50;
        """)

        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        data = [dict(zip(columns, row)) for row in rows]

        cur.close()
        conn.close()

        return {
            "status": "ok",
            "count": len(data),
            "listings": data
        }

    except Exception as e:
        return {
            "status": "error",
            "detail": str(e)
        }


# =========================================================
# LISTINGS + ANALYSIS
# =========================================================

@app.get("/listings-with-analysis")
def get_listings_with_analysis():
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = conn.cursor()

        cur.execute("""
            SELECT
                l.id,
                l.title,
                l.category,
                l.brand,
                l.model,
                l.year,
                l.mileage_km,
                l.fuel_type,
                l.transmission,
                l.location,
                l.status,
                l.closing_at,
                l.jump_url,
                s.lotrank_score,
                s.confidence,
                s.lotrank_max,
                r.flag_type,
                r.description
            FROM listings l
            LEFT JOIN scores s
                ON s.listing_id = l.id
            LEFT JOIN risk_flags r
                ON r.listing_id = l.id
            ORDER BY l.created_at DESC
            LIMIT 50;
        """)

        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        data = [dict(zip(columns, row)) for row in rows]

        cur.close()
        conn.close()

        return {
            "status": "ok",
            "count": len(data),
            "listings": data
        }

    except Exception as e:
        return {
            "status": "error",
            "detail": str(e)
        }


# =========================================================
# LOTRANK SCORING ENGINE V1.0
# =========================================================

class LotRankInput(BaseModel):
    market_value: float
    safe_sale_value: float
    current_bid: float

    auction_fees: float = 0
    transport_cost: float = 0
    repair_cost: float = 0
    other_costs: float = 0

    target_profit: float = 0
    risk_reserve: float = 0

    comparable_count: int = 0
    comparable_dispersion_high: bool = False

    condition_penalty: float = 0

    liquidity_level: int = 5
    competition_level: int = 5
    source_trust_level: int = 3

    vehicle_identity_quality: float = 100
    auction_data_quality: float = 100
    condition_data_quality: float = 100
    cost_data_quality: float = 100


def price_advantage_score(discount_pct):
    if discount_pct >= 35:
        return 30
    elif discount_pct >= 30:
        return 27
    elif discount_pct >= 25:
        return 24
    elif discount_pct >= 20:
        return 21
    elif discount_pct >= 15:
        return 17
    elif discount_pct >= 10:
        return 13
    elif discount_pct >= 5:
        return 8
    elif discount_pct >= 0:
        return 3
    return 0


def margin_score(margin_pct):
    if margin_pct >= 25:
        return 20
    elif margin_pct >= 20:
        return 18
    elif margin_pct >= 15:
        return 15
    elif margin_pct >= 12:
        return 12
    elif margin_pct >= 9:
        return 9
    elif margin_pct >= 6:
        return 6
    elif margin_pct >= 3:
        return 3
    return 0


def market_data_score(count, high_dispersion):
    if count >= 15:
        score = 15
    elif count >= 10:
        score = 13
    elif count >= 7:
        score = 11
    elif count >= 5:
        score = 8
    elif count >= 3:
        score = 5
    elif count >= 1:
        score = 2
    else:
        score = 0

    if high_dispersion:
        score -= 2

    return max(0, score)


def score_label(score):
    if score >= 90:
        return "Exceptional"
    elif score >= 80:
        return "Excellent"
    elif score >= 70:
        return "Good"
    elif score >= 60:
        return "Watch"
    elif score >= 50:
        return "Risky"
    return "Avoid"


@app.post("/score-preview")
def score_preview(data: LotRankInput):

    acquisition_cost = (
        data.current_bid
        + data.auction_fees
        + data.transport_cost
        + data.repair_cost
        + data.other_costs
    )

    if data.market_value > 0:
        discount_pct = (
            (data.market_value - acquisition_cost)
            / data.market_value
        ) * 100
    else:
        discount_pct = 0

    estimated_net_margin = (
        data.safe_sale_value - acquisition_cost
    )

    if data.safe_sale_value > 0:
        margin_pct = (
            estimated_net_margin
            / data.safe_sale_value
        ) * 100
    else:
        margin_pct = 0

    price_score = price_advantage_score(discount_pct)

    net_margin_score = margin_score(margin_pct)

    market_score = market_data_score(
        data.comparable_count,
        data.comparable_dispersion_high
    )

    condition_score = max(
        0,
        min(10, 10 - data.condition_penalty)
    )

    liquidity_score = max(
        0,
        min(10, data.liquidity_level)
    )

    competition_score = max(
        0,
        min(10, data.competition_level)
    )

    source_score = max(
        0,
        min(5, data.source_trust_level)
    )

    total_score = (
        price_score
        + net_margin_score
        + market_score
        + condition_score
        + liquidity_score
        + competition_score
        + source_score
    )

    market_confidence = min(
        100,
        (data.comparable_count / 15) * 100
    )

    confidence = (
        data.vehicle_identity_quality * 0.20
        + market_confidence * 0.30
        + data.auction_data_quality * 0.15
        + data.condition_data_quality * 0.15
        + (data.source_trust_level / 5 * 100) * 0.10
        + data.cost_data_quality * 0.10
    )

    confidence = round(
        max(0, min(100, confidence)),
        1
    )

    lotrank_max = (
        data.safe_sale_value
        - data.auction_fees
        - data.transport_cost
        - data.repair_cost
        - data.other_costs
        - data.target_profit
        - data.risk_reserve
    )

    remaining_room = (
        lotrank_max - data.current_bid
    )

    if remaining_room < 0:
        bid_status = "MAX_EXCEEDED"

    elif (
        lotrank_max > 0
        and remaining_room <= lotrank_max * 0.05
    ):
        bid_status = "NEAR_MAX"

    else:
        bid_status = "BID_ROOM_AVAILABLE"

    if confidence < 40:
        confidence_warning = "LOW_CONFIDENCE"
    else:
        confidence_warning = None

    return {
        "status": "ok",
        "lotrank_version": "1.0",
        "lotrank_score": round(total_score, 1),
        "label": score_label(total_score),
        "confidence": confidence,
        "lotrank_max": round(lotrank_max, 2),
        "current_bid": round(data.current_bid, 2),
        "remaining_bid_room": round(remaining_room, 2),
        "bid_status": bid_status,
        "acquisition_cost": round(acquisition_cost, 2),
        "estimated_net_margin": round(
            estimated_net_margin,
            2
        ),
        "discount_pct": round(discount_pct, 2),
        "margin_pct": round(margin_pct, 2),

        "score_breakdown": {
            "price_advantage": price_score,
            "net_margin": net_margin_score,
            "market_data": market_score,
            "condition_risk": condition_score,
            "liquidity": liquidity_score,
            "auction_competition": competition_score,
            "source_trust": source_score
        },

        "confidence_warning": confidence_warning
    }
# =========================================================
# SCORE REAL LISTING + SAVE TO DATABASE
# =========================================================

@app.post("/score-listing/{listing_id}")
def score_listing(listing_id: str, data: LotRankInput):

    conn = None
    cur = None

    try:
        # Önce mevcut LotRank motoruyla hesapla
        result = score_preview(data)

        conn = psycopg2.connect(
            os.environ["DATABASE_URL"]
        )

        cur = conn.cursor()

        # İlan gerçekten var mı kontrol et
        cur.execute(
            """
            SELECT id, title
            FROM listings
            WHERE id = %s::uuid;
            """,
            (listing_id,)
        )

        listing = cur.fetchone()

        if not listing:
            cur.close()
            conn.close()

            return {
                "status": "error",
                "detail": "LISTING_NOT_FOUND"
            }

        # Bu ilana daha önce score yazılmış mı?
        cur.execute(
            """
            SELECT id
            FROM scores
            WHERE listing_id = %s::uuid
            ORDER BY calculated_at DESC
            LIMIT 1;
            """,
            (listing_id,)
        )

        existing_score = cur.fetchone()

        if existing_score:

            cur.execute(
                """
                UPDATE scores
                SET
                    lotrank_score = %s,
                    confidence = %s,
                    lotrank_max = %s,
                    calculated_at = NOW()
                WHERE id = %s;
                """,
                (
                    result["lotrank_score"],
                    result["confidence"],
                    result["lotrank_max"],
                    existing_score[0]
                )
            )

            database_action = "UPDATED"

        else:

            cur.execute(
                """
                INSERT INTO scores (
                    listing_id,
                    lotrank_score,
                    confidence,
                    lotrank_max,
                    calculated_at
                )
                VALUES (
                    %s::uuid,
                    %s,
                    %s,
                    %s,
                    NOW()
                );
                """,
                (
                    listing_id,
                    result["lotrank_score"],
                    result["confidence"],
                    result["lotrank_max"]
                )
            )

            database_action = "CREATED"

        conn.commit()

        cur.close()
        conn.close()

        return {
            "status": "ok",
            "listing_id": listing_id,
            "listing_title": listing[1],
            "score_database_action": database_action,
            "analysis": result
        }

    except Exception as e:

        if conn:
            conn.rollback()

        if cur:
            cur.close()

        if conn:
            conn.close()

        return {
            "status": "error",
            "detail": str(e)
        }
# =========================================================
# CREATE LISTING + AUTO SCORE
# =========================================================

class CreateListingAndScoreInput(BaseModel):
    source_id: str | None = None
    raw_listing_id: str | None = None

    title: str
    category: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    mileage_km: int | None = None
    fuel_type: str | None = None
    transmission: str | None = None
    location: str | None = None
    status: str = "active"
    jump_url: str | None = None

    market_value: float
    safe_sale_value: float
    current_bid: float

    auction_fees: float = 0
    transport_cost: float = 0
    repair_cost: float = 0
    other_costs: float = 0

    target_profit: float = 0
    risk_reserve: float = 0

    comparable_count: int = 0
    comparable_dispersion_high: bool = False

    condition_penalty: float = 0

    liquidity_level: int = 5
    competition_level: int = 5
    source_trust_level: int = 3

    vehicle_identity_quality: float = 100
    auction_data_quality: float = 100
    condition_data_quality: float = 100
    cost_data_quality: float = 100


@app.post("/create-listing-and-score")
def create_listing_and_score(data: CreateListingAndScoreInput):

    conn = None
    cur = None

    try:
        conn = psycopg2.connect(
            os.environ["DATABASE_URL"]
        )
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO listings (
                raw_listing_id,
                source_id,
                title,
                category,
                brand,
                model,
                year,
                mileage_km,
                fuel_type,
                transmission,
                location,
                status,
                jump_url,
                created_at,
                updated_at
            )
            VALUES (
                %s::uuid,
                %s::uuid,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                NOW(),
                NOW()
            )
            RETURNING id;
            """,
            (
                data.raw_listing_id,
                data.source_id,
                data.title,
                data.category,
                data.brand,
                data.model,
                data.year,
                data.mileage_km,
                data.fuel_type,
                data.transmission,
                data.location,
                data.status,
                data.jump_url
            )
        )

        listing_id = cur.fetchone()[0]

        scoring_input = LotRankInput(
            market_value=data.market_value,
            safe_sale_value=data.safe_sale_value,
            current_bid=data.current_bid,
            auction_fees=data.auction_fees,
            transport_cost=data.transport_cost,
            repair_cost=data.repair_cost,
            other_costs=data.other_costs,
            target_profit=data.target_profit,
            risk_reserve=data.risk_reserve,
            comparable_count=data.comparable_count,
            comparable_dispersion_high=data.comparable_dispersion_high,
            condition_penalty=data.condition_penalty,
            liquidity_level=data.liquidity_level,
            competition_level=data.competition_level,
            source_trust_level=data.source_trust_level,
            vehicle_identity_quality=data.vehicle_identity_quality,
            auction_data_quality=data.auction_data_quality,
            condition_data_quality=data.condition_data_quality,
            cost_data_quality=data.cost_data_quality
        )

        result = score_preview(scoring_input)

        cur.execute(
            """
            INSERT INTO scores (
                listing_id,
                lotrank_score,
                confidence,
                lotrank_max,
                calculated_at
            )
            VALUES (
                %s::uuid,
                %s,
                %s,
                %s,
                NOW()
            );
            """,
            (
                str(listing_id),
                result["lotrank_score"],
                result["confidence"],
                result["lotrank_max"]
            )
        )

        conn.commit()

        cur.close()
        conn.close()

        return {
            "status": "ok",
            "listing_id": str(listing_id),
            "listing_title": data.title,
            "score_database_action": "CREATED",
            "analysis": result
        }

    except Exception as e:

        if conn:
            conn.rollback()

        if cur:
            cur.close()

        if conn:
            conn.close()

        return {
            "status": "error",
            "detail": str(e)
        }
