"""
app.py — Roteirização de Inspeção de Linhas de Transmissão
Nova arquitetura: data / domain / services / ui
JOIN obrigatório via COD_ATIVO entre VIEW_PLANO_CONSOLIDADO_INSPECAO e VW_TORRES_COM_CRITICIDADE
"""

from __future__ import annotations

import io
import time
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from services.database import (
    iniciar_device_flow, concluir_login, is_authenticated,
    tentar_login_silencioso, logout,
    load_inspecoes_consolidadas, get_filter_options,
    load_torres_por_instalacao, sid_atual,
)
from services.weather import get_weather, get_forecast_5d, weather_badge
from components.mapa  import build_map
from utils.routing    import otimizar_rota, resumo_rota

# ── domain/models e domain/priorizacao inline ──────────────────────────────
# Os arquivos domain/models e domain/priorizacao não possuem extensão .py
# no repositório, portanto não são reconhecidos pelo Python como módulos.
# O conteúdo foi embutido aqui para evitar ModuleNotFoundError.

import numpy as np
from datetime import date
from enum import IntEnum

class Prioridade(IntEnum):
    MAXIMA = 1   # STATUS_PRAZO = 'ATRASADA'
    ALTA   = 2   # DATA_LIMITE próxima (≤ 7 dias)
    NORMAL = 3   # Demais OS

_JANELA_ALTA_PRIORIDADE_DIAS = 7

def priorizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def _prioridade(row) -> int:
        if str(row.get("STATUS_PRAZO", "")).upper() == "ATRASADA":
            return int(Prioridade.MAXIMA)
        try:
            limite = pd.to_datetime(row.get("DATA_LIMITE"))
            if pd.notna(limite):
                dias = (limite.date() - date.today()).days
                if 0 <= dias <= _JANELA_ALTA_PRIORIDADE_DIAS:
                    return int(Prioridade.ALTA)
        except Exception:
            pass
        return int(Prioridade.NORMAL)

    df["PRIORIDADE"] = df.apply(_prioridade, axis=1)

    score_prio   = (4 - df["PRIORIDADE"]) * 30
    dias_atraso  = pd.to_numeric(df.get("DIAS_ATRASO", 0), errors="coerce").fillna(0)
    score_atraso = dias_atraso.clip(upper=30) * (10 / 30)
    df["SCORE"]  = (score_prio + score_atraso).clip(0, 100).round(1)

    df = df.sort_values(
        ["PRIORIDADE", "DIAS_ATRASO", "DATA_LIMITE"],
        ascending=[True, False, True],
        na_position="last",
    ).reset_index(drop=True)
    return df

def selecionar_inspecoes(df: pd.DataFrame, max_os: int = 20, forcar_atrasadas: bool = True) -> pd.DataFrame:
    if forcar_atrasadas:
        atrasadas    = df[df["PRIORIDADE"] == int(Prioridade.MAXIMA)]
        restantes    = df[df["PRIORIDADE"] != int(Prioridade.MAXIMA)]
        slots        = max(0, max_os - len(atrasadas))
        selecionadas = pd.concat([atrasadas, restantes.head(slots)])
    else:
        selecionadas = df.head(max_os)
    return selecionadas.reset_index(drop=True)
