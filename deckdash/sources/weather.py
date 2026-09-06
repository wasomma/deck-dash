"""Open-Meteo (no key, CC BY 4.0). Location is looked up once from the public IP and cached."""

from __future__ import annotations

import logging

import requests

from .. import config
from .base import Poller

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
LOCATE_URL = "https://ipinfo.io/json"


def parse_openmeteo(j: dict) -> dict:
    c = j["current"]
    current = {
        "time": c["time"],
        "temp": c["temperature_2m"],
        "feels": c["apparent_temperature"],
        "humidity": c["relative_humidity_2m"],
        "code": int(c["weather_code"]),
        "wind": c["wind_speed_10m"],
        "is_day": bool(c["is_day"]),
    }
    now_hour = c["time"][:13]
    h = j["hourly"]
    hourly = []
    for i, t in enumerate(h["time"]):
        if t[:13] <= now_hour:
            continue
        hourly.append({
            "time": t,
            "temp": h["temperature_2m"][i],
            "code": int(h["weather_code"][i]),
            "precip": h["precipitation_probability"][i] or 0,
            "is_day": bool(h["is_day"][i]),
        })
        if len(hourly) >= 24:
            break
    d = j["daily"]
    daily = [
        {
            "date": d["time"][i],
            "code": int(d["weather_code"][i]),
            "hi": d["temperature_2m_max"][i],
            "lo": d["temperature_2m_min"][i],
            "precip": d["precipitation_probability_max"][i] or 0,
            "sunrise": d["sunrise"][i],
            "sunset": d["sunset"][i],
        }
        for i in range(len(d["time"]))
    ]
    return {"current": current, "hourly": hourly, "daily": daily}


class WeatherPoller(Poller):
    def __init__(self, cfg: dict):
        w = cfg.get("weather", {})
        super().__init__("weather", w.get("refresh_minutes", 10) * 60)
        self.lat = float(w.get("latitude", 0.0))
        self.lon = float(w.get("longitude", 0.0))
        self.place = w.get("place", "")
        self.units = w.get("units", "imperial")

    def fetch(self) -> dict:
        if not self.lat and not self.lon:
            self._locate()
        params = {
            "latitude": self.lat,
            "longitude": self.lon,
            "timezone": "auto",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
            "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
            "forecast_days": 6,
        }
        if self.units == "imperial":
            params.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
        r = requests.get(FORECAST_URL, params=params, timeout=15)
        r.raise_for_status()
        data = parse_openmeteo(r.json())
        data["place"] = self.place
        data["units"] = self.units
        return data

    def _locate(self) -> None:
        r = requests.get(LOCATE_URL, timeout=10)
        r.raise_for_status()
        j = r.json()
        lat, lon = (float(x) for x in j["loc"].split(","))
        place = ", ".join(p for p in (j.get("city"), j.get("region")) if p)
        self.lat, self.lon, self.place = lat, lon, place
        config.write_local({"weather": {"latitude": lat, "longitude": lon, "place": place}})
        log.info("weather location cached: %s (%.3f, %.3f)", place, lat, lon)
