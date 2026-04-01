"""
utils/routing.py  —  Engine de roteirização de inspeções — nível PRODUÇÃO

Pipeline
--------
1. calcular_urgencia()      → score 0-100 baseado em prazo e atraso
2. clusterizar()            → agrupa torres por proximidade (DBSCAN / KMeans)
3. calcular_score_hibrido() → urgência + bônus geográfico de cluster
4. selecionar_os()          → garante atrasadas + completa por score híbrido
5. otimizar_rota()          → Nearest Neighbor + melhoria 2-opt por cluster
6. resumo_rota()            → métricas consolidadas

Fórmula do Score Híbrido
------------------------
    SCORE = 0.55 * score_urgencia
          + 0.30 * bonus_cluster
          + 0.15 * bonus_densidade

Todos normalizados 0-100. Score final: 0-100.

Evolução VRP (futuro)
---------------------
  multi-equipe : particionar clusters por equipe, rodar otimizar_rota() em cada um
  multi-dia    : filtrar DATA_LIMITE <= D+n antes de clusterizar
  janela tempo : coluna JANELA_INICIO/FIM filtrada em selecionar_os()
"""
from __future__ import annotations

import warnings
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ── Pesos do score híbrido (soma = 1.0) ──
W_URGENCIA  = 0.55
W_CLUSTER   = 0.30
W_DENSIDADE = 0.15

JANELA_ALTA_DIAS   = 7
RAIO_DENSIDADE_KM  = 50.0
DBSCAN_EPS_KM      = 80.0
DBSCAN_MIN_PTS     = 2
DOIS_OPT_MAX_ITER  = 3


# ═══════════════════════════════════════════════════════════════════════
# HAVERSINE
# ═══════════════════════════════════════════════════════════════════════

def _haversine_matrix(coords: np.ndarray) -> np.ndarray:
    R   = 6371.0
    lat = np.radians(coords[:, 0])
    lon = np.radians(coords[:, 1])
    n   = len(coords)
    D   = np.zeros((n, n))
    for i in range(n):
        dlat = lat - lat[i]
        dlon = lon - lon[i]
        a    = np.sin(dlat/2)**2 + np.cos(lat[i]) * np.cos(lat) * np.sin(dlon/2)**2
        D[i] = 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return D


# ═══════════════════════════════════════════════════════════════════════
# ETAPA 1 — SCORE DE URGÊNCIA
# ═══════════════════════════════════════════════════════════════════════

