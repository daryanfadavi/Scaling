import time
from fastapi import FastAPI

app = FastAPI()

BURN_SECONDS = 0.005  # ~5 ms of CPU per request


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/work")
def work():
    end = time.perf_counter() + BURN_SECONDS
    x = 0
    while time.perf_counter() < end:
        x += 1
    return {"done": True}