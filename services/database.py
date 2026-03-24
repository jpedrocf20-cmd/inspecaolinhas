"""
services/database.py
Conexão com Microsoft Fabric usando Device Code Flow (suporta MFA obrigatório).
O usuário autentica pelo navegador — o app nunca toca na senha.
"""

import struct
import pandas as pd
import pyodbc
import streamlit as st
from msal import PublicClientApplication

# ──────────────────────────────────────────────
# CONFIGURAÇÃO DO FABRIC
# ──────────────────────────────────────────────
FABRIC_SERVER   = "q2amn6c4xhfuthjy5u3zicv66u-cmbcabgdz5jehnyem4j735ihxm.datawarehouse.fabric.microsoft.com"
FABRIC_DATABASE = "SGM"
FABRIC_PORT     = 1433

# App público "Microsoft Azure PowerShell" — aceito por qualquer tenant corporativo.
_PUBLIC_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
_SCOPE            = ["https://database.windows.net/user_impersonation"]
_ODBC_DRIVER      = "ODBC Driver 17 for SQL Server"


# ──────────────────────────────────────────────
# AUTENTICAÇÃO — Device Code Flow (suporta MFA)
# ──────────────────────────────────────────────

def iniciar_device_flow() -> dict:
    """
    Passo 1: Inicia o Device Code Flow.
    Retorna o dict do flow com 'user_code' e 'verification_uri'.
    Salva o app MSAL na sessão para uso no passo 2.
    """
    app = PublicClientApplication(
        _PUBLIC_CLIENT_ID,
        authority="https://login.microsoftonline.com/organizations",
    )
    st.session_state["_msal_app"] = app

    flow = app.initiate_device_flow(scopes=_SCOPE)
    if "user_code" not in flow:
        erro = flow.get("error_description") or flow.get("error") or str(flow)
        raise RuntimeError(f"Não foi possível iniciar o login: {erro}")

    # Salva o flow na sessão para o passo 2
    st.session_state["_device_flow"] = flow
    return flow


def concluir_login() -> bool:
    """
    Passo 2: Aguarda confirmação do usuário e troca o device code pelo token.
    Deve ser chamado após o usuário clicar em 'Já autentiquei'.
    Salva o token na sessão e retorna True se OK, levanta RuntimeError se falhou.
    """
    app  = st.session_state.get("_msal_app")
    flow = st.session_state.get("_device_flow")

    if not app or not flow:
        raise RuntimeError("Sessão expirada. Clique em 'Iniciar Login' novamente.")

    # timeout=0 → não bloqueia; tenta uma vez e retorna imediatamente
    result = app.acquire_token_by_device_flow(flow, exit_condition=lambda f: True)

    if "access_token" not in result:
        erro = result.get("error_description") or result.get("error") or str(result)
        raise RuntimeError(f"Autenticação não concluída: {erro}")

    st.session_state["fabric_token"]  = result["access_token"]
    st.session_state["fabric_user"]   = result.get("id_token_claims", {}).get("preferred_username", "Usuário")
    st.session_state["fabric_authed"] = True

    # Limpa dados temporários
    st.session_state.pop("_msal_app", None)
    st.session_state.pop("_device_flow", None)
    return True


def is_authenticated() -> bool:
    return bool(st.session_state.get("fabric_authed"))


# ──────────────────────────────────────────────
# CONEXÃO COM O FABRIC
# ──────────────────────────────────────────────

def _token_para_bytes(token: str) -> bytes:
    """Converte access_token para formato binário exigido pelo pyodbc/ODBC 17."""
    token_bytes = token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def _build_connection() -> pyodbc.Connection:
    """Abre conexão pyodbc com o Fabric usando o token OAuth2 da sessão."""
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
    conn = pyodbc.connect(
        conn_str,
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_para_bytes(token)},
    )
    return conn


# ──────────────────────────────────────────────
# QUERIES
# ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_torres_criticidade(
    empresa: str | None = None,
    instalacao: str | None = None,
) -> pd.DataFrame:
    """Carrega torres com criticidade, aplicando filtros opcionais."""
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
    """Carrega ocorrências/SS abertas, opcionalmente filtradas por ativo."""
    if cod_ativo:
        query = """
            SELECT
                COD_SS, COD_ATIVO, NOME_PRIORIDADE, NIVEL_CRITICIDADE,
                DIAS_EM_ABERTO, PRAZO_DIAS, SALDO_DIAS, STATUS_PRAZO
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
                DIAS_EM_ABERTO, PRAZO_DIAS, SALDO_DIAS, STATUS_PRAZO
            FROM VW_SS_TRATADA
            ORDER BY NIVEL_CRITICIDADE ASC, SALDO_DIAS ASC
        """
        with _build_connection() as conn:
            return pd.read_sql(query, conn)


@st.cache_data(ttl=600, show_spinner=False)
def get_filter_options() -> dict:
    """
    Retorna:
      - 'empresas': lista ordenada de empresas
      - 'instalacoes_por_empresa': dict {empresa: [instalacoes]} para filtragem encadeada
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