def calcular_urgencia(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adiciona: PRIORIDADE (1/2/3), DIAS_VENCER, SCORE_URG (0-100).

    Fórmula SCORE_URG
    -----------------
    base_prioridade  = (4 - P) * 30         máx 90
    bonus_atraso     = min(atraso, 60)*0.15  máx 9
    bonus_vencimento = (7-dias)/7 * 10       máx 10  (só P=2)
    Total clipado em 100.
    """
    df   = df.copy()
    hoje = date.today()

    def _calc(row):
        status      = str(row.get("STATUS_PRAZO", "")).strip().upper()
        dias_atraso = float(pd.to_numeric(row.get("DIAS_ATRASO", 0), errors="coerce") or 0)

        dias_vencer = None
        try:
            limite = pd.to_datetime(row.get("DATA_LIMITE"))
            if pd.notna(limite):
                dias_vencer = (limite.date() - hoje).days
        except Exception:
            pass

        if status == "ATRASADA" or (dias_vencer is not None and dias_vencer < 0):
            p = 1
        elif dias_vencer is not None and 0 <= dias_vencer <= JANELA_ALTA_DIAS:
            p = 2
        else:
            p = 3

        base    = (4 - p) * 30.0
        b_at    = min(dias_atraso, 60) * 0.15
        b_venc  = 0.0
        if p == 2 and dias_vencer is not None:
            b_venc = max(0.0, (JANELA_ALTA_DIAS - dias_vencer) / JANELA_ALTA_DIAS) * 10.0

        score = float(np.clip(base + b_at + b_venc, 0, 100))
        dv    = dias_vencer if dias_vencer is not None else 0.0
        return p, dv, score

    res               = df.apply(_calc, axis=1)
    df["PRIORIDADE"]  = res.apply(lambda t: t[0])
    df["DIAS_VENCER"] = res.apply(lambda t: t[1])
    df["SCORE_URG"]   = res.apply(lambda t: t[2])
    return df


# ═══════════════════════════════════════════════════════════════════════
# ETAPA 2 — CLUSTERIZAÇÃO GEOGRÁFICA
# ═══════════════════════════════════════════════════════════════════════

def clusterizar(
    df: pd.DataFrame,
    metodo: str = "dbscan",
    n_clusters: int = 5,
) -> pd.DataFrame:
    """
    Adiciona coluna CLUSTER.

    DBSCAN (padrão): k automático, detecta outliers, ideal para linhas de transmissão.
    KMeans (fallback): k fixo, útil quando torres estão uniformemente distribuídas.
    Outliers DBSCAN (-1) são realocados ao cluster mais próximo.
    """
    df = df.copy()
    coords_val = df.dropna(subset=["LATITUDE", "LONGITUDE"])
    if len(coords_val) < 2:
        df["CLUSTER"] = 0
        return df

    coords = coords_val[["LATITUDE", "LONGITUDE"]].values

    if metodo == "dbscan":
        try:
            from sklearn.cluster import DBSCAN
            eps    = DBSCAN_EPS_KM / 111.0
            labels = DBSCAN(eps=eps, min_samples=DBSCAN_MIN_PTS,
                            metric="euclidean").fit_predict(coords)

            # Realocar outliers ao cluster mais próximo
            if -1 in labels:
                centroides = {}
                for lbl in set(labels):
                    if lbl != -1:
                        centroides[lbl] = coords[labels == lbl].mean(axis=0)
                if centroides:
                    c_arr = np.array(list(centroides.values()))
                    c_ids = list(centroides.keys())
                    for i, lbl in enumerate(labels):
                        if lbl == -1:
                            labels[i] = c_ids[int(np.argmin(np.linalg.norm(c_arr - coords[i], axis=1)))]
                else:
                    labels[:] = 0
        except ImportError:
            metodo = "kmeans"

    if metodo == "kmeans":
        try:
            from sklearn.cluster import KMeans
            k      = min(n_clusters, len(coords_val))
            labels = KMeans(n_clusters=k, random_state=42, n_init="auto").fit_predict(coords)
        except ImportError:
            labels = np.zeros(len(coords_val), dtype=int)

    mapa   = {v: i for i, v in enumerate(sorted(set(labels)))}
    labels = np.array([mapa[l] for l in labels])

    df.loc[coords_val.index, "CLUSTER"] = labels.astype(int)
    df["CLUSTER"] = df["CLUSTER"].fillna(0).astype(int)
    return df


# ═══════════════════════════════════════════════════════════════════════
# ETAPA 3 — SCORE HÍBRIDO
# ═══════════════════════════════════════════════════════════════════════

def calcular_score_hibrido(df: pd.DataFrame) -> pd.DataFrame:
    """
    SCORE = W_URGENCIA*SCORE_URG + W_CLUSTER*bonus_cluster + W_DENSIDADE*bonus_densidade

    bonus_cluster   : 100 ao maior cluster, proporcional aos demais
    bonus_densidade : % de torres no raio RAIO_DENSIDADE_KM, normalizado 0-100
    """
    df = df.copy()
    if "SCORE_URG" not in df.columns:
        df["SCORE_URG"] = 50.0
    if "CLUSTER" not in df.columns:
        df["CLUSTER"] = 0

    tam = df["CLUSTER"].value_counts().to_dict()
    mx  = max(tam.values(), default=1)
    df["BONUS_CLUSTER"] = df["CLUSTER"].map(lambda c: round(100.0 * tam.get(c, 1) / mx, 2))

    coords = df[["LATITUDE", "LONGITUDE"]].values
    if len(coords) > 1 and not np.isnan(coords).any():
        D        = _haversine_matrix(coords)
        vizinhos = (D < RAIO_DENSIDADE_KM).sum(axis=1) - 1
        mx_viz   = max(vizinhos.max(), 1)
        df["BONUS_DENSIDADE"] = np.round(100.0 * vizinhos / mx_viz, 2)
    else:
        df["BONUS_DENSIDADE"] = 50.0

    df["SCORE"] = (
        W_URGENCIA  * df["SCORE_URG"]
        + W_CLUSTER * df["BONUS_CLUSTER"]
        + W_DENSIDADE * df["BONUS_DENSIDADE"]
    ).clip(0, 100).round(1)

    return df


# ═══════════════════════════════════════════════════════════════════════
# ETAPA 4 — SELEÇÃO DE OS
# ═══════════════════════════════════════════════════════════════════════

def selecionar_os(
    df: pd.DataFrame,
    max_os: int = 20,
    forcar_atrasadas: bool = True,
    modo_cluster: bool = True,
) -> pd.DataFrame:
    """
    1. Inclui todas as ATRASADAS (se forcar_atrasadas=True).
    2. Completa com OS do cluster dominante (se modo_cluster=True),
       ordenadas por SCORE descendente.
    3. Ordenação final: PRIORIDADE ASC → SCORE DESC.
    """
    if df.empty:
        return df
    df = df.copy()
    if "SCORE" not in df.columns:
        df["SCORE"] = df.get("SCORE_URG", 50.0)

    atrasadas = df[df["PRIORIDADE"] == 1].copy() if forcar_atrasadas else pd.DataFrame()
    restantes = df[df["PRIORIDADE"] != 1].copy() if forcar_atrasadas else df.copy()
    slots     = max(0, max_os - len(atrasadas))

    if modo_cluster and "CLUSTER" in restantes.columns and slots > 0:
        contagem    = restantes["CLUSTER"].value_counts()
        top_cluster = contagem.index[0] if not contagem.empty else -1
        no_top      = restantes[restantes["CLUSTER"] == top_cluster].sort_values("SCORE", ascending=False)
        fora        = restantes[restantes["CLUSTER"] != top_cluster].sort_values("SCORE", ascending=False)
        complemento = pd.concat([no_top, fora]).head(slots)
    else:
        complemento = restantes.sort_values("SCORE", ascending=False).head(slots)

    return (
        pd.concat([atrasadas, complemento])
        .sort_values(["PRIORIDADE", "SCORE"], ascending=[True, False])
        .reset_index(drop=True)
    )


# Alias de compatibilidade com código existente
def selecionar_inspecoes(df, max_os=20, forcar_atrasadas=True):
    return selecionar_os(df, max_os, forcar_atrasadas, modo_cluster=True)


# ═══════════════════════════════════════════════════════════════════════
# ETAPA 5 — NEAREST NEIGHBOR + 2-OPT
# ═══════════════════════════════════════════════════════════════════════

def _nn(dist: np.ndarray, inicio: int) -> list[int]:
    n        = len(dist)
    vis      = [False] * n
    rota     = [inicio]
    vis[inicio] = True
    for _ in range(n - 1):
        d          = dist[rota[-1]].copy()
        d[vis]     = np.inf
        prox       = int(np.argmin(d))
        rota.append(prox)
        vis[prox]  = True
    return rota


def _dois_opt(rota: list[int], dist: np.ndarray) -> list[int]:
    def _len(r):
        return sum(dist[r[i]][r[i+1]] for i in range(len(r)-1))

    melhor      = rota[:]
    sem_melhora = 0
    while sem_melhora < DOIS_OPT_MAX_ITER:
        melhorou = False
        for i in range(1, len(melhor) - 2):
            for j in range(i + 1, len(melhor)):
                if j - i == 1:
                    continue
                nova = melhor[:i] + melhor[i:j][::-1] + melhor[j:]
                if _len(nova) < _len(melhor) - 1e-6:
                    melhor   = nova
                    melhorou = True
        sem_melhora = 0 if melhorou else sem_melhora + 1
    return melhor


def otimizar_rota(
    df: pd.DataFrame,
    ponto_partida: Optional[tuple[float, float]] = None,
    usar_dois_opt: bool = True,
    roteirizar_por_cluster: bool = True,
) -> pd.DataFrame:
    """
    Gera rota otimizada com NN + 2-opt.

    roteirizar_por_cluster=True (padrão):
      - Ordena clusters: tem_atrasada DESC → score_medio DESC
      - Roteiriza cada cluster independentemente → elimina saltos entre regiões
      - Encadeia clusters: início do próximo = torre mais próxima do fim do anterior

    roteirizar_por_cluster=False:
      - NN + 2-opt global (compatível com comportamento anterior)
    """
    if df.empty:
        return df
    df_v = df.dropna(subset=["LATITUDE", "LONGITUDE"]).copy().reset_index(drop=True)
    if df_v.empty:
        return df

    coords = df_v[["LATITUDE", "LONGITUDE"]].values
    D      = _haversine_matrix(coords)
    n      = len(df_v)

    # Índice de início
    if ponto_partida:
        p          = np.array([[ponto_partida[0], ponto_partida[1]]])
        dists      = _haversine_matrix(np.vstack([p, coords]))[0, 1:]
        idx_inicio = int(np.argmin(dists))
    else:
        idx_inicio = 0

    if roteirizar_por_cluster and "CLUSTER" in df_v.columns:
        def _rank(c):
            sub = df_v[df_v["CLUSTER"] == c]
            return (-int((sub.get("PRIORIDADE", pd.Series()) == 1).any()),
                    -float(sub.get("SCORE", pd.Series([50])).mean()))

        clusters_ord = sorted(df_v["CLUSTER"].unique(), key=_rank)
        cl_inicio    = int(df_v.loc[idx_inicio, "CLUSTER"])
        if cl_inicio in clusters_ord:
            clusters_ord.remove(cl_inicio)
            clusters_ord.insert(0, cl_inicio)

        rota_global: list[int] = []
        for c in clusters_ord:
            idx_c = df_v[(df_v["CLUSTER"] == c) & (~df_v.index.isin(rota_global))].index.tolist()
            if not idx_c:
                continue
            sub_D = D[np.ix_(idx_c, idx_c)]
            if rota_global:
                local_ini = int(np.argmin(D[rota_global[-1]][idx_c]))
            elif idx_inicio in idx_c:
                local_ini = idx_c.index(idx_inicio)
            else:
                local_ini = 0
            rota_local = _nn(sub_D, local_ini)
            if usar_dois_opt and len(rota_local) > 3:
                rota_local = _dois_opt(rota_local, sub_D)
            rota_global.extend([idx_c[i] for i in rota_local])
    else:
        rota_global = _nn(D, idx_inicio)
        if usar_dois_opt and len(rota_global) > 3:
            rota_global = _dois_opt(rota_global, D)

    df_rota                 = df_v.iloc[rota_global].copy()
    df_rota["ORDEM_VISITA"] = range(1, len(df_rota) + 1)
    dist_prox = [
        round(float(D[rota_global[i]][rota_global[i+1]]), 2) if i < len(rota_global)-1 else 0.0
        for i in range(len(rota_global))
    ]
    df_rota["DIST_PROX_KM"] = dist_prox
    df_rota["DIST_ACUM_KM"] = df_rota["DIST_PROX_KM"].cumsum().round(2)
    return df_rota.reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════
# RESUMO
# ═══════════════════════════════════════════════════════════════════════

def resumo_rota(df_rota: pd.DataFrame) -> dict:
    if df_rota is None or df_rota.empty:
        return {}
    atrasadas  = int((df_rota.get("STATUS_PRAZO", pd.Series(dtype=str)) == "ATRASADA").sum())
    n_clusters = int(df_rota["CLUSTER"].nunique()) if "CLUSTER" in df_rota.columns else 1
    dist_total = float(df_rota["DIST_PROX_KM"].sum().round(1))
    dist_media = round(dist_total / max(len(df_rota) - 1, 1), 1)
    crit_min   = "-"
    if "CRITICIDADE" in df_rota.columns:
        v = df_rota["CRITICIDADE"].dropna()
        if not v.empty:
            crit_min = int(v.min())
    score_medio = float(df_rota["SCORE"].mean().round(1)) if "SCORE" in df_rota.columns and len(df_rota) else 0.0
    return {
        "total_os"        : len(df_rota),
        "os_atrasadas"    : atrasadas,
        "distancia_total" : dist_total,
        "distancia_media" : dist_media,
        "n_clusters"      : n_clusters,
        "criticidade_min" : crit_min,
        "score_medio"     : score_medio,
    }


# ═══════════════════════════════════════════════════════════════════════
# PIPELINE COMPLETO (conveniência)
# ═══════════════════════════════════════════════════════════════════════

def pipeline_priorizacao(
    df: pd.DataFrame,
    max_os: int = 20,
    forcar_atrasadas: bool = True,
    metodo_cluster: str = "dbscan",
    n_clusters_kmeans: int = 5,
    ponto_partida: Optional[tuple[float, float]] = None,
    usar_dois_opt: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Executa o pipeline completo numa única chamada.
    Retorna (df_consolidado, df_rota, resumo).
    """
    df      = calcular_urgencia(df)
    df      = clusterizar(df, metodo=metodo_cluster, n_clusters=n_clusters_kmeans)
    df      = calcular_score_hibrido(df)
    df_sel  = selecionar_os(df, max_os=max_os, forcar_atrasadas=forcar_atrasadas)
    df_rota = otimizar_rota(df_sel, ponto_partida=ponto_partida, usar_dois_opt=usar_dois_opt)
    return df, df_rota, resumo_rota(df_rota)
