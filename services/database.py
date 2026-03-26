"""
services/database.py
Conexão com Microsoft Fabric usando Device Code Flow (suporta MFA obrigatório).
O usuário autentica pelo navegador — o app nunca toca na senha.

Persistência de sessão — abordagem server-side (sem cookies):
  - Após autenticar, o cache MSAL é salvo em disco (shelve) no servidor,
    indexado por um token de sessão UUID gerado no login.
  - O token de sessão é mantido na URL via st.query_params (?sid=...).
  - No F5 ou reabertura da aba, o sid ainda está na URL e o cache MSAL
    é recuperado do disco automaticamente — sem precisar de cookies.
  - Funciona mesmo com bloqueadores de cookies corporativos.
  - Cada usuário tem seu próprio sid; múltiplos usuários simultâneos OK.
  - Sessões expiram após 48h (renovadas a cada uso).

Sem dependências extras além do stdlib (shelve, uuid) e das já existentes.
A dependência extra_streamlit_components pode ser removida do requirements.
"""

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
# CONFIGURAÇÃO DO FABRIC
# ──────────────────────────────────────────────
FABRIC_SERVER   = "q2amn6c4xhfuthjy5u3zicv66u-cmbcabgdz5jehnyem4j735ihxm.datawarehouse.fabric.microsoft.com"
FABRIC_DATABASE = "SGM"
FABRIC_PORT     = 1433

_PUBLIC_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
_SCOPE            = ["https://database.windows.net/user_impersonation"]
_ODBC_DRIVER      = "ODBC Driver 17 for SQL Server"

# Arquivo shelve salvo na raiz do projeto (um nível acima de /services)
_SHELVE_PATH = os.path.join(os.path.dirname(__file__), "..", ".session_cache")
_SESSION_TTL = 48 * 3600   # 48 horas em segundos
_SID_PARAM   = "sid"       # nome do query param na URL


# ──────────────────────────────────────────────
# PERSISTÊNCIA SERVER-SIDE (shelve em disco)
# ──────────────────────────────────────────────

def _shelve_save(sid: str, token_cache_str: str, username: str) -> None:
    """Salva o cache MSAL no disco associado ao sid."""
    try:
        with shelve.open(_SHELVE_PATH) as db:
            db[sid] = {
                "cache":    token_cache_str,
                "username": username,
                "ts":       time.time(),
            }
    except Exception:
        pass


def _shelve_load(sid: str) -> dict | None:
    """
    Carrega o cache do disco pelo sid.
    Retorna None se não existir ou se tiver expirado (> 48h).
    """
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
    """Remove a entrada do disco (logout ou expiração)."""
    try:
        with shelve.open(_SHELVE_PATH) as db:
            if sid in db:
                del db[sid]
    except Exception:
        pass


def _shelve_touch(sid: str) -> None:
    """Renova o timestamp para prorrogar o TTL a cada uso."""
    try:
        with shelve.open(_SHELVE_PATH) as db:
            entry = db.get(sid)
            if entry:
                entry["ts"] = time.time()
                db[sid] = entry
    except Exception:
        pass


# ──────────────────────────────────────────────
# SID NA URL (query params)
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
    """
    Obtém o cache MSAL.
    Prioridade: session_state (rápido, sem I/O) → shelve via sid na URL → vazio.
    """
    cache = SerializableTokenCache()

    # 1. Session state — caminho rápido
    cached_state = st.session_state.get("_msal_token_cache")
    if cached_state:
        cache.deserialize(cached_state)
        return cache

    # 2. Shelve via sid da URL
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
    """
    Persiste o cache no session_state + shelve em disco.
    Cria um sid novo se necessário e o escreve na URL.
    """
    # Mesmo sem mudança, renova o TTL
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
    """Remove cache da session e do disco (logout)."""
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
# RENOVAÇÃO SILENCIOSA DE TOKEN
# ──────────────────────────────────────────────

def tentar_login_silencioso() -> bool:
    """
    Tenta renovar o access token a partir do cache em disco (via sid na URL).
    Chamado automaticamente no início de cada página.

    Após F5 ou reabertura da aba:
      - O sid continua na URL.
      - O cache MSAL é recuperado do shelve.
      - O access token é renovado silenciosamente via refresh token.
      - Se o refresh token ainda for válido (até ~90 dias pela Microsoft),
        o usuário entra sem nenhuma interação.
    """
    sid = _get_sid()
    if not sid:
        return False

    cache = _get_token_cache()
    app   = _build_msal_app(cache)

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


# ──────────────────────────────────────────────
# AUTENTICAÇÃO — Device Code Flow (suporta MFA)
# ──────────────────────────────────────────────

def iniciar_device_flow() -> dict:
    cache = _get_token_cache()
    app   = _build_msal_app(cache)
    st.session_state["_msal_app"]   = app
    st.session_state["_msal_cache"] = cache

    flow = app.initiate_device_flow(scopes=_SCOPE)
    if "user_code" not in flow:
        erro = flow.get("error_description") or flow.get("error") or str(flow)
        raise RuntimeError(f"Não foi possível iniciar o login: {erro}")

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
        erro = result.get("error_description") or result.get("error") or str(result)
        raise RuntimeError(f"Autenticação não concluída: {erro}")

    username = result.get("id_token_claims", {}).get("preferred_username", "Usuário")

    if cache:
        _save_token_cache(cache, username)

    st.session_state["fabric_token"]  = result["access_token"]
    st.session_state["fabric_user"]   = username
    st.session_state["fabric_authed"] = True

    st.session_state.pop("_msal_app",    None)
    st.session_state.pop("_msal_cache",  None)
    st.session_state.pop("_device_flow", None)
    return True


