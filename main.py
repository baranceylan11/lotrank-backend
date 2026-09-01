import os
import psycopg2
from fastapi import FastAPI

app = FastAPI(title="LotRank Backend")

@app.get("/")
def root():
    return {"status": "ok", "service": "lotrank-backend"}

@app.get("/health")
def health():
    return {"status": "healthy"}

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
