import hmac
import os
import psycopg2
import requests
from dataclasses import dataclass
from email.utils import parseaddr

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool


app = FastAPI(title="LotRank Backend")

SAFE_MAIL_ERROR_CODES = frozenset({
    "invalid_token",
    "missing_env",
    "resend_auth_failed",
    "resend_connect_failed",
    "resend_send_failed",
})


@dataclass(frozen=True)
class ResendSettings:
    api_key: str
    from_value: str
    from_address: str
    admin_email: str


class MailConfigurationError(Exception):
    pass


class MailDeliveryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _load_resend_settings():
    values = {
        "api_key": os.getenv("RESEND_API_KEY", ""),
        "from_value": os.getenv("MAIL_FROM", "").strip(),
        "admin_email": os.getenv("ADMIN_REPORT_EMAIL", "").strip(),
    }
    if not all(values.values()):
        raise MailConfigurationError

    from_address = parseaddr(values["from_value"])[1].lower()
    admin_email = parseaddr(values["admin_email"])[1]
    if from_address != "info@lotrank.ai" or not admin_email:
        raise MailConfigurationError

    return ResendSettings(
        api_key=values["api_key"],
        from_value=values["from_value"],
        from_address=from_address,
        admin_email=admin_email,
    )


def _mail_test_token_is_valid(provided_token):
    expected_token = os.getenv("MAIL_TEST_TOKEN", "")
    if not expected_token or not provided_token:
        return False
    return hmac.compare_digest(
        provided_token.encode("utf-8"),
        expected_token.encode("utf-8"),
    )


def _send_resend_test_email():
    settings = _load_resend_settings()
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Accept": "application/json",
            },
            json={
                "from": settings.from_value,
                "to": [settings.admin_email],
                "subject": "LotRank SMTP test başarılı",
                "text": "LotRank mail bağlantısı başarıyla doğrulandı.",
            },
            timeout=15,
        )
    except (requests.Timeout, requests.ConnectionError):
        raise MailDeliveryError("resend_connect_failed") from None
    except requests.RequestException:
        raise MailDeliveryError("resend_send_failed") from None

    if response.status_code in (401, 403):
        raise MailDeliveryError("resend_auth_failed")
    if not 200 <= response.status_code < 300:
        raise MailDeliveryError("resend_send_failed")


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


@app.post("/internal/mail/test")
async def send_mail_test(
    x_mail_test_token: str | None = Header(default=None, alias="X-Mail-Test-Token"),
):
    if not _mail_test_token_is_valid(x_mail_test_token):
        raise HTTPException(status_code=401, detail={"code": "invalid_token"})

    try:
        await run_in_threadpool(_send_resend_test_email)
    except MailConfigurationError:
        raise HTTPException(status_code=503, detail={"code": "missing_env"}) from None
    except MailDeliveryError as error:
        safe_code = (
            error.code
            if error.code in SAFE_MAIL_ERROR_CODES
            else "resend_send_failed"
        )
        raise HTTPException(status_code=502, detail={"code": safe_code}) from None
    except Exception:
        raise HTTPException(
            status_code=502,
            detail={"code": "resend_send_failed"},
        ) from None

    return {"status": "ok", "message": "Resend test email sent"}


