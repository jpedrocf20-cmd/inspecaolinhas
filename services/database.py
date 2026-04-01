"""
data/database.py
Camada de dados — conexão com Microsoft Fabric e queries SQL.

Responsabilidades:
  - Autenticação via Device Code Flow (MFA-compatible)
  - Persistência de sessão via shelve server-side + sid na URL
  - Consulta das duas views principais
  - JOIN obrigatório via COD_ATIVO entre:
      VIEW_PLANO_CONSOLIDADO_INSPECAO  ←→  VW_TORRES_COM_CRITICIDADE
  - Retorno de DataFrames limpos e tipados
"""

from __future__ import annotations

import os
import shelve
import struct
import time
import uuid
import pandas as pd
import pyodbc
import streamlit as st
from msal import PublicClientApplication, SerializableTokenCache

# ──────────────────────────────────────────────
# CONFIG FABRIC
# ──────────────────────────────────────────────
FABRIC_SERVER   = "q2amn6c4xhfuthjy5u3zicv66u-cmbcabgdz5jehnyem4j735ihxm.datawarehouse.fabric.microsoft.com"
FABRIC_DATABASE = "SGM"
FABRIC_PORT     = 1433

_PUBLIC_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
_SCOPE            = ["https://database.windows.net/user_impersonation"]
_ODBC_DRIVER      = "ODBC Driver 17 for SQL Server"

_SHELVE_PATH = os.path.join(os.path.dirname(__file__), "..", ".session_cache")
_SESSION_TTL = 48 * 3600
_SID_PARAM   = "sid"


# ──────────────────────────────────────────────
# PERSISTÊNCIA SERVER-SIDE (shelve)
# ──────────────────────────────────────────────

def _shelve_save(sid: str, token_cache_str: str, username: str) -> None:
    try:
        with shelve.open(_SHELVE_PATH) as db:
            db[sid] = {"cache": token_cache_str, "username": username, "ts": time.time()}
    except Exception:
        pass


def _shelve_load(sid: str) -> dict | None:
    try:
        with shelve.open(_SHELVE_PATH) as db:
            entry = db.get(sid)
        if not entry:
            return None
        if time.time() - entry["ts"] > _SESSION_TTL:
            _shelve_delete(sid)
            return None
        return entry
    except Exception:
        return None


def _shelve_delete(sid: str) -> None:
    try:
        with shelve.open(_SHELVE_PATH) as db:
            if sid in db:
                del db[sid]
    except Exception:
        pass


def _shelve_touch(sid: str) -> None:
    try:
        with shelve.open(_SHELVE_PATH) as db:
            entry = db.get(sid)
            if entry:
                entry["ts"] = time.time()
                db[sid] = entry
    except Exception:
        pass


# ──────────────────────────────────────────────
# SID NA URL
# ──────────────────────────────────────────────

def _get_sid() -> str | None:
    return st.query_params.get(_SID_PARAM)

def _set_sid(sid: str) -> None:
    st.query_params[_SID_PARAM] = sid

def _clear_sid() -> None:
    if _SID_PARAM in st.query_params:
        del st.query_params[_SID_PARAM]


# ──────────────────────────────────────────────
# TOKEN CACHE MSAL
# ──────────────────────────────────────────────

def _get_token_cache() -> SerializableTokenCache:
    cache = SerializableTokenCache()
    cached_state = st.session_state.get("_msal_token_cache")
    if cached_state:
        cache.deserialize(cached_state)
        return cache
    sid = _get_sid()
    if sid:
        entry = _shelve_load(sid)
        if entry:
            try:
                cache.deserialize(entry["cache"])
                st.session_state["_msal_token_cache"] = entry["cache"]
                st.session_state["_session_sid"]      = sid
            except Exception:
                pass
    return cache


def _save_token_cache(cache: SerializableTokenCache, username: str = "Usuário") -> None:
    sid = st.session_state.get("_session_sid") or _get_sid()
    if not cache.has_state_changed:
        if sid:
            _shelve_touch(sid)
        return
    serialized = cache.serialize()
    st.session_state["_msal_token_cache"] = serialized
    if not sid:
        sid = str(uuid.uuid4())
        st.session_state["_session_sid"] = sid
    _shelve_save(sid, serialized, username)
    _set_sid(sid)


def _delete_token_cache() -> None:
    sid = st.session_state.get("_session_sid") or _get_sid()
    if sid:
        _shelve_delete(sid)
    st.session_state.pop("_msal_token_cache", None)
    st.session_state.pop("_session_sid",      None)
    _clear_sid()


