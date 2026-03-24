"""
app.py — App principal de roteirização de inspeção de linhas de transmissão.
"""

import time
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from services.database import (
    login_fabric, is_authenticated,
    load_torres_criticidade, get_filter_options,
)
from services.weather  import get_weather, weather_badge
from components.mapa   import build_map
from utils.routing     import (
    calcular_score,
    aplicar_filtro_clima,
    selecionar_torres,
    otimizar_rota,
    resumo_rota,
)

# ──────────────────────────────────────────────
# CONFIGURAÇÃO DA PÁGINA
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="Roteirização de Inspeção",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────
# CSS CUSTOMIZADO
# ──────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .stApp {
        background: #0D0F14;
        color: #E8EAF0;
    }

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
        color: #7A8099 !important;
        font-size: 11px !important;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        font-family: 'Space Mono', monospace;
        font-size: 28px !important;
        color: #00CFFF !important;
    }

    .app-title {
        font-family: 'Space Mono', monospace;
        font-size: 22px;
        font-weight: 700;
        color: #00CFFF;
        letter-spacing: -0.02em;
        border-bottom: 2px solid #1E2330;
        padding-bottom: 12px;
        margin-bottom: 20px;
    }

    .badge-risco {
        background: #FF2D2D22;
        color: #FF6B6B;
        border: 1px solid #FF2D2D44;
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
    }
    .badge-ok {
        background: #4CAF5022;
        color: #81C784;
        border: 1px solid #4CAF5044;
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
    }

    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #00CFFF, #0080FF);
        color: #0D0F14;
        font-weight: 700;
        font-family: 'Space Mono', monospace;
        border: none;
        border-radius: 6px;
        padding: 10px 24px;
        letter-spacing: 0.05em;
        width: 100%;
    }
    .stButton > button[kind="primary"]:hover {
        opacity: 0.9;
        transform: translateY(-1px);
    }

    [data-testid="stDataFrame"] {
        border: 1px solid #1E2330;
        border-radius: 8px;
    }

    .stTabs [data-baseweb="tab-list"] {
        background: #13161D;
        border-radius: 8px 8px 0 0;
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        color: #7A8099;
        font-family: 'Space Mono', monospace;
        font-size: 12px;
    }
    .stTabs [aria-selected="true"] {
        color: #00CFFF !important;
        border-bottom: 2px solid #00CFFF;
    }

    .clima-alert {
        background: #FF6B2D15;
        border-left: 3px solid #FF6B2D;
        border-radius: 0 6px 6px 0;
        padding: 10px 14px;
        font-size: 13px;
        margin: 8px 0;
    }

    .login-box {
        background: #13161D;
        border: 1px solid #1E2330;
        border-radius: 10px;
        padding: 20px;
        margin-top: 12px;
    }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# ESTADO DA SESSÃO
