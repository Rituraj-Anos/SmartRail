from fastapi import FastAPI

app = FastAPI(
    title="SmartRail Optimization Engine",
    description="Event Processor & Optimization Service",
    version="0.1.0",
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "optimization"}
