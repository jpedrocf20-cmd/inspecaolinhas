"""
services/database.py
Conexão com Microsoft Fabric usando Device Code Flow (suporta MFA obrigatório).
O usuário autentica pelo navegador — o app nunca toca na senha.

Login persistente via COOKIE no browser do usuário:
  - Após autenticar, o refresh token é serializado e salvo em cookie (48h).
  - A cada recarregamento a página tenta renovar silenciosamente.
  - Funciona para múltiplos usuários simultâneos — cada um carrega o próprio cookie.

Dependência extra:
    pip install extra-streamlit-components
"""

import struct
import pandas as pd
import pyodbc
import streamlit as st
from msal import PublicClientApplication, SerializableTokenCache

# Cookie manager — importado com try para evitar crash se não instalado
try:
    import extra_streamlit_components as stx
    _COOKIE_MANAGER_AVAILABLE = True
except ImportError:
    _COOKIE_MANAGER_AVAILABLE = False

# ──────────────────────────────────────────────
# CONFIGURAÇÃO DO FABRIC
# ──────────────────────────────────────────────
FABRIC_SERVER   = "q2amn6c4xhfuthjy5u3zicv66u-cmbcabgdz5jehnyem4j735ihxm.datawarehouse.fabric.microsoft.com"
FABRIC_DATABASE = "SGM"
FABRIC_PORT     = 1433

_PUBLIC_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
_SCOPE            = ["https://database.windows.net/user_impersonation"]
_ODBC_DRIVER      = "ODBC Driver 17 for SQL Server"

_COOKIE_NAME      = "msal_token_cache"
_COOKIE_MAX_AGE   = 48 * 3600   # 48 horas em segundos


# ──────────────────────────────────────────────
# COOKIE MANAGER (singleton por sessão)
# ──────────────────────────────────────────────

@st.cache_resource
def _get_cookie_manager():
    """
    Retorna o CookieManager singleton.
    O @st.cache_resource garante que só existe uma instância por processo,
    evitando o bug de múltiplas instâncias do extra-streamlit-components.
    """
    if not _COOKIE_MANAGER_AVAILABLE:
        return None
    return stx.CookieManager()


# ──────────────────────────────────────────────
# TOKEN CACHE — persiste no cookie do browser
# ──────────────────────────────────────────────

def _get_token_cache() -> SerializableTokenCache:
    """
    Obtém o cache de tokens.
    Prioridade: session_state (rápido) → cookie do browser → cache vazio.
    """
    cache = SerializableTokenCache()

    # 1. Session state (evita leitura de cookie a cada rerun)
    cached_state = st.session_state.get("_msal_token_cache")
    if cached_state:
        cache.deserialize(cached_state)
        return cache

    # 2. Cookie do browser
    mgr = _get_cookie_manager()
    if mgr:
        cookie_val = mgr.get(_COOKIE_NAME)
        if cookie_val:
            try:
                cache.deserialize(cookie_val)
                # Espelha na session para reruns seguintes
                st.session_state["_msal_token_cache"] = cookie_val
            except Exception:
                pass

    return cache


def _save_token_cache(cache: SerializableTokenCache) -> None:
    """
    Persiste o cache na session_state E no cookie do browser.
    O cookie dura 48h e está vinculado ao browser do usuário.
    """
    if not cache.has_state_changed:
        return

    serialized = cache.serialize()
    st.session_state["_msal_token_cache"] = serialized

    mgr = _get_cookie_manager()
    if mgr:
        try:
            mgr.set(_COOKIE_NAME, serialized, max_age=_COOKIE_MAX_AGE)
        except Exception:
            pass  # Falha silenciosa — a sessão ainda funciona


def _delete_token_cache() -> None:
    """Remove o cache da session e do cookie (usado no logout)."""
    st.session_state.pop("_msal_token_cache", None)
    mgr = _get_cookie_manager()
    if mgr:
        try:
            mgr.delete(_COOKIE_NAME)
        except Exception:
            pass


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
    Tenta renovar o access token silenciosamente a partir do cookie.
    Chamado automaticamente no início de cada carregamento de página.
    Retorna True se conseguiu, False se precisar de login interativo.
    """
    cache = _get_token_cache()
    app   = _build_msal_app(cache)

    accounts = app.get_accounts()
    if not accounts:
        return False

    result = app.acquire_token_silent(scopes=_SCOPE, account=accounts[0])

    if result and "access_token" in result:
        _save_token_cache(cache)
        st.session_state["fabric_token"]  = result["access_token"]
        st.session_state["fabric_user"]   = (
            result.get("id_token_claims", {}).get("preferred_username")
            or accounts[0].get("username", "Usuário")
        )
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

    if cache:
        _save_token_cache(cache)

    st.session_state["fabric_token"]  = result["access_token"]
    st.session_state["fabric_user"]   = result.get("id_token_claims", {}).get("preferred_username", "Usuário")
    st.session_state["fabric_authed"] = True

    st.session_state.pop("_msal_app",    None)
    st.session_state.pop("_msal_cache",  None)
    st.session_state.pop("_device_flow", None)
    return True


def logout() -> None:
    """Desloga e apaga o cookie — o próximo acesso exigirá login."""
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
    """
    Retorna empresas e instalações para os filtros da sidebar.
    Inclui também as torres por instalação para o ponto de partida.
    """
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
    """
    Retorna COD_ATIVO, NUM_TORRE, LATITUDE, LONGITUDE das torres
    filtradas por empresa e/ou instalação — usado no seletor de ponto de partida.
    """
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
