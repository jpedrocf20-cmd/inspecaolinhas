"""
services/database.py
Conexão segura com Microsoft Fabric via Service Principal (Azure AD).
Credenciais lidas de variáveis de ambiente — nunca hardcoded.
"""

import os
import struct
import pyodbc
import pandas as pd
import streamlit as st
from msal import ConfidentialClientApplication
from dotenv import load_dotenv

load_dotenv()

FABRIC_SERVER   = os.getenv("FABRIC_SERVER")
FABRIC_DATABASE = os.getenv("FABRIC_DATABASE")
TENANT_ID       = os.getenv("AZURE_TENANT_ID")
CLIENT_ID       = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET   = os.getenv("AZURE_CLIENT_SECRET")

_SCOPE = "https://database.windows.net/.default"


def _get_access_token() -> str:
    """Obtém token OAuth2 via Service Principal (sem usuário/senha)."""
    app = ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=[_SCOPE])
    if "access_token" not in result:
        raise RuntimeError(f"Falha ao obter token Azure AD: {result.get('error_description')}")
    return result["access_token"]


def _build_connection() -> pyodbc.Connection:
    """Cria conexão ODBC autenticada com token Azure AD."""
    token = _get_access_token()

    # Converte token para bytes no formato esperado pelo driver ODBC
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(
        f"<I{len(token_bytes)}s",
        len(token_bytes),
        token_bytes,
    )

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={FABRIC_SERVER};"
        f"DATABASE={FABRIC_DATABASE};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={1256: token_struct})
    return conn


@st.cache_data(ttl=300, show_spinner=False)
def load_torres() -> pd.DataFrame:
    """Carrega coordenadas das torres (cache 5 min)."""
    query = """
        SELECT
            COD_ATIVO,
            EMPRESA,
            INSTALACAO,
            NUM_TORRE,
            LATITUDE,
            LONGITUDE
        FROM VIEW_COORD_TORRES
        WHERE LATITUDE  IS NOT NULL
          AND LONGITUDE IS NOT NULL
    """
    with _build_connection() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=300, show_spinner=False)
def load_torres_criticidade(
    empresa: str | None = None,
    instalacao: str | None = None,
) -> pd.DataFrame:
    """
    Carrega torres com criticidade calculada.
    Aplica filtros opcionais de empresa e instalação.
    """
    where_clauses = ["1=1"]
    if empresa:
        where_clauses.append(f"EMPRESA = '{empresa}'")
    if instalacao:
        where_clauses.append(f"INSTALACAO = '{instalacao}'")
    where = " AND ".join(where_clauses)

    query = f"""
        SELECT
            COD_ATIVO,
            EMPRESA,
            INSTALACAO,
            NUM_TORRE,
            LATITUDE,
            LONGITUDE,
            CRITICIDADE_MIN,
            QTD_SS,
            MAX_DIAS_ABERTO,
            PIOR_SALDO_DIAS,
            FL_ATRASADO
        FROM VW_TORRES_COM_CRITICIDADE
        WHERE LATITUDE  IS NOT NULL
          AND LONGITUDE IS NOT NULL
          AND {where}
        ORDER BY CRITICIDADE_MIN ASC, FL_ATRASADO DESC
    """
    with _build_connection() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=300, show_spinner=False)
def load_ocorrencias(cod_ativo: str | None = None) -> pd.DataFrame:
    """Carrega ocorrências pendentes. Filtra por torre se informado."""
    where = f"AND COD_ATIVO = '{cod_ativo}'" if cod_ativo else ""
    query = f"""
        SELECT
            COD_SS,
            COD_ATIVO,
            NOME_PRIORIDADE,
            NIVEL_CRITICIDADE,
            DIAS_EM_ABERTO,
            PRAZO_DIAS,
            SALDO_DIAS,
            STATUS_PRAZO
        FROM VW_SS_TRATADA
        WHERE 1=1 {where}
        ORDER BY NIVEL_CRITICIDADE ASC, SALDO_DIAS ASC
    """
    with _build_connection() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=600, show_spinner=False)
def get_filter_options() -> dict:
    """Retorna listas únicas de empresa e instalação para os filtros."""
    query = "SELECT DISTINCT EMPRESA, INSTALACAO FROM VIEW_COORD_TORRES ORDER BY EMPRESA, INSTALACAO"
    with _build_connection() as conn:
        df = pd.read_sql(query, conn)
    return {
        "empresas":    sorted(df["EMPRESA"].dropna().unique().tolist()),
        "instalacoes": sorted(df["INSTALACAO"].dropna().unique().tolist()),
    }
