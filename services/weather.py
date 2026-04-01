"""
services/weather.py
Integração com OpenWeather API.
  - Clima atual por coordenada
  - Previsão de 5 dias (forecast)
  - NÃO filtra torres — apenas fornece informação de apoio
"""

from __future__ import annotations

import os
from datetime import datetime
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

_BASE_CURRENT  = "https://api.openweathermap.org/data/2.5/weather"
_BASE_FORECAST = "https://api.openweathermap.org/data/2.5/forecast"

WIND_SPEED_LIMIT_MS  = 10.0   # m/s (~36 km/h)
RAIN_VOLUME_LIMIT_MM = 5.0    # mm/h


def _get_api_key() -> str:
    key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if key:
        return key
    try:
        key = st.secrets.get("OPENWEATHER_API_KEY", "").strip()
        if key:
            return key
    except Exception:
        pass
    return ""


# ──────────────────────────────────────────────
# CLIMA ATUAL
# ──────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def get_weather(lat: float, lon: float) -> dict:
    """Retorna clima atual para uma coordenada."""
    api_key = _get_api_key()
    if not api_key:
        return {"ok": False, "erro": "API_KEY não configurada.", "risco": False}

    try:
        resp = requests.get(
            _BASE_CURRENT,
            params={"lat": lat, "lon": lon, "appid": api_key, "units": "metric", "lang": "pt_br"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        wind_speed  = data.get("wind", {}).get("speed", 0.0)
        rain_1h     = data.get("rain", {}).get("1h", 0.0)
        weather_id  = data["weather"][0]["id"]

        has_risk = (
            wind_speed > WIND_SPEED_LIMIT_MS
            or rain_1h  > RAIN_VOLUME_LIMIT_MM
            or weather_id in range(200, 300)
            or weather_id in range(600, 700)
        )

        return {
            "ok":          True,
            "descricao":   data["weather"][0]["description"].capitalize(),
            "temperatura": data["main"]["temp"],
            "umidade":     data["main"]["humidity"],
            "vento_ms":    wind_speed,
            "vento_kmh":   round(wind_speed * 3.6, 1),
            "chuva_mm":    rain_1h,
            "risco":       has_risk,
            "icone":       data["weather"][0]["icon"],
        }
    except Exception as exc:
        return {"ok": False, "erro": str(exc), "risco": False}


# ──────────────────────────────────────────────
# PREVISÃO 5 DIAS
# ──────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def get_forecast_5d(lat: float, lon: float) -> list[dict]:
    """
    Retorna previsão de 5 dias agrupada por dia.
    NÃO filtra torres — é apenas informação de apoio para o inspetor.

    Retorna lista de dicts:
      [ { data, temp_min, temp_max, descricao, icone, chuva_mm, vento_kmh, risco } ]
    """
    api_key = _get_api_key()
    if not api_key:
        return []

    try:
        resp = requests.get(
            _BASE_FORECAST,
            params={"lat": lat, "lon": lon, "appid": api_key, "units": "metric", "lang": "pt_br", "cnt": 40},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # Agrupa por dia (a API retorna intervalos de 3h)
        dias: dict[str, dict] = {}
        for item in data.get("list", []):
            dt   = datetime.fromtimestamp(item["dt"])
            dia  = dt.strftime("%d/%m")
            temp = item["main"]["temp"]
            wind = item.get("wind", {}).get("speed", 0.0)
            rain = item.get("rain", {}).get("3h", 0.0)
            wid  = item["weather"][0]["id"]

            if dia not in dias:
                dias[dia] = {
                    "data":      dia,
                    "temp_min":  temp,
                    "temp_max":  temp,
                    "descricao": item["weather"][0]["description"].capitalize(),
                    "icone":     item["weather"][0]["icon"],
                    "chuva_mm":  0.0,
                    "vento_kmh": round(wind * 3.6, 1),
                    "risco":     False,
                    "_wid":      wid,
                }
            else:
                dias[dia]["temp_min"] = min(dias[dia]["temp_min"], temp)
                dias[dia]["temp_max"] = max(dias[dia]["temp_max"], temp)
                dias[dia]["chuva_mm"] += rain

            # Vento máximo do dia
            if round(wind * 3.6, 1) > dias[dia]["vento_kmh"]:
                dias[dia]["vento_kmh"] = round(wind * 3.6, 1)
                dias[dia]["descricao"] = item["weather"][0]["description"].capitalize()
                dias[dia]["icone"]     = item["weather"][0]["icon"]

            # Risco se qualquer período do dia tiver risco
            if (
                wind > WIND_SPEED_LIMIT_MS
                or rain > RAIN_VOLUME_LIMIT_MM
                or wid in range(200, 300)
                or wid in range(600, 700)
            ):
                dias[dia]["risco"] = True

        result = list(dias.values())[:5]
        for d in result:
            d.pop("_wid", None)
            d["temp_min"] = round(d["temp_min"], 1)
            d["temp_max"] = round(d["temp_max"], 1)
            d["chuva_mm"] = round(d["chuva_mm"], 1)

        return result

    except Exception:
        return []


def weather_badge(info: dict) -> str:
    if not info.get("ok"):
        return "❓"
    if info.get("risco"):
        return "⛔"
    if info.get("chuva_mm", 0) > 0:
        return "🌧️"
    if info.get("vento_kmh", 0) > 20:
        return "💨"
    return "✅"
