"""
services/database.py
Conexão com Microsoft Fabric usando login corporativo (e-mail + senha).
Não exige Service Principal nem acesso ao Azure AD.
Credenciais digitadas pelo usuário na sidebar — nunca salvas em disco.
"""

import pandas as pd
import pymssql
import streamlit as st
from msal import PublicClientApplication

FABRIC_SERVER   = "q2amn6c4xhfuthjy5u3zicv66u-cmbcabgdz5jehnyem4j735ihxm.datawarehouse.fabric.microsoft.com"
FABRIC_DATABASE = "SGM"

# Client ID do app público "Microsoft Azure PowerShell" — não precisa de cadastro
# É um app público da própria Microsoft, aceito por qualquer tenant corporativo.
_PUBLIC_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
_SCOPE            = ["https://database.windows.net/user_impersonation"]


def _get_token(email: str, senha: str) -> str:
    """Autentica com e-mail + senha corporativa e retorna token de acesso."""
    app = PublicClientApplication(
        _PUBLIC_CLIENT_ID,
        authority="https://login.microsoftonline.com/organizations",
    )
    result = app.acquire_token_by_username_password(
        username=email,
        password=senha,
        scopes=_SCOPE,
    )
    if "access_token" not in result:
        erro = result.get("error_description") or result.get("error") or str(result)
        raise RuntimeError(f"Falha na autenticação: {erro}")
    return result["access_token"]


def _build_connection() -> pymssql.Connection:
    """Conecta ao Fabric usando o token da sessão atual."""
    token = st.session_state.get("fabric_token")
    if not token:
        raise RuntimeError("Não autenticado. Faça login na sidebar.")
    return pymssql.connect(
        server=FABRIC_SERVER,
        database=FABRIC_DATABASE,
        user="token",
        password=token,
        tds_version="7.4",
        login_timeout=30,
    )


def login_fabric(email: str, senha: str) -> bool:
    """
    Autentica e salva o token na sessão.
    Retorna True se OK, levanta exceção se falhar.
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
    where_clauses = ["1=1"]
    if empresa:
        where_clauses.append(f"EMPRESA = '{empresa}'")
    if instalacao:
        where_clauses.append(f"INSTALACAO = '{instalacao}'")
    where = " AND ".join(where_clauses)

    query = f"""
        SELECT
            COD_ATIVO, EMPRESA, INSTALACAO, NUM_TORRE,
            LATITUDE, LONGITUDE, CRITICIDADE_MIN,
            QTD_SS, MAX_DIAS_ABERTO, PIOR_SALDO_DIAS, FL_ATRASADO
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
    where = f"AND COD_ATIVO = '{cod_ativo}'" if cod_ativo else ""
    query = f"""
        SELECT
            COD_SS, COD_ATIVO, NOME_PRIORIDADE, NIVEL_CRITICIDADE,
            DIAS_EM_ABERTO, PRAZO_DIAS, SALDO_DIAS, STATUS_PRAZO
        FROM VW_SS_TRATADA
        WHERE 1=1 {where}
        ORDER BY NIVEL_CRITICIDADE ASC, SALDO_DIAS ASC
    """
    with _build_connection() as conn:
        return pd.read_sql(query, conn)


@st.cache_data(ttl=600, show_spinner=False)
def get_filter_options() -> dict:
    query = "SELECT DISTINCT EMPRESA, INSTALACAO FROM VIEW_COORD_TORRES ORDER BY EMPRESA, INSTALACAO"
    with _build_connection() as conn:
        df = pd.read_sql(query, conn)
    return {
        "empresas":    sorted(df["EMPRESA"].dropna().unique().tolist()),
        "instalacoes": sorted(df["INSTALACAO"].dropna().unique().tolist()),
    }