def logout() -> None:
    """Desloga, remove sessão do disco e limpa o sid da URL."""
    _delete_token_cache()
    for k in ["fabric_authed", "fabric_token", "fabric_user",
              "df_rota", "df_base", "weather_map", "resumo",
              "_device_flow", "_msal_app"]:
        st.session_state[k] = None if k != "fabric_authed" else False


def is_authenticated() -> bool:
    return bool(st.session_state.get("fabric_authed"))


# ──────────────────────────────────────────────
# CONEXÃO COM O FABRIC
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
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    return pyodbc.connect(
        conn_str,
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_para_bytes(token)},
    )


# ──────────────────────────────────────────────
# QUERIES
# ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_torres_criticidade(
    empresa: str | None = None,
    instalacao: str | None = None,
) -> pd.DataFrame:
    where_clauses = ["LATITUDE IS NOT NULL", "LONGITUDE IS NOT NULL"]
    params = []
    if empresa:
        where_clauses.append("EMPRESA = ?")
        params.append(empresa)
    if instalacao:
        where_clauses.append("INSTALACAO = ?")
        params.append(instalacao)

    where = " AND ".join(where_clauses)
    query = f"""
        SELECT
            COD_ATIVO, EMPRESA, INSTALACAO, NUM_TORRE,
            LATITUDE, LONGITUDE, CRITICIDADE_MIN,
            QTD_SS, MAX_DIAS_ABERTO, PIOR_SALDO_DIAS, FL_ATRASADO
        FROM VW_TORRES_COM_CRITICIDADE
        WHERE {where}
        ORDER BY CRITICIDADE_MIN ASC, FL_ATRASADO DESC
    """
    with _build_connection() as conn:
        return pd.read_sql(query, conn, params=params if params else None)


@st.cache_data(ttl=300, show_spinner=False)
def load_ocorrencias(cod_ativo: str | None = None) -> pd.DataFrame:
    if cod_ativo:
        query = """
            SELECT
                COD_SS, COD_ATIVO, NOME_PRIORIDADE, NIVEL_CRITICIDADE,
                DIAS_EM_ABERTO, PRAZO_DIAS, SALDO_DIAS, STATUS_PRAZO,
                TEXT_OBSERVACAO
            FROM VW_SS_TRATADA
            WHERE COD_ATIVO = ?
            ORDER BY NIVEL_CRITICIDADE ASC, SALDO_DIAS ASC
        """
        with _build_connection() as conn:
            return pd.read_sql(query, conn, params=[cod_ativo])
    else:
        query = """
            SELECT
                COD_SS, COD_ATIVO, NOME_PRIORIDADE, NIVEL_CRITICIDADE,
                DIAS_EM_ABERTO, PRAZO_DIAS, SALDO_DIAS, STATUS_PRAZO,
                TEXT_OBSERVACAO
            FROM VW_SS_TRATADA
            ORDER BY NIVEL_CRITICIDADE ASC, SALDO_DIAS ASC
        """
        with _build_connection() as conn:
            return pd.read_sql(query, conn)


@st.cache_data(ttl=600, show_spinner=False)
def get_filter_options() -> dict:
    query = """
        SELECT DISTINCT EMPRESA, INSTALACAO
        FROM VIEW_COORD_TORRES
        WHERE EMPRESA IS NOT NULL AND INSTALACAO IS NOT NULL
        ORDER BY EMPRESA, INSTALACAO
    """
    with _build_connection() as conn:
        df = pd.read_sql(query, conn)

    df = df.dropna(subset=["EMPRESA", "INSTALACAO"])
    empresas = sorted(df["EMPRESA"].unique().tolist())
    instalacoes_por_empresa = {
        emp: sorted(df.loc[df["EMPRESA"] == emp, "INSTALACAO"].unique().tolist())
        for emp in empresas
    }

    return {
        "empresas": empresas,
        "instalacoes_por_empresa": instalacoes_por_empresa,
    }


@st.cache_data(ttl=300, show_spinner=False)
def load_torres_por_instalacao(
    empresa: str | None = None,
    instalacao: str | None = None,
) -> pd.DataFrame:
    where_clauses = ["LATITUDE IS NOT NULL", "LONGITUDE IS NOT NULL"]
    params = []
    if empresa:
        where_clauses.append("EMPRESA = ?")
        params.append(empresa)
    if instalacao:
        where_clauses.append("INSTALACAO = ?")
        params.append(instalacao)

    where = " AND ".join(where_clauses)
    query = f"""
        SELECT COD_ATIVO, NUM_TORRE, LATITUDE, LONGITUDE
        FROM VW_TORRES_COM_CRITICIDADE
        WHERE {where}
        ORDER BY NUM_TORRE ASC
    """
    with _build_connection() as conn:
        return pd.read_sql(query, conn, params=params if params else None)