def _build_msal_app(cache: SerializableTokenCache | None = None) -> PublicClientApplication:
    return PublicClientApplication(
        _PUBLIC_CLIENT_ID,
        authority="https://login.microsoftonline.com/organizations",
        token_cache=cache,
    )


# ──────────────────────────────────────────────
# AUTH — Device Code Flow
# ──────────────────────────────────────────────

def tentar_login_silencioso() -> bool:
    sid = _get_sid()
    if not sid:
        return False
    cache   = _get_token_cache()
    app     = _build_msal_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        return False
    result = app.acquire_token_silent(scopes=_SCOPE, account=accounts[0])
    if result and "access_token" in result:
        username = (
            result.get("id_token_claims", {}).get("preferred_username")
            or accounts[0].get("username", "Usuário")
        )
        _save_token_cache(cache, username)
        st.session_state["fabric_token"]  = result["access_token"]
        st.session_state["fabric_user"]   = username
        st.session_state["fabric_authed"] = True
        return True
    return False


def iniciar_device_flow() -> dict:
    cache = _get_token_cache()
    app   = _build_msal_app(cache)
    st.session_state["_msal_app"]   = app
    st.session_state["_msal_cache"] = cache
    flow = app.initiate_device_flow(scopes=_SCOPE)
    if "user_code" not in flow:
        raise RuntimeError(f"Não foi possível iniciar o login: {flow.get('error_description', flow)}")
    st.session_state["_device_flow"] = flow
    return flow


def concluir_login() -> bool:
    app   = st.session_state.get("_msal_app")
    flow  = st.session_state.get("_device_flow")
    cache = st.session_state.get("_msal_cache")
    if not app or not flow:
        raise RuntimeError("Sessão expirada. Clique em 'Iniciar Login' novamente.")
    result = app.acquire_token_by_device_flow(flow, exit_condition=lambda f: True)
    if "access_token" not in result:
        raise RuntimeError(f"Autenticação não concluída: {result.get('error_description', result)}")
    username = result.get("id_token_claims", {}).get("preferred_username", "Usuário")
    if cache:
        _save_token_cache(cache, username)
    st.session_state["fabric_token"]  = result["access_token"]
    st.session_state["fabric_user"]   = username
    st.session_state["fabric_authed"] = True
    for k in ["_msal_app", "_msal_cache", "_device_flow"]:
        st.session_state.pop(k, None)
    return True


def logout() -> None:
    _delete_token_cache()
    for k in ["fabric_authed", "fabric_token", "fabric_user",
              "df_consolidado", "df_rota", "weather_map", "resumo",
              "_device_flow", "_msal_app"]:
        st.session_state[k] = None if k != "fabric_authed" else False


def is_authenticated() -> bool:
    return bool(st.session_state.get("fabric_authed"))


def sid_atual() -> str:
    return st.session_state.get("_session_sid") or _get_sid() or ""


# ──────────────────────────────────────────────
# CONEXÃO
# ──────────────────────────────────────────────

