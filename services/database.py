"""
services/database.py
Conexão com Microsoft Fabric usando login corporativo (e-mail + senha OAuth2).
Driver: pyodbc com token Bearer — único método compatível com Fabric SQL Endpoint.
Credenciais digitadas pelo usuário na sidebar — nunca salvas em disco.
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
# Não exige cadastro de Service Principal.
_PUBLIC_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
_SCOPE            = ["https://database.windows.net/user_impersonation"]

# Driver ODBC — nome exato instalado no ambiente Linux do Streamlit Cloud
_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"


# ──────────────────────────────────────────────
# AUTENTICAÇÃO
# ──────────────────────────────────────────────

def _get_token(email: str, senha: str) -> str:
    """
    Autentica com e-mail + senha corporativa via MSAL e retorna access_token.
    Lança RuntimeError com mensagem legível em caso de falha.
    """
    app = PublicClientApplication(
        _PUBLIC_CLIENT_ID,
        authority="https://login.microsoftonline.com/organizations",
    )

    # Tenta silenciosamente primeiro (cache), depois com credencial
    accounts = app.get_accounts(username=email)
    result = None
    if accounts:
        result = app.acquire_token_silent(_SCOPE, account=accounts[0])

    if not result:
        result = app.acquire_token_by_username_password(
            username=email,
            password=senha,
            scopes=_SCOPE,
        )

    if "access_token" not in result:
        erro = result.get("error_description") or result.get("error") or str(result)
        raise RuntimeError(f"Falha na autenticação Microsoft: {erro}")

    return result["access_token"]


def _token_para_bytes(token: str) -> bytes:
    """
    Converte o access_token para o formato binário exigido pelo pyodbc
    ao usar autenticação por token no SQL Server / Fabric.
    Ref: https://docs.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory
    """
    token_bytes = token.encode("utf-16-le")
    # Estrutura: cada byte do token empacotado como unsigned short (2 bytes)
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    return token_struct


def _build_connection() -> pyodbc.Connection:
    """
    Abre conexão pyodbc com o Fabric usando o token OAuth2 da sessão.
    O token é passado via atributo SQL_COPT_SS_ACCESS_TOKEN — método oficial
    da Microsoft para autenticação AAD sem senha no driver ODBC 17+.
    """
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

    # SQL_COPT_SS_ACCESS_TOKEN = 1256 (constante do driver ODBC da Microsoft)
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    token_bytes = _token_para_bytes(token)

    conn = pyodbc.connect(
        conn_str,
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_bytes},
    )
    return conn


# ──────────────────────────────────────────────
# INTERFACE PÚBLICA DE AUTENTICAÇÃO
# ──────────────────────────────────────────────

def login_fabric(email: str, senha: str) -> bool:
    """
    Autentica com credenciais corporativas e salva o token na sessão.
    Retorna True se OK, levanta RuntimeError se falhar.
    """
    token = _get_token(email, senha)
    st.session_state["fabric_token"]  = token
    st.session_state["fabric_user"]   = email
    st.session_state["fabric_authed"] = True
    return True


def is_authenticated() -> bool:
    return bool(st.session_state.get("fabric_authed"))


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
    """Retorna listas distintas de empresas e instalações para os filtros da sidebar."""
    query = """
        SELECT DISTINCT EMPRESA, INSTALACAO
        FROM VIEW_COORD_TORRES
        WHERE EMPRESA IS NOT NULL AND INSTALACAO IS NOT NULL
        ORDER BY EMPRESA, INSTALACAO
    """
    with _build_connection() as conn:
        df = pd.read_sql(query, conn)
    return {
        "empresas":    sorted(df["EMPRESA"].dropna().unique().tolist()),
        "instalacoes": sorted(df["INSTALACAO"].dropna().unique().tolist()),
    }