@app.get(
    "/internal/mail/test-page",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def mail_test_page():
    content = """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LotRank mail testi</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 420px; margin: 64px auto; padding: 0 20px; }
    form { display: grid; gap: 14px; }
    label { font-weight: 700; }
    input, button { min-height: 44px; padding: 0 12px; font: inherit; }
    button { cursor: pointer; }
    #status { min-height: 24px; }
  </style>
</head>
<body>
  <h1>LotRank mail testi</h1>
  <form id="mail-test-form" action="/internal/mail/test" method="post">
    <label for="mail-test-token">MAIL_TEST_TOKEN</label>
    <input id="mail-test-token" type="password" required autocomplete="off" spellcheck="false">
    <button id="submit-button" type="submit">Test maili gönder</button>
    <p id="status" role="status" aria-live="polite"></p>
  </form>
  <script>
    const form = document.getElementById("mail-test-form");
    const tokenField = document.getElementById("mail-test-token");
    const submitButton = document.getElementById("submit-button");
    const status = document.getElementById("status");
    const safeErrorCodes = new Set([
      "invalid_token",
      "missing_env",
      "resend_auth_failed",
      "resend_connect_failed",
      "resend_send_failed"
    ]);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      let token = tokenField.value;
      tokenField.value = "";
      submitButton.disabled = true;
      status.textContent = "Gönderiliyor…";

      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: { "X-Mail-Test-Token": token, "Accept": "application/json" },
          credentials: "same-origin",
          cache: "no-store",
          referrerPolicy: "no-referrer"
        });
        if (response.ok) {
          status.textContent = "Mail gönderildi";
        } else {
          let code = "resend_send_failed";
          try {
            const payload = await response.json();
            const candidate = payload && payload.detail && payload.detail.code;
            if (safeErrorCodes.has(candidate)) code = candidate;
          } catch {}
          status.textContent = `Test maili gönderilemedi: ${code}`;
        }
      } catch {
        status.textContent = "Test maili gönderilemedi: resend_connect_failed";
      } finally {
        token = "";
        submitButton.disabled = false;
        tokenField.focus();
      }
    });
  </script>
</body>
</html>"""
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


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
from domaine_collector import collect_domaine_lot
@app.get("/test-domaine")
async def test_domaine():
    url = "https://encheres-domaine.gouv.fr/lot/audiq7-1-doo-1.html"

    try:
        result = await collect_domaine_lot(url)
        return result
    except Exception as e:
        return {
            "status": "error",
            "detail": str(e)
        }
# =========================================================
# DOMAINE IMPORT + AUTO SCORE
# =========================================================

class DomaineImportInput(BaseModel):
    url: str
    market_value: float
    safe_sale_value: float

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
    source_trust_level: int = 5

    vehicle_identity_quality: float = 100
    auction_data_quality: float = 100
    condition_data_quality: float = 90
    cost_data_quality: float = 90


@app.post("/import-domaine")
async def import_domaine(data: DomaineImportInput):

    try:
        collected = await collect_domaine_lot(data.url)
        parsed = collected.get("parsed", {})

        if not parsed:
            return {
                "status": "error",
                "detail": "Domaine ilan verisi okunamadi."
            }

        current_bid = parsed.get("current_bid")

        if current_bid is None:
            return {
                "status": "error",
                "detail": "Mevcut ihale fiyati bulunamadi."
            }

        auction_fee_pct = parsed.get("auction_fee_pct", 11)

        auction_fees = (
            current_bid * auction_fee_pct / 100
        )

        fuel_type = parsed.get("fuel_type")

        if fuel_type:
            if fuel_type.lower() == "gazole":
                fuel_type = "DIESEL"
            elif fuel_type.lower() == "essence":
                fuel_type = "PETROL"

        transmission = parsed.get("transmission")

        if transmission:
            if "automatique" in transmission.lower():
                transmission = "automatic"
            elif "manuelle" in transmission.lower():
                transmission = "manual"

        title_parts = [
            parsed.get("brand"),
            parsed.get("model")
        ]

        title = " ".join(
            part for part in title_parts if part
        )

        listing_input = CreateListingAndScoreInput(
            source_id="aab590a1-85f9-41f5-8c0e-0822b12ed776",
            raw_listing_id=None,

            title=title or "Domaine Vehicle",
            category="car",
            brand=parsed.get("brand"),
            model=parsed.get("model"),
            year=parsed.get("year"),
            mileage_km=parsed.get("mileage_km"),
            fuel_type=fuel_type,
            transmission=transmission,
            location=parsed.get("location"),
            status="active",
            jump_url=data.url,

            market_value=data.market_value,
            safe_sale_value=data.safe_sale_value,
            current_bid=current_bid,

            auction_fees=auction_fees,
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
        result = create_listing_and_score(
            listing_input
        )

        # Domaine risklerini risk_flags tablosuna kaydet
        listing_id = result.get("listing_id")
        domaine_risks = parsed.get("risk_flags", [])

        saved_risks = 0

        if listing_id and domaine_risks:
            conn = psycopg2.connect(
                os.environ["DATABASE_URL"]
            )
            cur = conn.cursor()

            for risk in domaine_risks:
                cur.execute(
                    """
                    INSERT INTO risk_flags (
                        listing_id,
                        flag_type,
                        description
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (
                        listing_id,
                        risk,
                        risk
                    )
                )
                saved_risks += 1

            conn.commit()
            cur.close()
            conn.close()

        return {
            "status": "ok",
            "source": "Encheres du Domaine",
            "collected_vehicle": parsed,
            "risk_flags": domaine_risks,
            "risk_flags_saved": saved_risks,
            "database_result": result
        }

    except Exception as e:
        return {
            "status": "error",
            "detail": str(e)
        }
      
