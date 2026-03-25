"""
utils/routing.py
Algoritmo de priorização e otimização de rota de inspeção.

Fluxo:
  1. Calcular SCORE de prioridade por torre (0–100%)
  2. Filtrar torres com risco climático (se modo conservador)
  3. Ordenar por score e selecionar top N torres
  4. Otimizar sequência de visita via Nearest Neighbor (TSP heurístico)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist


# ──────────────────────────────────────────────
# 1. SCORE DE PRIORIZAÇÃO (0–100%)
# ──────────────────────────────────────────────
# Componentes e pesos máximos teóricos:
#   Criticidade : (7 - nível) * 10  → máx 60 pts  (nível 1 = mais crítico)
#   Ocorrências : QTD_SS * 2        → máx 40 pts  (cap em 20 SS)
#   Atraso      : |saldo| * 5       → máx 100 pts (cap em 20 dias)
# Total bruto máximo = 200 pts → normalizado para 0–100%

_SCORE_MAX_BRUTO = 200.0


def calcular_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adiciona coluna SCORE ao DataFrame (0.0 a 100.0, em %).

    Componentes:
      - Criticidade : (7 - CRITICIDADE_MIN) × 10   [máx 60]
      - Ocorrências : min(QTD_SS, 20) × 2           [máx 40]
      - Atraso      : min(|PIOR_SALDO_DIAS|, 20) × 5 [máx 100, só se atrasado]

    100% = torre com o pior cenário possível (nível 1, 20+ OS, 20+ dias atrasada)
    """
    df = df.copy()

    score_criticidade = (7 - df["CRITICIDADE_MIN"].clip(1, 6)) * 10
    score_ocorrencias = df["QTD_SS"].clip(upper=20) * 2
    score_atraso = np.where(
        df["FL_ATRASADO"] == 1,
        df["PIOR_SALDO_DIAS"].abs().clip(upper=20) * 5,
        0,
    )

    score_bruto = score_criticidade + score_ocorrencias + score_atraso
    df["SCORE"] = (score_bruto / _SCORE_MAX_BRUTO * 100).round(1)
    return df


# ──────────────────────────────────────────────
# 2. FILTRO CLIMÁTICO
# ──────────────────────────────────────────────

def aplicar_filtro_clima(
    df: pd.DataFrame,
    weather_map: dict[str, dict],
    modo_conservador: bool = True,
) -> pd.DataFrame:
    df = df.copy()
    df["CLIMA_RISCO"] = df["COD_ATIVO"].map(
        lambda c: weather_map.get(c, {}).get("risco", False)
    )
    if modo_conservador:
        df = df[~df["CLIMA_RISCO"]]
    return df


# ──────────────────────────────────────────────
# 3. SELEÇÃO DAS TORRES A VISITAR
# ──────────────────────────────────────────────

def selecionar_torres(
    df: pd.DataFrame,
    max_torres: int = 20,
    forcar_atrasadas: bool = True,
) -> pd.DataFrame:
    df = df.sort_values("SCORE", ascending=False)

    if forcar_atrasadas:
        atrasadas    = df[df["FL_ATRASADO"] == 1].head(max_torres)
        restantes    = df[df["FL_ATRASADO"] == 0]
        slots_livres = max(0, max_torres - len(atrasadas))
        selecionadas = pd.concat([atrasadas, restantes.head(slots_livres)])
    else:
        selecionadas = df.head(max_torres)

    return selecionadas.reset_index(drop=True)


# ──────────────────────────────────────────────
# 4. OTIMIZAÇÃO DA SEQUÊNCIA (TSP Nearest Neighbor)
# ──────────────────────────────────────────────

def _haversine_matrix(coords: np.ndarray) -> np.ndarray:
    R = 6371.0
    lat = np.radians(coords[:, 0])
    lon = np.radians(coords[:, 1])
    n   = len(coords)
    dist = np.zeros((n, n))
    for i in range(n):
        dlat = lat - lat[i]
        dlon = lon - lon[i]
        a    = np.sin(dlat / 2) ** 2 + np.cos(lat[i]) * np.cos(lat) * np.sin(dlon / 2) ** 2
        dist[i] = 2 * R * np.arcsin(np.sqrt(a))
    return dist


def otimizar_rota(
    df: pd.DataFrame,
    ponto_partida: tuple[float, float] | None = None,
) -> pd.DataFrame:
    if len(df) == 0:
        return df

    coords      = df[["LATITUDE", "LONGITUDE"]].values
    dist_matrix = _haversine_matrix(coords)
    n           = len(df)
    visitado    = [False] * n

    if ponto_partida:
        partida_coord = np.array([[ponto_partida[0], ponto_partida[1]]])
        dists_partida = _haversine_matrix(np.vstack([partida_coord, coords]))[0, 1:]
        atual = int(np.argmin(dists_partida))
    else:
        atual = 0

    rota = [atual]
    visitado[atual] = True

    for _ in range(n - 1):
        distancias = dist_matrix[atual].copy()
        distancias[visitado] = np.inf
        proximo = int(np.argmin(distancias))
        rota.append(proximo)
        visitado[proximo] = True
        atual = proximo

    df_rota              = df.iloc[rota].copy()
    df_rota["ORDEM_VISITA"] = range(1, len(df_rota) + 1)

    dist_prox = []
    for i, idx in enumerate(rota):
        if i < len(rota) - 1:
            dist_prox.append(round(dist_matrix[idx][rota[i + 1]], 2))
        else:
            dist_prox.append(0.0)

    df_rota["DIST_PROX_KM"] = dist_prox
    df_rota["DIST_ACUM_KM"] = df_rota["DIST_PROX_KM"].cumsum().round(2)
    return df_rota.reset_index(drop=True)


# ──────────────────────────────────────────────
# 5. RESUMO DA ROTA
# ──────────────────────────────────────────────

def resumo_rota(df_rota: pd.DataFrame) -> dict:
    return {
        "total_torres":     len(df_rota),
        "torres_atrasadas": int(df_rota["FL_ATRASADO"].sum()),
        "distancia_total":  float(df_rota["DIST_PROX_KM"].sum().round(1)),
        "criticidade_min":  int(df_rota["CRITICIDADE_MIN"].min()) if len(df_rota) else "-",
        "score_medio":      float(df_rota["SCORE"].mean().round(1)) if len(df_rota) else 0,
    }