def _token_para_bytes(token: str) -> bytes:
    token_bytes = token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def _build_connection() -> pyodbc.Connection:
    token = st.session_state.get("fabric_token")
    if not token:
        raise RuntimeError("Não autenticado. Faça login na sidebar.")
    conn_str = (
        f"DRIVER={{{_ODBC_DRIVER}}};"
        f"SERVER={FABRIC_SERVER},{FABRIC_PORT};"
        f"DATABASE={FABRIC_DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_para_bytes(token)})


# ──────────────────────────────────────────────
# QUERIES PRINCIPAIS
# ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_inspecoes_consolidadas(
    empresa:    str | None = None,
    instalacao: str | None = None,
    _sid: str = "",
) -> pd.DataFrame:
    """
    🔑 QUERY PRINCIPAL DO SISTEMA.

    Faz o JOIN obrigatório entre:
      VIEW_PLANO_CONSOLIDADO_INSPECAO  (dados da OS e prazo)
      VW_TORRES_COM_CRITICIDADE        (LATITUDE, LONGITUDE, torre)

    A ligação é EXCLUSIVAMENTE via COD_ATIVO.
    NÃO usa COD_OS como chave de ligação entre as views.

    Filtra apenas registros com coordenadas válidas.
    """
    where_clauses = [
        "T.LATITUDE  IS NOT NULL",
        "T.LONGITUDE IS NOT NULL",
    ]
    params: list = []

    if empresa:
        where_clauses.append("T.EMPRESA = ?")
        params.append(empresa)
    if instalacao:
        where_clauses.append("T.INSTALACAO = ?")
        params.append(instalacao)

    where = " AND ".join(where_clauses)

    query = f"""
        SELECT
            -- Identificação
            P.COD_OS,
            P.COD_ATIVO,

            -- Prazo e status (VIEW_PLANO_CONSOLIDADO_INSPECAO)
            P.STATUS_PRAZO,
            P.DATA_LIMITE,
            P.DIAS_ATRASO,
            P.DATA_PREVISTA,
            P.DESC_PRIORIDADE,
            P.DESC_NUMERO_OS,
            P.DESC_ESTADO,
            P.COD_PLANO,
            P.DESC_PLANO,
            P.DESC_ESQUEMA,
            P.NOME_EMPRESA,
            P.SIGLA_EMPRESA,
            P.COD_INSTALACAO,
            P.DESC_LOCALIZACAO,
            P.DATA_EXTRACAO,

            -- Localização e torre (VW_TORRES_COM_CRITICIDADE via COD_ATIVO)
            T.LATITUDE,
            T.LONGITUDE,
            T.NUM_TORRE,
            T.CRITICIDADE_MIN   AS CRITICIDADE,
            T.EMPRESA,
            T.INSTALACAO

        FROM
            VIEW_PLANO_CONSOLIDADO_INSPECAO P
            INNER JOIN VW_TORRES_COM_CRITICIDADE T
                ON P.COD_ATIVO = T.COD_ATIVO   -- JOIN EXCLUSIVO via COD_ATIVO

        WHERE
            {where}

        ORDER BY
            P.STATUS_PRAZO DESC,   -- ATRASADA primeiro
            P.DIAS_ATRASO  DESC,
            P.DATA_LIMITE  ASC
    """

    with _build_connection() as conn:
        df = pd.read_sql(query, conn, params=params if params else None)

    # Garantias de tipo
    for col in ["LATITUDE", "LONGITUDE"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["DIAS_ATRASO"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in ["DATA_LIMITE", "DATA_PREVISTA"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Remove linhas sem coordenada (segurança extra)
    df = df.dropna(subset=["LATITUDE", "LONGITUDE"]).reset_index(drop=True)

    return df


@st.cache_data(ttl=600, show_spinner=False)
def get_filter_options(_sid: str = "") -> dict:
    """
    Retorna opções de filtro de empresa e instalação
    baseadas nas torres que têm dados em ambas as views.
    """
    query = """
        SELECT DISTINCT T.EMPRESA, T.INSTALACAO
        FROM VW_TORRES_COM_CRITICIDADE T
        INNER JOIN VIEW_PLANO_CONSOLIDADO_INSPECAO P ON P.COD_ATIVO = T.COD_ATIVO
        WHERE T.EMPRESA IS NOT NULL AND T.INSTALACAO IS NOT NULL
            AND T.LATITUDE IS NOT NULL AND T.LONGITUDE IS NOT NULL
        ORDER BY T.EMPRESA, T.INSTALACAO
    """
    with _build_connection() as conn:
        df = pd.read_sql(query, conn)

    df = df.dropna(subset=["EMPRESA", "INSTALACAO"])
    empresas = sorted(df["EMPRESA"].unique().tolist())
    instalacoes_por_empresa = {
        emp: sorted(df.loc[df["EMPRESA"] == emp, "INSTALACAO"].unique().tolist())
        for emp in empresas
    }
    return {"empresas": empresas, "instalacoes_por_empresa": instalacoes_por_empresa}


@st.cache_data(ttl=300, show_spinner=False)
def load_torres_por_instalacao(
    empresa:    str | None = None,
    instalacao: str | None = None,
    _sid: str = "",
) -> pd.DataFrame:
    """Torres disponíveis para seleção de ponto de partida."""
    if not instalacao:
        return pd.DataFrame(columns=["COD_ATIVO", "NUM_TORRE", "LATITUDE", "LONGITUDE"])

    where_clauses = [
        "LATITUDE IS NOT NULL",
        "LONGITUDE IS NOT NULL",
        "INSTALACAO = ?",
    ]
    params: list = [instalacao]
    if empresa:
        where_clauses.append("EMPRESA = ?")
        params.append(empresa)

    where = " AND ".join(where_clauses)
    query = f"""
        SELECT COD_ATIVO, NUM_TORRE, LATITUDE, LONGITUDE
        FROM VW_TORRES_COM_CRITICIDADE
        WHERE {where}
        ORDER BY TRY_CAST(NUM_TORRE AS INT) ASC, NUM_TORRE ASC
    """
    with _build_connection() as conn:
        return pd.read_sql(query, conn, params=params)
