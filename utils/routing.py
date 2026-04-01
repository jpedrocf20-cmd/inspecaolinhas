"""
utils/routing.py
Otimização de rota de inspeção.

Baseado em OS (não SS).
Coordenadas obtidas via COD_ATIVO (JOIN feito na data layer).
Algoritmo: Nearest Neighbor (TSP heurístico O(n²)).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────
# DISTÂNCIA HAVERSINE
# ──────────────────────────────────────────────

def _haversine_matrix(coords: np.ndarray) -> np.ndarray:
    """Matriz de distâncias geodésicas em km (Haversine)."""
    R    = 6371.0
    lat  = np.radians(coords[:, 0])
    lon  = np.radians(coords[:, 1])
    n    = len(coords)
    dist = np.zeros((n, n))

    for i in range(n):
        dlat    = lat - lat[i]
        dlon    = lon - lon[i]
        a       = np.sin(dlat / 2) ** 2 + np.cos(lat[i]) * np.cos(lat) * np.sin(dlon / 2) ** 2
        dist[i] = 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

    return dist


# ──────────────────────────────────────────────
# NEAREST NEIGHBOR — TSP HEURÍSTICO
# ──────────────────────────────────────────────

def otimizar_rota(
    df: pd.DataFrame,
    ponto_partida: tuple[float, float] | None = None,
) -> pd.DataFrame:
    """
    Ordena as OS na sequência de visita que minimiza a distância total
    usando o algoritmo Nearest Neighbor.

    Parâmetros
    ----------
    df             : DataFrame com colunas LATITUDE e LONGITUDE (de VW_TORRES_COM_CRITICIDADE via COD_ATIVO)
    ponto_partida  : (lat, lon) opcional — início da rota; se None usa a primeira OS da lista

    Retorna
    -------
    DataFrame com colunas adicionais:
      ORDEM_VISITA  : sequência (1, 2, 3...)
      DIST_PROX_KM  : distância até o próximo ponto
      DIST_ACUM_KM  : distância acumulada
    """
    if df.empty:
        return df

    # Remove linhas sem coordenada (nulos já deveriam ter sido removidos no data layer)
    df = df.dropna(subset=["LATITUDE", "LONGITUDE"]).reset_index(drop=True)
    if df.empty:
        return df

    coords      = df[["LATITUDE", "LONGITUDE"]].values
    dist_matrix = _haversine_matrix(coords)
    n           = len(df)
    visitado    = [False] * n

    # Ponto inicial
    if ponto_partida:
        p      = np.array([[ponto_partida[0], ponto_partida[1]]])
        dists  = _haversine_matrix(np.vstack([p, coords]))[0, 1:]
        atual  = int(np.argmin(dists))
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

    df_rota = df.iloc[rota].copy()
    df_rota["ORDEM_VISITA"] = range(1, len(df_rota) + 1)

    # Distâncias
    dist_prox = []
    for i, idx in enumerate(rota):
        dist_prox.append(
            round(dist_matrix[idx][rota[i + 1]], 2) if i < len(rota) - 1 else 0.0
        )

    df_rota["DIST_PROX_KM"] = dist_prox
    df_rota["DIST_ACUM_KM"] = df_rota["DIST_PROX_KM"].cumsum().round(2)

    return df_rota.reset_index(drop=True)


# ──────────────────────────────────────────────
# RESUMO DA ROTA
# ──────────────────────────────────────────────

def resumo_rota(df_rota: pd.DataFrame) -> dict:
    if df_rota is None or df_rota.empty:
        return {}

    atrasadas = int((df_rota.get("STATUS_PRAZO", pd.Series(dtype=str)) == "ATRASADA").sum())

    return {
        "total_os":         len(df_rota),
        "os_atrasadas":     atrasadas,
        "distancia_total":  float(df_rota["DIST_PROX_KM"].sum().round(1)),
        "criticidade_min":  int(df_rota["CRITICIDADE"].min()) if "CRITICIDADE" in df_rota.columns and len(df_rota) else "-",
        "score_medio":      float(df_rota["SCORE"].mean().round(1)) if "SCORE" in df_rota.columns and len(df_rota) else 0,
    }
