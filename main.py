from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv()

OPENMETEO_BASE = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_AIR_QUALITY_BASE = "https://air-quality-api.open-meteo.com/v1/air-quality"
NOMINATIM_BASE = "https://nominatim.openstreetmap.org/reverse"
APP_USER_AGENT = os.getenv("NOMINATIM_USER_AGENT", "CleanSky/1.0 (contact: your-email@example.com)")

app = FastAPI(title="CleanSky API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


app.add_middleware(CacheControlMiddleware)



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")



def safe_get(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 15) -> Any | None:
    try:
        response = requests.get(
            url,
            params=params or {},
            headers=headers or {},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"[ERROR] {url}: {exc}")
        return None



def aqi_label(aqi: float | None) -> str:
    if aqi is None:
        return "Unavailable"
    if aqi <= 20:
        return "Very Good"
    if aqi <= 40:
        return "Good"
    if aqi <= 60:
        return "Moderate"
    if aqi <= 80:
        return "Poor"
    if aqi <= 100:
        return "Very Poor"
    return "Extremely Poor"



def recommendation_text(aqi: float | None) -> str:
    if aqi is None:
        return "Live air-quality data is temporarily unavailable. You can still use the map and weather features."
    if aqi <= 20:
        return "Air quality is very good. Outdoor activities are generally fine."
    if aqi <= 40:
        return "Air quality is good. Most people can stay active outdoors normally."
    if aqi <= 60:
        return "Air quality is moderate. Sensitive people may prefer lighter outdoor activity."
    if aqi <= 80:
        return "Air quality is poor. Consider limiting intense outdoor exercise."
    if aqi <= 100:
        return "Air quality is very poor. Sensitive groups should reduce time outside."
    return "Air quality is extremely poor. Avoid prolonged outdoor exposure if possible."


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "CleanSky API", "time": utc_now_iso()}


@app.get("/air-quality")
def air_quality(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "pm10,pm2_5,nitrogen_dioxide,european_aqi",
        "timezone": "auto",
    }
    data = safe_get(OPENMETEO_AIR_QUALITY_BASE, params=params)

    if not data or "current" not in data:
        return {
            "aggregated": {"pm25": None, "pm10": None, "no2": None, "aqi": None},
            "aqi_label": "Unavailable",
            "recommendations": recommendation_text(None),
            "last_updated": utc_now_iso(),
            "locations": [],
            "source_unavailable": True,
        }

    current = data.get("current", {})
    pm25 = current.get("pm2_5")
    pm10 = current.get("pm10")
    no2 = current.get("nitrogen_dioxide")
    aqi = current.get("european_aqi")

    aggregated = {
        "pm25": pm25,
        "pm10": pm10,
        "no2": no2,
        "aqi": aqi,
    }

    locations_out = []
    if any(value is not None for value in [pm25, pm10, no2, aqi]):
        locations_out.append(
            {
                "location": f"Selected location ({lat:.3f}, {lon:.3f})",
                "coordinates": {"latitude": lat, "longitude": lon},
                "measurements": {k: v for k, v in aggregated.items() if v is not None and k != "aqi"},
            }
        )

    return {
        "aggregated": aggregated,
        "aqi_label": aqi_label(aqi),
        "recommendations": recommendation_text(aqi),
        "last_updated": current.get("time", utc_now_iso()),
        "locations": locations_out,
        "source_unavailable": False,
    }


@app.get("/weather")
def current_weather(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    params = {"latitude": lat, "longitude": lon, "current_weather": True, "timezone": "auto"}
    data = safe_get(OPENMETEO_BASE, params=params)
    if not data or "current_weather" not in data:
        return {
            "current_weather": {
                "temperature": None,
                "windspeed": None,
                "weathercode": None,
                "time": utc_now_iso(),
            },
            "source_unavailable": True,
        }
    return {"current_weather": data["current_weather"], "source_unavailable": False}


@app.get("/forecast")
def forecast(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    days: int = Query(14, ge=1, le=16),
) -> dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
        "forecast_days": days,
    }
    data = safe_get(OPENMETEO_BASE, params=params)
    if not data or "daily" not in data:
        base = datetime.now(timezone.utc)
        dates = [(base + timedelta(days=i)).date().isoformat() for i in range(days)]
        return {
            "daily": {
                "time": dates,
                "temperature_2m_max": [round(random.uniform(20, 35), 1) for _ in dates],
                "temperature_2m_min": [round(random.uniform(10, 20), 1) for _ in dates],
                "precipitation_sum": [round(random.uniform(0, 5), 1) for _ in dates],
            },
            "source_unavailable": True,
        }
    return {"daily": data["daily"], "source_unavailable": False}


@app.get("/reverse-geocode")
def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    params = {
        "format": "jsonv2",
        "lat": lat,
        "lon": lon,
        "addressdetails": 1,
    }
    headers = {
        "User-Agent": APP_USER_AGENT,
        "Accept-Language": "en",
    }
    data = safe_get(NOMINATIM_BASE, params=params, headers=headers)
    if not data:
        return {
            "display_name": None,
            "country": None,
            "country_code": None,
            "city": None,
            "source_unavailable": True,
        }

    address = data.get("address", {})
    return {
        "display_name": data.get("display_name"),
        "country": address.get("country"),
        "country_code": (address.get("country_code") or "").upper() or None,
        "city": address.get("city") or address.get("town") or address.get("village") or address.get("hamlet") or address.get("county"),
        "source_unavailable": False,
    }


app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
