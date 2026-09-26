from __future__ import annotations

from fastapi import FastAPI

from .serialization import network_from_dict

app = FastAPI(title="Transit Planner", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/network/validate")
def validate_network(payload: dict) -> dict:
    network = network_from_dict(payload)
    errors = network.validate()
    return {"valid": not errors, "errors": errors}