# ──────────────────────────────────────────────
for key, default in [
    ("df_rota", None),
    ("df_base", None),
    ("weather_map", {}),
    ("resumo", {}),
    ("fabric_authed", False),
    ("fabric_token", None),
    ("fabric_user", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ──────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="app-title">⚡ INSPEÇÃO<br>LINHAS DE TRANSMISSÃO</div>', unsafe_allow_html=True)

    # ── LOGIN ──
    if not is_authenticated():
        st.markdown("### 🔐 Login Energisa")
        st.caption("Use seu e-mail e senha corporativos.")
        email_input = st.text_input("E-mail", placeholder="joao@energisa.com.br")
        senha_input = st.text_input("Senha", type="password")
        if st.button("Entrar", type="primary"):
            if not email_input or not senha_input:
                st.error("Preencha e-mail e senha.")
            else:
                with st.spinner("Autenticando..."):
                    try:
                        login_fabric(email_input, senha_input)
                        st.success("✅ Autenticado!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")
        st.stop()

    # ── USUÁRIO LOGADO ──
    st.success(f"✅ {st.session_state['fabric_user']}")
    if st.button("Sair"):
        for k in ["fabric_authed", "fabric_token", "fabric_user",
                  "df_rota", "df_base", "weather_map", "resumo"]:
            st.session_state[k] = None if k != "fabric_authed" else False
        st.rerun()

    st.divider()
    st.markdown("### 🔍 Filtros")

    try:
        opcoes = get_filter_options()
        empresas    = ["Todas"] + opcoes["empresas"]
        instalacoes = ["Todas"] + opcoes["instalacoes"]
    except Exception:
        empresas    = ["Todas"]
        instalacoes = ["Todas"]

    empresa_sel    = st.selectbox("Empresa", empresas)
    instalacao_sel = st.selectbox("Instalação", instalacoes)

    st.divider()
    st.markdown("### ⚙️ Parâmetros da Rota")

    max_torres = st.slider("Máximo de torres na rota", 5, 50, 20, step=5)

    modo_conservador = st.toggle(
        "🌦️ Evitar torres com risco climático",
        value=True,
        help="Remove torres com chuva forte ou vento acima de 36 km/h",
    )

    forcar_atrasadas = st.toggle(
        "🚨 Priorizar torres atrasadas",
        value=True,
        help="Torres com FL_ATRASADO=1 sempre entram na rota",
    )

    st.markdown("##### 📍 Ponto de partida (opcional)")
    col_lat, col_lon = st.columns(2)
    lat_base = col_lat.number_input("Lat", value=None, placeholder="-23.5")
    lon_base = col_lon.number_input("Lon", value=None, placeholder="-46.6")
    ponto_partida = (lat_base, lon_base) if (lat_base and lon_base) else None

    st.divider()
    gerar = st.button("🚀 Gerar Rota Otimizada", type="primary")


# ──────────────────────────────────────────────
# LÓGICA PRINCIPAL — ao clicar em "Gerar Rota"
# ──────────────────────────────────────────────
if gerar:
    with st.spinner("🔄 Carregando torres do Fabric..."):
        try:
            df_raw = load_torres_criticidade(
                empresa    = None if empresa_sel    == "Todas" else empresa_sel,
                instalacao = None if instalacao_sel == "Todas" else instalacao_sel,
            )
        except Exception as e:
            st.error(f"❌ Erro ao conectar ao Fabric: {e}")
            st.stop()

    if df_raw.empty:
        st.warning("Nenhuma torre encontrada com os filtros selecionados.")
        st.stop()

    df_scored = calcular_score(df_raw)

    candidatas = df_scored.nlargest(50, "SCORE")
    weather_map: dict = {}

    with st.spinner(f"🌦️ Consultando clima para {len(candidatas)} torres..."):
        progress = st.progress(0)
        for i, (_, row) in enumerate(candidatas.iterrows()):
            weather_map[row["COD_ATIVO"]] = get_weather(row["LATITUDE"], row["LONGITUDE"])
            progress.progress((i + 1) / len(candidatas))
            time.sleep(0.05)
        progress.empty()

    df_filtrado    = aplicar_filtro_clima(df_scored, weather_map, modo_conservador)
    df_selecionadas = selecionar_torres(df_filtrado, max_torres, forcar_atrasadas)
    df_rota        = otimizar_rota(df_selecionadas, ponto_partida)

    st.session_state.df_rota     = df_rota
    st.session_state.df_base     = df_raw
    st.session_state.weather_map = weather_map
    st.session_state.resumo      = resumo_rota(df_rota)


# ──────────────────────────────────────────────
# PAINEL PRINCIPAL
# ──────────────────────────────────────────────
df_rota     = st.session_state.df_rota
df_base     = st.session_state.df_base
weather_map = st.session_state.weather_map
resumo      = st.session_state.resumo

if resumo:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🗼 Torres na rota",     resumo["total_torres"])
    c2.metric("⚠️ Atrasadas",          resumo["torres_atrasadas"])
    c3.metric("📏 Distância total",    f"{resumo['distancia_total']} km")
    c4.metric("🔴 Criticidade mínima", resumo["criticidade_min"])
    c5.metric("📊 Score médio",        resumo["score_medio"])
    st.divider()

tab_mapa, tab_rota, tab_clima, tab_ocorrencias = st.tabs([
    "🗺️  Mapa",
    "📋  Rota detalhada",
    "🌦️  Clima",
    "⚠️  Ocorrências",
])

with tab_mapa:
    if df_base is not None:
        mapa = build_map(df=df_base, df_rota=df_rota, weather_map=weather_map)
        st_folium(mapa, use_container_width=True, height=580)
    else:
        st.info("👈 Configure os filtros e clique em **Gerar Rota Otimizada** para visualizar o mapa.")
        import folium
        mapa_vazio = folium.Map(location=[-15.8, -47.9], zoom_start=4, tiles="CartoDB dark_matter")
        st_folium(mapa_vazio, use_container_width=True, height=500)

with tab_rota:
    if df_rota is not None:
        st.markdown(f"#### Rota com {len(df_rota)} torres — distância total: **{resumo['distancia_total']} km**")
        colunas_exibir = [
            "ORDEM_VISITA", "COD_ATIVO", "NUM_TORRE", "EMPRESA", "INSTALACAO",
            "CRITICIDADE_MIN", "QTD_SS", "PIOR_SALDO_DIAS", "FL_ATRASADO",
            "SCORE", "DIST_PROX_KM", "DIST_ACUM_KM",
        ]
        colunas_disponiveis = [c for c in colunas_exibir if c in df_rota.columns]
        st.dataframe(
            df_rota[colunas_disponiveis].style
            .background_gradient(subset=["CRITICIDADE_MIN"], cmap="RdYlGn_r")
            .background_gradient(subset=["SCORE"], cmap="Blues")
            .applymap(
                lambda v: "color: #FF6B6B; font-weight:bold" if v == 1 else "",
                subset=["FL_ATRASADO"]
            ),
            use_container_width=True,
            hide_index=True,
        )
        csv = df_rota[colunas_disponiveis].to_csv(index=False).encode("utf-8")
        st.download_button("📥 Exportar rota CSV", csv, "rota_inspecao.csv", "text/csv")
    else:
        st.info("Gere a rota para ver o detalhamento aqui.")

with tab_clima:
    if weather_map:
        dados_clima = []
        for cod, info in weather_map.items():
            if info.get("ok"):
                dados_clima.append({
                    "Torre (COD_ATIVO)": cod,
                    "Condição":    info["descricao"],
                    "Temp (°C)":   info["temperatura"],
                    "Umidade (%)": info["umidade"],
                    "Vento (km/h)": info["vento_kmh"],
                    "Chuva (mm/h)": info["chuva_mm"],
                    "Status":      "⛔ RISCO" if info["risco"] else "✅ OK",
                })
        df_clima = pd.DataFrame(dados_clima)
        torres_risco = df_clima[df_clima["Status"] == "⛔ RISCO"]
        if len(torres_risco):
            st.markdown(
                f'<div class="clima-alert">⛔ <b>{len(torres_risco)} torres</b> com condição climática adversa '
                f'{"foram removidas da rota" if modo_conservador else "estão na rota (modo não conservador)"}.</div>',
                unsafe_allow_html=True,
            )
        st.dataframe(df_clima, use_container_width=True, hide_index=True)
    else:
        st.info("Gere a rota para ver as condições climáticas.")

with tab_ocorrencias:
    if df_rota is not None:
        torre_sel = st.selectbox(
            "Selecione uma torre para ver ocorrências",
            options=df_rota["COD_ATIVO"].tolist(),
            format_func=lambda c: f"{c} — Torre {df_rota.loc[df_rota['COD_ATIVO']==c,'NUM_TORRE'].values[0] if 'NUM_TORRE' in df_rota.columns else ''}",
        )
        if torre_sel:
            try:
                from services.database import load_ocorrencias
                with st.spinner("Carregando ocorrências..."):
                    df_oc = load_ocorrencias(torre_sel)
                if df_oc.empty:
                    st.info("Nenhuma ocorrência pendente para esta torre.")
                else:
                    st.dataframe(df_oc, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Erro ao carregar ocorrências: {e}")
    else:
        st.info("Gere a rota para consultar ocorrências por torre.")
