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

BASE_URL = "https://api.openweathermap.org/data/2.5/weather"

# Limites para alertas de risco operacional
WIND_SPEED_LIMIT_MS  = 10.0   # m/s (~36 km/h) — acima = risco
RAIN_VOLUME_LIMIT_MM = 5.0    # mm/h — acima = risco


def _get_api_key() -> str:
    """
    Lê a API key em tempo de execução (não no import).
    Ordem: variável de ambiente → st.secrets (Streamlit Cloud).
    """
    # 1. Variável de ambiente / .env (funciona localmente)
    key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if key:
        return key

    # 2. st.secrets (Streamlit Cloud) — lido em runtime, não no import
    try:
        key = st.secrets.get("OPENWEATHER_API_KEY", "").strip()
        if key:
            return key
    except Exception:
        pass

    return ""


@st.cache_data(ttl=1800, show_spinner=False)   # cache 30 min por coord
def get_weather(lat: float, lon: float) -> dict:
    """
    Consulta clima atual para uma coordenada.
    Retorna dicionário padronizado com flag de risco.
    """
    api_key = _get_api_key()

    if not api_key:
        return {
            "ok":    False,
            "erro":  "API_KEY não encontrada. Configure OPENWEATHER_API_KEY nos Secrets do Streamlit Cloud.",
            "risco": False,
        }

    try:
        resp = requests.get(
            BASE_URL,
            params={
                "lat":   lat,
                "lon":   lon,
                "appid": api_key,
                "units": "metric",
                "lang":  "pt_br",
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        wind_speed  = data.get("wind", {}).get("speed", 0.0)
        rain_1h     = data.get("rain", {}).get("1h", 0.0)
        description = data["weather"][0]["description"].capitalize()
        temp        = data["main"]["temp"]
        humidity    = data["main"]["humidity"]
        weather_id  = data["weather"][0]["id"]

        has_risk = (
            wind_speed > WIND_SPEED_LIMIT_MS
            or rain_1h  > RAIN_VOLUME_LIMIT_MM
            or weather_id in range(200, 300)
            or weather_id in range(600, 700)
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
    if info.get("chuva_mm", 0) > 0:
        return "🌧️"
    if info.get("vento_kmh", 0) > 20:
        return "💨"
    return "✅"
