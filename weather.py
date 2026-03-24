"""
services/weather.py
Integração com OpenWeather API.
Cache por coordenada para evitar chamadas repetidas.
"""

import os
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("OPENWEATHER_API_KEY")
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

# Limites para alertas de risco operacional
WIND_SPEED_LIMIT_MS  = 10.0   # m/s (~36 km/h) — acima = risco
RAIN_VOLUME_LIMIT_MM = 5.0    # mm/h — acima = risco


@st.cache_data(ttl=1800, show_spinner=False)   # cache 30 min por coord
def get_weather(lat: float, lon: float) -> dict:
    """
    Consulta clima atual para uma coordenada.
    Retorna dicionário padronizado com flag de risco.
    """
    try:
        resp = requests.get(
            BASE_URL,
            params={
                "lat":   lat,
                "lon":   lon,
                "appid": API_KEY,
                "units": "metric",
                "lang":  "pt_br",
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        wind_speed   = data.get("wind", {}).get("speed", 0.0)
        rain_1h      = data.get("rain", {}).get("1h", 0.0)
        description  = data["weather"][0]["description"].capitalize()
        temp         = data["main"]["temp"]
        humidity     = data["main"]["humidity"]
        weather_id   = data["weather"][0]["id"]

        # Condições que impedem inspeção
        has_risk = (
            wind_speed > WIND_SPEED_LIMIT_MS
            or rain_1h  > RAIN_VOLUME_LIMIT_MM
            or weather_id in range(200, 300)   # tempestades
            or weather_id in range(600, 700)   # neve
        )

        return {
            "ok":          True,
            "descricao":   description,
            "temperatura": temp,
            "umidade":     humidity,
            "vento_ms":    wind_speed,
            "vento_kmh":   round(wind_speed * 3.6, 1),
            "chuva_mm":    rain_1h,
            "risco":       has_risk,
            "icone":       data["weather"][0]["icon"],
        }

    except Exception as exc:
        return {
            "ok":    False,
            "erro":  str(exc),
            "risco": False,
        }


def weather_badge(info: dict) -> str:
    """Retorna emoji resumido para exibir no mapa/tabela."""
    if not info.get("ok"):
        return "❓"
    if info["risco"]:
        return "⛔"
    if info["chuva_mm"] > 0:
        return "🌧️"
    if info["vento_kmh"] > 20:
        return "💨"
    return "✅"
