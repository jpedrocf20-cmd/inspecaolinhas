"""
app.py — App principal de roteirização de inspeção de linhas de transmissão.
"""

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
    tentar_login_silencioso,
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

    .login-box {
        background: #13161D;
        border: 1px solid #1E2330;
        border-radius: 10px;
        padding: 20px;
        margin-top: 12px;
    }

    .device-code {
        background: #0D0F14;
        border: 2px solid #00CFFF;
        border-radius: 8px;
        padding: 14px 20px;
        font-family: 'Space Mono', monospace;
        font-size: 26px;
        font-weight: 700;
        color: #00CFFF;
        letter-spacing: 0.15em;
        text-align: center;
        margin: 12px 0;
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
    .stButton > button[kind="primary"]:hover { opacity: 0.9; transform: translateY(-1px); }

    [data-testid="stDataFrame"] { border: 1px solid #1E2330; border-radius: 8px; }

    .stTabs [data-baseweb="tab-list"] { background: #13161D; border-radius: 8px 8px 0 0; gap: 4px; }
    .stTabs [data-baseweb="tab"] { color: #7A8099; font-family: 'Space Mono', monospace; font-size: 12px; }
    .stTabs [aria-selected="true"] { color: #00CFFF !important; border-bottom: 2px solid #00CFFF; }

    .clima-alert {
        background: #FF6B2D15;
        border-left: 3px solid #FF6B2D;
        border-radius: 0 6px 6px 0;
        padding: 10px 14px;
        font-size: 13px;
        margin: 8px 0;
    }

    /* ── PROTEÇÃO DO IFRAME DO MAPA FOLIUM ──
       Força color-scheme:light no iframe para evitar que o tema escuro
       do Streamlit vaze e escureça o canvas do Leaflet ao interagir.
       "light only" impede herança do color-scheme pai completamente. */
    iframe {
        color-scheme: light only !important;
    }
    [data-testid="stCustomComponentV1"] > div > iframe,
    .element-container iframe,
    .stIFrame > iframe,
    .stIframe > iframe {
        color-scheme: light only !important;
        background-color: transparent !important;
        filter: none !important;
        /* Evita piscar preto durante re-render do Streamlit */
        content-visibility: auto !important;
    }
    /* Previne que o container do mapa pisque ao trocar de aba */
    [data-testid="stCustomComponentV1"] {
        min-height: 0 !important;
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
    ("modo_conservador", True),
    ("_device_flow", None),
    ("_msal_app", None),
    ("_msal_token_cache", None),   # cache serializado para renovação silenciosa
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── Tentativa de renovação silenciosa de token ──
# Se o usuário já autenticou anteriormente (nesta sessão do navegador ou em
# uma sessão anterior cujo cache foi preservado), tenta renovar o access token
# sem interação. Só executa se ainda não estiver autenticado neste rerun.
if not is_authenticated() and st.session_state.get("_msal_token_cache"):
    tentar_login_silencioso()


# ──────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="app-title">⚡ INSPEÇÃO<br>LINHAS DE TRANSMISSÃO</div>', unsafe_allow_html=True)

    # ── BLOCO DE LOGIN ──
    if not is_authenticated():
        st.markdown("### 🔐 Login Energisa")

        # Estado: ainda não iniciou o flow
        if st.session_state["_device_flow"] is None:
            st.caption("Clique abaixo para gerar o código de acesso. Você precisará autenticar pelo navegador com seu e-mail e senha corporativos (incluindo MFA).")
            if st.button("🔑 Iniciar Login", type="primary"):
                with st.spinner("Gerando código de acesso..."):
                    try:
                        flow = iniciar_device_flow()
                        st.session_state["_device_flow"] = flow
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

        # Estado: flow iniciado, aguardando autenticação no navegador
        else:
            flow = st.session_state["_device_flow"]
            code = flow.get("user_code", "")
            url  = flow.get("verification_uri", "https://microsoft.com/devicelogin")

            st.markdown("#### Passo 1 — Copie o código")
            st.markdown(f'<div class="device-code">{code}</div>', unsafe_allow_html=True)

            st.markdown(f"#### Passo 2 — Abra o link e cole o código")
            st.markdown(f"[🌐 Abrir página de login]({url})", unsafe_allow_html=False)
            st.caption(f"URL: `{url}`")

            st.markdown("#### Passo 3 — Confirme aqui após autenticar")
            col1, col2 = st.columns(2)

            with col1:
                if st.button("✅ Já autentiquei", type="primary"):
                    with st.spinner("Verificando autenticação..."):
                        try:
                            concluir_login()
                            st.success("✅ Autenticado!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ {e}")

            with col2:
                if st.button("↩️ Reiniciar"):
                    st.session_state["_device_flow"] = None
                    st.session_state["_msal_app"]    = None
                    st.rerun()

        st.stop()

    # ── USUÁRIO LOGADO ──
    st.success(f"✅ {st.session_state['fabric_user']}")
    if st.button("Sair"):
        for k in ["fabric_authed", "fabric_token", "fabric_user",
                  "df_rota", "df_base", "weather_map", "resumo",
                  "_device_flow", "_msal_app", "_msal_token_cache"]:
            st.session_state[k] = None if k != "fabric_authed" else False
        st.rerun()

    st.divider()
    st.markdown("### 🔍 Filtros")

    try:
        opcoes = get_filter_options()
        empresas = ["Todas"] + opcoes["empresas"]
        instalacoes_por_empresa = opcoes["instalacoes_por_empresa"]
    except Exception:
        empresas = ["Todas"]
        instalacoes_por_empresa = {}

    empresa_sel = st.selectbox("Empresa", empresas)

    # Filtra instalações com base na empresa selecionada
    if empresa_sel == "Todas":
        # Todas as instalações de todas as empresas, sem duplicatas
        todas = []
        for lst in instalacoes_por_empresa.values():
            todas.extend(lst)
        lista_instalacoes = ["Todas"] + sorted(set(todas))
    else:
        lista_instalacoes = ["Todas"] + instalacoes_por_empresa.get(empresa_sel, [])

    instalacao_sel = st.selectbox("Instalação", lista_instalacoes)

    st.divider()
    st.markdown("### ⚙️ Parâmetros da Rota")

    max_torres = st.slider("Máximo de torres na rota", 5, 50, 20, step=5)

    modo_conservador = st.toggle(
        "🌦️ Evitar torres com risco climático",
        value=True,
        help="Remove torres com chuva forte ou vento acima de 36 km/h",
    )
    st.session_state["modo_conservador"] = modo_conservador

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

    df_filtrado     = aplicar_filtro_clima(df_scored, weather_map, modo_conservador)
    df_selecionadas = selecionar_torres(df_filtrado, max_torres, forcar_atrasadas)
    df_rota         = otimizar_rota(df_selecionadas, ponto_partida)

    # Garante clima para TODAS as torres da rota final
    torres_sem_clima = [
        row for _, row in df_rota.iterrows()
        if row["COD_ATIVO"] not in weather_map
    ]
    if torres_sem_clima:
        with st.spinner(f"🌦️ Consultando clima para {len(torres_sem_clima)} torre(s) adicional(is)..."):
            for row in torres_sem_clima:
                weather_map[row["COD_ATIVO"]] = get_weather(row["LATITUDE"], row["LONGITUDE"])

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
    c5.metric("📊 Score médio",        f"{resumo['score_medio']}%")
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
        st_folium(
            mapa,
            use_container_width=True,
            height=580,
            key="mapa_principal",
            returned_objects=[],
        )
    else:
        st.info("👈 Configure os filtros e clique em **Gerar Rota Otimizada** para visualizar o mapa.")
        import folium
        mapa_vazio = folium.Map(location=[-15.8, -47.9], zoom_start=4, tiles=None)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri", name="🛰️ Satélite",
        ).add_to(mapa_vazio)
        st_folium(
            mapa_vazio,
            use_container_width=True,
            height=500,
            key="mapa_vazio",
            returned_objects=[],
        )

with tab_rota:
    if df_rota is not None:
        st.markdown(f"#### Rota com {len(df_rota)} torres — distância total: **{resumo['distancia_total']} km**")

        # ── Legenda do Score ──
        with st.expander("ℹ️ Como o Score é calculado?", expanded=False):
            st.markdown("""
            O **Score (0–100%)** representa a urgência de inspeção de cada torre.
            Quanto mais próximo de 100%, mais crítica e urgente é a visita.

            | Componente | Peso | Máximo |
            |---|---|---|
            | 🔴 Criticidade (nível 1 a 6) | Nível 1 vale 60 pts, nível 6 vale 0 pts | 60 pts |
            | 📋 Qtd. de ocorrências abertas (cap 20) | 2 pts por OS | 40 pts |
            | ⚠️ Dias de atraso (cap 20 dias, só se atrasada) | 5 pts por dia | 100 pts |

            **Exemplo:** Torre nível 1, 5 OS, 10 dias atrasada →
            60 + 10 + 50 = 120 pts brutos → **Score = 60%**
            """)

        # ── Prepara exibição ──
        df_exibir = df_rota.copy()

        # Formata colunas numéricas ANTES de estilizar
        def _fmt_int(v):
            try: return int(float(v))
            except: return None
        def _fmt_f1(v):
            try: return round(float(v), 1)
            except: return None

        def _fmt_int_str(v):
            try:
                return str(int(float(v)))
            except (TypeError, ValueError):
                return None

        for col in ["ORDEM_VISITA","NUM_TORRE","CRITICIDADE_MIN","QTD_SS","PIOR_SALDO_DIAS","FL_ATRASADO"]:
            if col in df_exibir.columns:
                df_exibir[col] = df_exibir[col].apply(_fmt_int_str)
        for col in ["SCORE"]:
            if col in df_exibir.columns:
                df_exibir[col] = df_exibir[col].apply(
                    lambda v: f"{int(round(float(v)))}%" if pd.notna(v) else "–"
                )
        for col in ["DIST_PROX_KM","DIST_ACUM_KM"]:
            if col in df_exibir.columns:
                df_exibir[col] = df_exibir[col].apply(
                    lambda v: f"{float(v):.1f}" if v is not None else "–"
                )

        # Renomeia colunas para português
        rename = {
            "ORDEM_VISITA":   "Ordem",
            "COD_ATIVO":      "Ativo",
            "NUM_TORRE":      "Torre",
            "EMPRESA":        "Empresa",
            "INSTALACAO":     "Instalação",
            "CRITICIDADE_MIN":"Criticidade",
            "QTD_SS":         "Qtd SS",
            "PIOR_SALDO_DIAS":"Pior saldo (dias)",
            "FL_ATRASADO":    "Atrasado",
            "SCORE":          "Score (%)",
            "DIST_PROX_KM":   "Dist. próx (km)",
            "DIST_ACUM_KM":   "Dist. acum (km)",
        }
        colunas_exibir = [k for k in rename if k in df_exibir.columns]
        df_exibir = df_exibir[colunas_exibir].rename(columns=rename)

        # Converte FL_ATRASADO de 0/1 para texto legível
        if "Atrasado" in df_exibir.columns:
            df_exibir["Atrasado"] = df_exibir["Atrasado"].apply(
                lambda v: "⚠️ Sim" if str(v) == "1" else "—"
            )

        # Estilos CSS puros — sem matplotlib
        CORES_CRIT = {
            1: "background:#FF2D2D33;color:#FF6B6B;font-weight:bold",
            2: "background:#FF6B2D33;color:#FFA07A;font-weight:bold",
            3: "background:#FFA50033;color:#FFC04D",
            4: "background:#FFD70033;color:#FFE680",
            5: "background:#90EE9033;color:#B8F0B8",
            6: "background:#4CAF5033;color:#81C784",
        }
        def _s_crit(v):
            try: return CORES_CRIT.get(int(v), "")
            except: return ""
        def _s_score(v):
            try:
                f = float(str(v).replace("%", ""))
                if f >= 80: return "background:#FF2D2D33;color:#FF6B6B;font-weight:bold"
                if f >= 60: return "background:#FF6B2D33;color:#FFA07A"
                if f >= 40: return "background:#FFA50033;color:#FFC04D"
                if f >= 20: return "background:#00CFFF22;color:#7EC8FF"
                return ""
            except: return ""
        def _s_atrasado(v):
            return "color:#FF6B6B;font-weight:bold" if v == "⚠️ Sim" else ""

        styled = df_exibir.style
        if "Criticidade" in df_exibir.columns:
            styled = styled.map(_s_crit, subset=["Criticidade"])
        if "Score (%)" in df_exibir.columns:
            styled = styled.map(_s_score, subset=["Score (%)"])
        if "Atrasado" in df_exibir.columns:
            styled = styled.map(_s_atrasado, subset=["Atrasado"])

        st.dataframe(styled, use_container_width=True, hide_index=True)

        # ── Exportar Excel formatado ──
        def _gerar_excel_rota(df: pd.DataFrame) -> bytes:
            wb = Workbook()
            ws = wb.active
            ws.title = "Rota de Inspeção"

            # Paleta
            COR_HEADER_BG  = "1A1F2E"
            COR_HEADER_FG  = "00CFFF"
            COR_TITULO_BG  = "0D1117"
            COR_LINHA_PAR  = "13161D"
            COR_LINHA_IMPAR= "0D0F14"
            COR_BORDA      = "1E2330"

            CRIT_BG = {1:"FF2D2D33",2:"FF6B2D33",3:"FFA50033",4:"FFD70033",5:"90EE9033",6:"4CAF5033"}
            CRIT_FG = {1:"FF6B6B",  2:"FFA07A",  3:"FFC04D",  4:"FFE680",  5:"B8F0B8",  6:"81C784"}

            def score_fg(v):
                try:
                    f = float(str(v).replace("%",""))
                    if f >= 80: return "FF6B6B"
                    if f >= 60: return "FFA07A"
                    if f >= 40: return "FFC04D"
                    if f >= 20: return "7EC8FF"
                    return "E8EAF0"
                except: return "E8EAF0"

            thin = Side(style="thin", color=COR_BORDA)
            borda = Border(left=thin, right=thin, top=thin, bottom=thin)

            # Título
            ws.merge_cells("A1:L1")
            tc = ws["A1"]
            tc.value = f"Rota de Inspeção — {len(df)} torres | Distância total: {resumo.get('distancia_total','?')} km"
            tc.font = Font(name="Arial", bold=True, size=14, color=COR_HEADER_FG)
            tc.fill = PatternFill("solid", fgColor=COR_TITULO_BG)
            tc.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[1].height = 30

            # Subtítulo / data
            ws.merge_cells("A2:L2")
            sc = ws["A2"]
            sc.value = f"Gerado em: {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M')}"
            sc.font = Font(name="Arial", size=9, color="7A8099")
            sc.fill = PatternFill("solid", fgColor=COR_TITULO_BG)
            sc.alignment = Alignment(horizontal="right", vertical="center")
            ws.row_dimensions[2].height = 16

            # Cabeçalho
            headers = list(df.columns)
            for ci, h in enumerate(headers, start=1):
                cell = ws.cell(row=3, column=ci, value=h)
                cell.font = Font(name="Arial", bold=True, size=10, color=COR_HEADER_FG)
                cell.fill = PatternFill("solid", fgColor=COR_HEADER_BG)
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = borda
            ws.row_dimensions[3].height = 28

            # Dados
            for ri, row_data in enumerate(df.itertuples(index=False), start=4):
                bg = COR_LINHA_PAR if ri % 2 == 0 else COR_LINHA_IMPAR
                for ci, val in enumerate(row_data, start=1):
                    col_name = headers[ci - 1]
                    cell = ws.cell(row=ri, column=ci, value=val)
                    cell.font = Font(name="Arial", size=10, color="E8EAF0")
                    cell.fill = PatternFill("solid", fgColor=bg)
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.border = borda

                    if col_name == "Criticidade":
                        try:
                            v = int(float(str(val)))
                            cell.fill = PatternFill("solid", fgColor=CRIT_BG.get(v, bg)[:-2] or bg)
                            cell.font = Font(name="Arial", bold=True, size=10, color=CRIT_FG.get(v, "E8EAF0"))
                        except: pass

                    elif col_name == "Score (%)":
                        cell.font = Font(name="Arial", bold=True, size=10, color=score_fg(val))

                    elif col_name == "Atrasado" and str(val) not in ("—", "0", ""):
                        cell.font = Font(name="Arial", bold=True, size=10, color="FF6B6B")

                ws.row_dimensions[ri].height = 20

            # Larguras de coluna
            col_widths = {
                "Ordem":14,"Ativo":14,"Torre":10,"Empresa":12,"Instalação":18,
                "Criticidade":14,"Qtd SS":10,"Pior saldo (dias)":18,
                "Atrasado":12,"Score (%)":12,"Dist. próx (km)":16,"Dist. acum (km)":16,
            }
            for ci, h in enumerate(headers, start=1):
                ws.column_dimensions[get_column_letter(ci)].width = col_widths.get(h, 14)

            # Congelar painel após cabeçalho
            ws.freeze_panes = "A4"

            buf = io.BytesIO()
            wb.save(buf)
            return buf.getvalue()

        xlsx_rota = _gerar_excel_rota(df_exibir)
        st.download_button(
            "📥 Exportar rota Excel",
            xlsx_rota,
            "rota_inspecao.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("Gere a rota para ver o detalhamento aqui.")

with tab_clima:
    if weather_map:
        dados_clima = []
        for cod, info in weather_map.items():
            if not isinstance(info, dict): continue
            dados_clima.append({
                "Torre (COD_ATIVO)": cod,
                "Condição":    info.get("descricao", "N/D") if info.get("ok") else f"Erro: {info.get('erro','')}",
                "Temp (°C)":   round(info["temperatura"], 1) if info.get("ok") else "-",
                "Umidade (%)": info.get("umidade", "-") if info.get("ok") else "-",
                "Vento (km/h)": info.get("vento_kmh", "-") if info.get("ok") else "-",
                "Chuva (mm/h)": info.get("chuva_mm", "-") if info.get("ok") else "-",
                "_risco_bool": bool(info.get("risco", False)),
                "Status":      "RISCO" if info.get("risco", False) else "OK",
            })
        if not dados_clima:
            st.info("Nenhum dado climático disponível.")
        else:
            df_clima = pd.DataFrame(dados_clima)
            n_risco = int(df_clima["_risco_bool"].sum())
            if n_risco:
                _modo = st.session_state["modo_conservador"]
                st.markdown(
                    f'<div class="clima-alert">⛔ <b>{n_risco} torres</b> com condição climática adversa '
                    f'{"foram removidas da rota" if _modo else "estão na rota (modo não conservador)"}.</div>',
                    unsafe_allow_html=True,
                )
            df_clima_exibir = df_clima.drop(columns=["_risco_bool"]).copy()
            df_clima_exibir["Status"] = df_clima_exibir["Status"].replace({"RISCO": "⛔ RISCO", "OK": "✅ OK"})
            st.dataframe(df_clima_exibir, use_container_width=True, hide_index=True)
    else:
        st.info("Gere a rota para ver as condições climáticas.")

with tab_ocorrencias:
    if df_rota is not None:
        try:
            from services.database import load_ocorrencias

            # ── Pré-carrega ocorrências de todas as torres da rota ──
            if "df_ocorrencias_cache" not in st.session_state or st.session_state.get("_oc_rota_hash") != id(df_rota):
                with st.spinner("🔄 Verificando ocorrências das torres..."):
                    _oc_parts = []
                    for _cod in df_rota["COD_ATIVO"].tolist():
                        try:
                            _df_tmp = load_ocorrencias(_cod)
                            if not _df_tmp.empty:
                                _oc_parts.append(_df_tmp)
                        except Exception:
                            pass
                    st.session_state["df_ocorrencias_cache"] = pd.concat(_oc_parts, ignore_index=True) if _oc_parts else pd.DataFrame()
                    st.session_state["_oc_rota_hash"] = id(df_rota)

            df_oc_all = st.session_state["df_ocorrencias_cache"]

            # Torres que realmente têm ocorrências
            if df_oc_all.empty:
                st.info("Nenhuma ocorrência pendente para as torres desta rota.")
            else:
                torres_com_oc = df_oc_all["COD_ATIVO"].unique().tolist() if "COD_ATIVO" in df_oc_all.columns else []

                def _label_torre(c):
                    if c == "__todas__":
                        return f"📋 Todas as torres ({len(torres_com_oc)} com ocorrências)"
                    num = df_rota.loc[df_rota["COD_ATIVO"] == c, "NUM_TORRE"].values
                    return f"{c} — Torre {num[0]}" if len(num) else c

                opcoes_sel = ["__todas__"] + torres_com_oc
                torre_sel = st.selectbox(
                    f"Selecione uma torre para ver ocorrências ({len(torres_com_oc)} torres com pendências)",
                    options=opcoes_sel,
                    format_func=_label_torre,
                )

                # Filtra df conforme seleção
                if torre_sel == "__todas__":
                    df_oc = df_oc_all.copy()
                    label_export = "todas_torres"
                else:
                    df_oc = df_oc_all[df_oc_all["COD_ATIVO"] == torre_sel].copy()
                    label_export = torre_sel

                # Renomeia para exibição amigável
                rename_oc = {
                    "COD_SS":           "Cód. SS",
                    "COD_ATIVO":        "Ativo",
                    "NOME_PRIORIDADE":  "Prioridade",
                    "NIVEL_CRITICIDADE":"Criticidade",
                    "DIAS_EM_ABERTO":   "Dias em aberto",
                    "PRAZO_DIAS":       "Prazo (dias)",
                    "SALDO_DIAS":       "Saldo (dias)",
                    "STATUS_PRAZO":     "Status do prazo",
                    "TEXT_OBSERVACAO":  "Observação / Causa",
                }
                cols_disp = [k for k in rename_oc if k in df_oc.columns]
                df_oc_exibir = df_oc[cols_disp].rename(columns=rename_oc)

                st.dataframe(
                    df_oc_exibir.style.map(
                        lambda v: "color: #FF6B6B; font-weight:bold"
                        if isinstance(v, str) and "Atrasado" in v else "",
                        subset=["Status do prazo"] if "Status do prazo" in df_oc_exibir.columns else [],
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                def _gerar_excel_ocorrencias(df: pd.DataFrame, cod_ativo: str) -> bytes:
                        wb = Workbook()
                        ws = wb.active
                        ws.title = "Ocorrências"

                        COR_HEADER_BG = "1A1F2E"
                        COR_HEADER_FG = "00CFFF"
                        COR_TITULO_BG = "0D1117"
                        COR_LINHA_PAR  = "13161D"
                        COR_LINHA_IMPAR= "0D0F14"
                        COR_BORDA      = "1E2330"

                        thin = Side(style="thin", color=COR_BORDA)
                        borda = Border(left=thin, right=thin, top=thin, bottom=thin)
                        n_cols = len(df.columns)
                        last_col = get_column_letter(n_cols)

                        ws.merge_cells(f"A1:{last_col}1")
                        tc = ws["A1"]
                        num_torre = ""
                        if df_rota is not None and "NUM_TORRE" in df_rota.columns:
                            matches = df_rota.loc[df_rota["COD_ATIVO"] == cod_ativo, "NUM_TORRE"].values
                            if len(matches): num_torre = f" — Torre {matches[0]}"
                        tc.value = f"Ocorrências: {cod_ativo}{num_torre}"
                        tc.font = Font(name="Arial", bold=True, size=14, color=COR_HEADER_FG)
                        tc.fill = PatternFill("solid", fgColor=COR_TITULO_BG)
                        tc.alignment = Alignment(horizontal="center", vertical="center")
                        ws.row_dimensions[1].height = 30

                        ws.merge_cells(f"A2:{last_col}2")
                        sc = ws["A2"]
                        sc.value = f"Gerado em: {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M')}"
                        sc.font = Font(name="Arial", size=9, color="7A8099")
                        sc.fill = PatternFill("solid", fgColor=COR_TITULO_BG)
                        sc.alignment = Alignment(horizontal="right", vertical="center")
                        ws.row_dimensions[2].height = 16

                        headers = list(df.columns)
                        for ci, h in enumerate(headers, start=1):
                            cell = ws.cell(row=3, column=ci, value=h)
                            cell.font = Font(name="Arial", bold=True, size=10, color=COR_HEADER_FG)
                            cell.fill = PatternFill("solid", fgColor=COR_HEADER_BG)
                            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                            cell.border = borda
                        ws.row_dimensions[3].height = 28

                        for ri, row_data in enumerate(df.itertuples(index=False), start=4):
                            bg = COR_LINHA_PAR if ri % 2 == 0 else COR_LINHA_IMPAR
                            for ci, val in enumerate(row_data, start=1):
                                col_name = headers[ci - 1]
                                cell = ws.cell(row=ri, column=ci, value=val)
                                cell.fill = PatternFill("solid", fgColor=bg)
                                align_left = col_name in ("Prioridade", "Observação / Causa", "Status do prazo")
                                cell.alignment = Alignment(horizontal="left" if align_left else "center",
                                                           vertical="center", wrap_text=True)
                                cell.border = borda
                                is_atrasado = col_name == "Status do prazo" and isinstance(val, str) and "Atrasado" in val
                                cell.font = Font(
                                    name="Arial", size=10,
                                    color="FF6B6B" if is_atrasado else "E8EAF0",
                                    bold=is_atrasado,
                                )
                            ws.row_dimensions[ri].height = 22

                        col_widths = {
                            "Cód. SS": 10, "Ativo": 12, "Prioridade": 22, "Criticidade": 12,
                            "Dias em aberto": 16, "Prazo (dias)": 14, "Saldo (dias)": 14,
                            "Status do prazo": 18, "Observação / Causa": 42,
                        }
                        for ci, h in enumerate(headers, start=1):
                            ws.column_dimensions[get_column_letter(ci)].width = col_widths.get(h, 16)

                        ws.freeze_panes = "A4"
                        buf = io.BytesIO()
                        wb.save(buf)
                        return buf.getvalue()

                xlsx_oc = _gerar_excel_ocorrencias(df_oc_exibir, label_export)
                st.download_button(
                    "📥 Exportar ocorrências Excel",
                    xlsx_oc,
                    f"ocorrencias_{label_export}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_oc_{label_export}",
                )
        except Exception as e:
            err = str(e)
            if "18456" in err or "authentication" in err.lower() or "login" in err.lower():
                st.error(
                    "❌ **Erro de autenticação ao carregar ocorrências.**\n\n"
                    "Seu token de sessão pode ter expirado. Clique em **Sair** na sidebar e faça login novamente."
                )
            else:
                st.error(f"Erro ao carregar ocorrências: {e}")
    else:
        st.info("Gere a rota para consultar ocorrências por torre.")
