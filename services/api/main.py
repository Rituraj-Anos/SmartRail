from fastapi import FastAPI

app = FastAPI(
    title="SmartRail API",
    description="AI-Powered Train Traffic Control System",
    version="0.1.0",
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "api"}
