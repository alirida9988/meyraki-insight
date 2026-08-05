from fastapi import FastAPI

from meyraki_contracts import CONTRACT_VERSION, json_schemas

app = FastAPI(title="Meyraki Insight API", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "contracts": CONTRACT_VERSION}


@app.get("/contracts")
def contracts() -> dict:
    """JSON Schemas of every pipeline contract — consumed by the TS type generator."""
    return json_schemas()
