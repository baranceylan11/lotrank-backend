import os
import psycopg2
from fastapi import FastAPI

app = FastAPI(title="LotRank Backend")


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
