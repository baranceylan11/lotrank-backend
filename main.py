from fastapi import FastAPI

app = FastAPI(title="LotRank Backend")

@app.get("/")
def root():
    return {"status": "ok", "service": "lotrank-backend"}

@app.get("/health")
def health():
    return {"status": "healthy"}