# ───────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────
# PAGE CONFIG
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="Roteirização de Inspeção LT",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@300;400;600&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background: #0D0F14; color: #E8EAF0; }

    [data-testid="stSidebar"] {
        background: #13161D;
        border-right: 1px solid #1E2330;
    }
    [data-testid="metric-container"] {
        background: #13161D;
        border: 1px solid #1E2330;
        border-radius: 10px;
        padding: 16px 20px;
    }
    [data-testid="metric-container"] label {
        color: #7A8099 !important; font-size: 11px !important;
        letter-spacing: 0.08em; text-transform: uppercase;
    }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        font-family: 'Space Mono', monospace;
        font-size: 28px !important; color: #00CFFF !important;
    }
    .app-title {
        font-family: 'Space Mono', monospace; font-size: 22px;
        font-weight: 700; color: #00CFFF; letter-spacing: -0.02em;
        border-bottom: 2px solid #1E2330; padding-bottom: 12px; margin-bottom: 20px;
    }
    .badge-atrasada { background:#FF2D2D22;color:#FF6B6B;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:12px; }
    .badge-alta     { background:#FFD70022;color:#FFD700;padding:2px 8px;border-radius:4px;font-size:12px; }
    .badge-normal   { background:#4CAF5022;color:#81C784;padding:2px 8px;border-radius:4px;font-size:12px; }

    .device-code {
        background:#0D0F14;border:2px solid #00CFFF;border-radius:8px;
        padding:14px 20px;font-family:'Space Mono',monospace;
        font-size:26px;font-weight:700;color:#00CFFF;
        letter-spacing:0.15em;text-align:center;margin:12px 0;
    }
    .stButton > button[kind="primary"] {
        background:linear-gradient(135deg,#00CFFF,#0080FF);color:#0D0F14;
        font-weight:700;font-family:'Space Mono',monospace;
        border:none;border-radius:6px;padding:10px 24px;width:100%;
    }
    .stTabs [data-baseweb="tab-list"] { background:#13161D;border-radius:8px 8px 0 0;gap:4px; }
    .stTabs [data-baseweb="tab"]      { color:#7A8099;font-family:'Space Mono',monospace;font-size:12px; }
    .stTabs [aria-selected="true"]    { color:#00CFFF !important;border-bottom:2px solid #00CFFF; }

    .forecast-card {
        background:#13161D;border:1px solid #1E2330;border-radius:10px;
        padding:14px 10px;text-align:center;
    }
    .forecast-card.risco { border-color:#FF6B2D55; }

    iframe { color-scheme: light only !important; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────
_defaults = {
    "df_consolidado": None,
    "df_rota":        None,
    "weather_map":    {},
    "resumo":         {},
    "fabric_authed":  False,
    "fabric_token":   None,
    "fabric_user":    None,
    "_device_flow":   None,
    "_msal_app":      None,
    "_msal_token_cache": None,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

if not is_authenticated():
    tentar_login_silencioso()


# ──────────────────────────────────────────────
# SIDEBAR — LOGIN & FILTROS
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="app-title">⚡ INSPEÇÃO<br>LINHAS DE TRANSMISSÃO</div>', unsafe_allow_html=True)

    # ── Login ──
    if not is_authenticated():
        st.markdown("### 🔐 Login Energisa")

        if st.session_state["_device_flow"] is None:
            st.caption("Autentique com seu e-mail corporativo (suporta MFA).")
            if st.button("🔑 Iniciar Login", type="primary"):
                with st.spinner("Gerando código de acesso..."):
                    try:
                        flow = iniciar_device_flow()
                        st.session_state["_device_flow"] = flow
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")
        else:
            flow = st.session_state["_device_flow"]
            code = flow.get("user_code", "")
            url  = flow.get("verification_uri", "https://microsoft.com/devicelogin")

            st.markdown("#### Passo 1 — Copie o código")
            st.markdown(f'<div class="device-code">{code}</div>', unsafe_allow_html=True)
            st.markdown(f"#### Passo 2 — [Abra a página de login]({url})")
            st.markdown("#### Passo 3 — Confirme aqui")

            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Já autentiquei", type="primary"):
                    with st.spinner("Verificando..."):
                        try:
                            concluir_login()
                            st.success("✅ Autenticado!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ {e}")
            with c2:
                if st.button("↩️ Reiniciar"):
                    st.session_state["_device_flow"] = None
                    st.rerun()

        st.stop()

    # ── Usuário logado ──
    st.success(f"✅ {st.session_state['fabric_user']}")
    if st.button("Sair"):
        logout()
        st.rerun()

    st.divider()
    st.markdown("### 🔍 Filtros")

    try:
        opcoes = get_filter_options(_sid=sid_atual())
        empresas = ["Todas"] + opcoes["empresas"]
        instalacoes_por_empresa = opcoes["instalacoes_por_empresa"]
    except Exception:
        empresas = ["Todas"]
        instalacoes_por_empresa = {}

    empresa_sel    = st.selectbox("Empresa",    empresas)
    _ins_lista     = (
        ["Todas"] + instalacoes_por_empresa.get(empresa_sel, [])
        if empresa_sel != "Todas"
        else ["Todas"] + sorted({i for lst in instalacoes_por_empresa.values() for i in lst})
    )
    instalacao_sel = st.selectbox("Instalação", _ins_lista)

    st.divider()
    st.markdown("### ⚙️ Parâmetros")

    max_os           = st.slider("Máximo de OS na rota", 5, 50, 20, step=5)
    forcar_atrasadas = st.toggle("🚨 Garantir ATRASADAS na rota", value=True)
    modo_conservador = st.toggle("🌦️ Destacar risco climático", value=True,
                                  help="Clima NÃO remove OS — apenas destaca visualmente")

    # Ponto de partida
    st.markdown("##### 📍 Ponto de partida (opcional)")
    _emp_key = None if empresa_sel    == "Todas" else empresa_sel
    _ins_key = None if instalacao_sel == "Todas" else instalacao_sel

    _filtro_atual = f"{_emp_key}|{_ins_key}"
    if st.session_state.get("_filtro_anterior") != _filtro_atual:
        st.session_state["_filtro_anterior"] = _filtro_atual
        st.session_state["_sel_partida"]     = 0

    df_torres_partida = pd.DataFrame()
    if _ins_key:
        try:
            df_torres_partida = load_torres_por_instalacao(_emp_key, _ins_key, _sid=sid_atual())
        except Exception:
            pass

    ponto_partida = None
    if df_torres_partida.empty:
        if _ins_key:
            st.caption("Nenhuma torre encontrada.")
        else:
            st.caption("Selecione uma instalação para escolher o ponto de partida.")
    else:
        opcoes_partida = ["— Início automático —"] + [
            f"Torre {r['NUM_TORRE']} — {r['COD_ATIVO']}"
            for _, r in df_torres_partida.iterrows()
        ]
        sel = st.selectbox("Torre de partida", opcoes_partida,
                           index=st.session_state.get("_sel_partida", 0))
        st.session_state["_sel_partida"] = opcoes_partida.index(sel)
        if sel != opcoes_partida[0]:
            idx   = opcoes_partida.index(sel) - 1
            rp    = df_torres_partida.iloc[idx]
            ponto_partida = (float(rp["LATITUDE"]), float(rp["LONGITUDE"]))

    st.divider()
    gerar = st.button("🚀 Gerar Rota Otimizada", type="primary")


# ──────────────────────────────────────────────
# LÓGICA PRINCIPAL
# ──────────────────────────────────────────────
if gerar:
    with st.spinner("🔄 Carregando OS do Fabric..."):
        try:
            df_raw = load_inspecoes_consolidadas(
                empresa    = None if empresa_sel    == "Todas" else empresa_sel,
                instalacao = None if instalacao_sel == "Todas" else instalacao_sel,
                _sid       = sid_atual(),
            )
        except Exception as e:
            st.error(f"❌ Erro ao conectar ao Fabric: {e}")
            st.stop()

    if df_raw.empty:
        st.warning("Nenhuma OS encontrada com os filtros selecionados.")
        st.stop()

    # 1. Priorização (domain)
    df_priorizado  = priorizar(df_raw)

    # 2. Seleção de OS para a rota
    df_selecionado = selecionar_inspecoes(df_priorizado, max_os, forcar_atrasadas)

    # 3. Consulta climática das OS candidatas (apoio — não filtra)
    weather_map: dict = {}
    with st.spinner(f"🌦️ Consultando clima para {len(df_selecionado)} OS..."):
        prog = st.progress(0)
        for i, (_, row) in enumerate(df_selecionado.iterrows()):
            weather_map[row["COD_ATIVO"]] = get_weather(row["LATITUDE"], row["LONGITUDE"])
            prog.progress((i + 1) / len(df_selecionado))
            time.sleep(0.04)
        prog.empty()

    # 4. Otimização da rota (routing baseado em OS + coords via COD_ATIVO)
    df_rota = otimizar_rota(df_selecionado, ponto_partida)

    st.session_state.df_consolidado = df_priorizado
    st.session_state.df_rota        = df_rota
    st.session_state.weather_map    = weather_map
    st.session_state.resumo         = resumo_rota(df_rota)


# ──────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────
df_consolidado = st.session_state.df_consolidado
df_rota        = st.session_state.df_rota
weather_map    = st.session_state.weather_map
resumo         = st.session_state.resumo

if resumo:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📋 OS na rota",      resumo["total_os"])
    c2.metric("🔴 Atrasadas",        resumo["os_atrasadas"])
    c3.metric("📏 Distância total",  f"{resumo['distancia_total']} km")
    c4.metric("⚡ Criticidade mín.", resumo["criticidade_min"])
    c5.metric("📊 Score médio",      f"{resumo['score_medio']}%")
    st.divider()

tab_mapa, tab_rota, tab_os, tab_clima = st.tabs([
    "🗺️  Mapa",
    "📋  Rota",
    "📂  OS Detalhadas",
    "🌦️  Clima 5 dias",
])


# ── TAB MAPA ──
with tab_mapa:
    if df_consolidado is not None and not df_consolidado.empty:
        _hash = f"{len(df_rota)}_{df_rota['COD_ATIVO'].iloc[0] if df_rota is not None and not df_rota.empty else 'x'}"
        mapa  = build_map(df=df_consolidado, df_rota=df_rota, weather_map=weather_map)
        st_folium(mapa, use_container_width=True, height=580,
                  key=f"mapa_{_hash}", returned_objects=[])
    else:
        st.info("👈 Configure os filtros e clique em **Gerar Rota Otimizada**.")
        import folium
        m = folium.Map(location=[-15.8, -47.9], zoom_start=4, tiles=None)
        folium.TileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri", name="🛰️ Satélite",
        ).add_to(m)
        st_folium(m, use_container_width=True, height=500, key="mapa_vazio", returned_objects=[])


# ── TAB ROTA ──
with tab_rota:
    if df_rota is not None and not df_rota.empty:
        st.markdown(f"#### Rota com **{len(df_rota)} OS** — {resumo['distancia_total']} km")

        # Cores por prioridade
        def _cor_prioridade_css(v):
            try:
                p = int(v)
                if p == int(Prioridade.MAXIMA): return "background:#FF2D2D22;color:#FF6B6B;font-weight:bold"
                if p == int(Prioridade.ALTA):   return "background:#FFD70022;color:#FFD700"
            except Exception: pass
            return ""

        def _label_prioridade(v):
            try:
                p = int(v)
                return {
                    int(Prioridade.MAXIMA): "🔴 ATRASADA",
                    int(Prioridade.ALTA):   "🟡 VENCE EM BREVE",
                    int(Prioridade.NORMAL): "🟢 NO PRAZO",
                }.get(p, str(v))
            except Exception:
                return str(v)

        df_exibir = df_rota[[
            "ORDEM_VISITA", "DESC_NUMERO_OS", "COD_ATIVO", "NUM_TORRE",
            "SIGLA_EMPRESA", "INSTALACAO", "DESC_ESTADO",
            "STATUS_PRAZO", "DATA_LIMITE", "DIAS_ATRASO",
            "PRIORIDADE", "SCORE", "DIST_PROX_KM", "DIST_ACUM_KM",
        ]].copy()

        df_exibir["PRIORIDADE"]   = df_exibir["PRIORIDADE"].apply(_label_prioridade)
        df_exibir["DATA_LIMITE"]  = pd.to_datetime(df_exibir["DATA_LIMITE"], errors="coerce").dt.strftime("%d/%m/%Y")
        df_exibir["SCORE"]        = df_exibir["SCORE"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "–")
        df_exibir["DIST_PROX_KM"] = df_exibir["DIST_PROX_KM"].apply(lambda v: f"{v:.1f}" if v else "–")
        df_exibir["DIST_ACUM_KM"] = df_exibir["DIST_ACUM_KM"].apply(lambda v: f"{v:.1f}" if v else "–")

        rename = {
            "ORDEM_VISITA":  "Ordem",    "DESC_NUMERO_OS": "OS",
            "COD_ATIVO":     "Ativo",    "NUM_TORRE":       "Torre",
            "SIGLA_EMPRESA": "Empresa",  "INSTALACAO":      "Instalação",
            "DESC_ESTADO":   "Estado",   "STATUS_PRAZO":    "Status",
            "DATA_LIMITE":   "Limite",   "DIAS_ATRASO":     "Atraso (d)",
            "PRIORIDADE":    "Prioridade","SCORE":           "Score",
            "DIST_PROX_KM":  "Dist→(km)","DIST_ACUM_KM":    "Acum.(km)",
        }
        df_exibir = df_exibir.rename(columns=rename)

        styled = df_exibir.style.map(
            lambda v: "background:#FF2D2D22;color:#FF6B6B;font-weight:bold" if "ATRASADA" in str(v)
            else ("background:#FFD70022;color:#FFD700" if "VENCE" in str(v) else ""),
            subset=["Prioridade"]
        ).map(
            lambda v: "color:#FF6B6B;font-weight:bold" if int(v) > 0 else ""
            if pd.notna(v) and str(v).lstrip('-').isdigit() else "",
            subset=["Atraso (d)"]
        )

        st.dataframe(styled, use_container_width=True, hide_index=True)

        # Export Excel
        xlsx = _gerar_excel_rota(df_exibir, resumo)
        st.download_button("📥 Exportar Excel", xlsx, "rota_inspecao.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("Gere a rota para ver o detalhamento.")


# ── TAB OS DETALHADAS ──
with tab_os:
    if df_consolidado is not None and not df_consolidado.empty:
        st.markdown(f"#### {len(df_consolidado)} OS consolidadas (todas, ordenadas por prioridade)")

        col_filtro, col_vazio = st.columns([1, 3])
        with col_filtro:
            filtro_status = st.selectbox("Filtrar por status",
                                         ["Todos", "🔴 Atrasadas", "🟡 Alta", "🟢 Normal"])

        df_os = df_consolidado.copy()
        if filtro_status == "🔴 Atrasadas":
            df_os = df_os[df_os["PRIORIDADE"] == int(Prioridade.MAXIMA)]
        elif filtro_status == "🟡 Alta":
            df_os = df_os[df_os["PRIORIDADE"] == int(Prioridade.ALTA)]
        elif filtro_status == "🟢 Normal":
            df_os = df_os[df_os["PRIORIDADE"] == int(Prioridade.NORMAL)]

        colunas = ["DESC_NUMERO_OS", "COD_ATIVO", "NUM_TORRE", "SIGLA_EMPRESA",
                   "INSTALACAO", "STATUS_PRAZO", "DATA_LIMITE", "DIAS_ATRASO",
                   "PRIORIDADE", "SCORE", "DESC_ESTADO"]
        df_os_exib = df_os[[c for c in colunas if c in df_os.columns]].copy()
        df_os_exib["DATA_LIMITE"] = pd.to_datetime(df_os_exib["DATA_LIMITE"], errors="coerce").dt.strftime("%d/%m/%Y")

        st.dataframe(df_os_exib, use_container_width=True, hide_index=True)

        xlsx_os = _gerar_excel_os(df_os_exib)
        st.download_button("📥 Exportar OS Excel", xlsx_os, "os_detalhadas.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="dl_os")
    else:
        st.info("Gere a rota para ver as OS detalhadas.")


# ── TAB CLIMA 5 DIAS ──
with tab_clima:
    if df_rota is not None and not df_rota.empty:
        st.markdown("#### 🌦️ Previsão de 5 dias — OS da rota")
        st.caption("⚠️ Clima é apenas apoio — não remove OS da rota.")

        # Seleciona OS para ver previsão
        os_opcoes = df_rota["COD_ATIVO"].tolist()
        os_sel    = st.selectbox("Selecione o ativo:", os_opcoes,
                                  format_func=lambda c: f"{c} — Torre {df_rota.loc[df_rota['COD_ATIVO']==c,'NUM_TORRE'].values[0] if len(df_rota.loc[df_rota['COD_ATIVO']==c])>0 else '?'}")

        row_sel = df_rota[df_rota["COD_ATIVO"] == os_sel].iloc[0]
        lat, lon = float(row_sel["LATITUDE"]), float(row_sel["LONGITUDE"])

        with st.spinner("Buscando previsão..."):
            forecast = get_forecast_5d(lat, lon)
            clima_atual = weather_map.get(os_sel) or get_weather(lat, lon)

        # Clima atual
        if clima_atual.get("ok"):
            badge = "⛔ RISCO OPERACIONAL" if clima_atual["risco"] else "✅ Condições OK"
            st.markdown(f"""
            **Clima atual** &nbsp; {badge}
            | 🌡️ {clima_atual['temperatura']}°C | 💧 {clima_atual['umidade']}% | 💨 {clima_atual['vento_kmh']} km/h | 🌧️ {clima_atual['chuva_mm']} mm/h
            """)

        st.divider()

        # Cards de 5 dias
        if forecast:
            cols = st.columns(min(len(forecast), 5))
            for i, dia in enumerate(forecast[:5]):
                with cols[i]:
                    card_class = "forecast-card risco" if dia["risco"] else "forecast-card"
                    risco_badge = "⛔" if dia["risco"] else "✅"
                    st.markdown(f"""
                    <div class="{card_class}">
                        <div style='font-size:16px;font-weight:bold;color:#00CFFF'>{dia['data']}</div>
                        <div style='font-size:22px;margin:6px 0'>
                            <img src='https://openweathermap.org/img/wn/{dia['icone']}@2x.png' width='48'>
                        </div>
                        <div style='font-size:13px;color:#E8EAF0;margin-bottom:4px'>{dia['descricao']}</div>
                        <div style='font-size:12px;color:#7A8099'>
                            🌡️ {dia['temp_min']}° / {dia['temp_max']}°C<br>
                            💨 {dia['vento_kmh']} km/h<br>
                            🌧️ {dia['chuva_mm']} mm<br>
                            {risco_badge}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.warning("Previsão não disponível para esta localização.")

        st.divider()

        # Resumo climático de todas as OS da rota
        st.markdown("#### Resumo climático — todas as OS da rota")
        dados_clima = []
        for cod, info in weather_map.items():
            if not isinstance(info, dict):
                continue
            row_match = df_rota[df_rota["COD_ATIVO"] == cod]
            torre = row_match["NUM_TORRE"].values[0] if not row_match.empty else "–"
            dados_clima.append({
                "Ativo":       cod,
                "Torre":       torre,
                "Condição":    info.get("descricao", "N/D") if info.get("ok") else f"Erro: {info.get('erro','')}",
                "Temp (°C)":   round(info["temperatura"], 1) if info.get("ok") else "–",
                "Umidade (%)": info.get("umidade", "–") if info.get("ok") else "–",
                "Vento (km/h)":info.get("vento_kmh", "–") if info.get("ok") else "–",
                "Chuva (mm/h)":info.get("chuva_mm", "–") if info.get("ok") else "–",
                "Status":      "⛔ RISCO" if info.get("risco") else "✅ OK",
            })

        if dados_clima:
            df_clima_tab = pd.DataFrame(dados_clima)
            st.dataframe(
                df_clima_tab.style.map(
                    lambda v: "color:#FF6B6B;font-weight:bold" if "RISCO" in str(v) else "",
                    subset=["Status"]
                ),
                use_container_width=True, hide_index=True,
            )
    else:
        st.info("Gere a rota para ver a previsão climática.")


# ──────────────────────────────────────────────
# HELPERS DE EXPORT EXCEL
# ──────────────────────────────────────────────

def _estilo_base_excel():
    thin   = Side(style="thin", color="1E2330")
    borda  = Border(left=thin, right=thin, top=thin, bottom=thin)
    return borda


def _gerar_excel_rota(df: pd.DataFrame, resumo: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Rota de Inspeção"

    borda = _estilo_base_excel()

    ws.merge_cells("A1:N1")
    tc = ws["A1"]
    tc.value = f"Rota de Inspeção — {resumo.get('total_os','?')} OS | {resumo.get('distancia_total','?')} km"
    tc.font  = Font(name="Arial", bold=True, size=14, color="00CFFF")
    tc.fill  = PatternFill("solid", fgColor="0D1117")
    tc.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A2:N2")
    sc = ws["A2"]
    sc.value = f"Gerado em: {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M')}"
    sc.font  = Font(name="Arial", size=9, color="7A8099")
    sc.fill  = PatternFill("solid", fgColor="0D1117")
    sc.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[2].height = 16

    headers = list(df.columns)
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=ci, value=h)
        cell.font      = Font(name="Arial", bold=True, size=10, color="00CFFF")
        cell.fill      = PatternFill("solid", fgColor="1A1F2E")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = borda
    ws.row_dimensions[3].height = 28

    for ri, row_data in enumerate(df.itertuples(index=False), 4):
        bg = "13161D" if ri % 2 == 0 else "0D0F14"
        for ci, val in enumerate(row_data, 1):
            col_name = headers[ci - 1]
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.fill      = PatternFill("solid", fgColor=bg)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border    = borda

            fg = "E8EAF0"
            bold = False
            if col_name == "Prioridade":
                if "ATRASADA" in str(val):   fg, bold = "FF6B6B", True
                elif "VENCE"  in str(val):   fg = "FFD700"
                else:                        fg = "81C784"
            elif col_name == "Atraso (d)" and str(val) not in ("0", "–", ""):
                try:
                    if int(val) > 0: fg, bold = "FF6B6B", True
                except Exception: pass

            cell.font = Font(name="Arial", size=10, color=fg, bold=bold)
        ws.row_dimensions[ri].height = 20

    for ci, h in enumerate(headers, 1):
        widths = {"Ordem":8,"OS":16,"Ativo":14,"Torre":8,"Empresa":10,"Instalação":18,
                  "Estado":16,"Status":14,"Limite":12,"Atraso (d)":12,
                  "Prioridade":18,"Score":10,"Dist→(km)":12,"Acum.(km)":12}
        ws.column_dimensions[get_column_letter(ci)].width = widths.get(h, 14)

    ws.freeze_panes = "A4"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _gerar_excel_os(df: pd.DataFrame) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "OS Consolidadas"
    borda = _estilo_base_excel()

    ws.merge_cells(f"A1:{get_column_letter(len(df.columns))}1")
    tc = ws["A1"]
    tc.value = f"OS Consolidadas — Gerado em {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M')}"
    tc.font  = Font(name="Arial", bold=True, size=13, color="00CFFF")
    tc.fill  = PatternFill("solid", fgColor="0D1117")
    tc.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    headers = list(df.columns)
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=ci, value=h)
        cell.font      = Font(name="Arial", bold=True, size=10, color="00CFFF")
        cell.fill      = PatternFill("solid", fgColor="1A1F2E")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = borda
    ws.row_dimensions[2].height = 26

    for ri, row_data in enumerate(df.itertuples(index=False), 3):
        bg = "13161D" if ri % 2 == 0 else "0D0F14"
        for ci, val in enumerate(row_data, 1):
            col_name = headers[ci - 1]
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.fill      = PatternFill("solid", fgColor=bg)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border    = borda
            fg   = "E8EAF0"
            bold = False
            if col_name == "STATUS_PRAZO" and str(val) == "ATRASADA":
                fg, bold = "FF6B6B", True
            cell.font = Font(name="Arial", size=10, color=fg, bold=bold)
        ws.row_dimensions[ri].height = 18

    for ci in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 16
    ws.freeze_panes = "A3"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
