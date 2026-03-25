"""
app.py — App principal de roteirização de inspeção de linhas de transmissão.
"""

import time
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from services.database import (
    iniciar_device_flow, concluir_login, is_authenticated,
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
]:
    if key not in st.session_state:
        st.session_state[key] = default


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
                  "_device_flow", "_msal_app"]:
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
        CORES_NIVEL = {
            1: "background-color:#FF2D2D33;color:#FF6B6B;font-weight:bold",
            2: "background-color:#FF6B2D33;color:#FFA07A;font-weight:bold",
            3: "background-color:#FFA50033;color:#FFC04D",
            4: "background-color:#FFD70033;color:#FFE680",
            5: "background-color:#90EE9033;color:#B8F0B8",
            6: "background-color:#4CAF5033;color:#81C784",
        }
        def _style_criticidade(val):
            try:
                if val in ("–", "", None): return ""
                return CORES_NIVEL.get(int(float(str(val))), "")
            except: return ""
        def _style_atrasado(val):
            try:
                if val in ("–", "", None): return ""
                return "color:#FF6B6B;font-weight:bold" if int(float(str(val))) == 1 else "color:#81C784"
            except: return ""
        def _style_score(val):
            try:
                if val in ("–", "", None): return ""
                v = float(str(val))
                if v >= 80: return "background-color:#00CFFF33;color:#00CFFF;font-weight:bold"
                if v >= 50: return "background-color:#0080FF22;color:#7EC8FF"
                return ""
            except: return ""

        df_display = df_rota[colunas_disponiveis].copy()

        # ── Formata colunas numéricas — sem casas decimais desnecessárias ──
        colunas_inteiras = ["ORDEM_VISITA", "NUM_TORRE", "CRITICIDADE_MIN",
                            "QTD_SS", "PIOR_SALDO_DIAS", "FL_ATRASADO"]
        colunas_float1   = ["DIST_PROX_KM", "DIST_ACUM_KM"]
        colunas_float2   = ["SCORE"]

        for col in colunas_inteiras:
            if col in df_display.columns:
                df_display[col] = df_display[col].apply(
                    lambda v: str(int(float(v))) if str(v) not in ("None", "nan", "") and v is not None else "–"
                )
        for col in colunas_float1:
            if col in df_display.columns:
                df_display[col] = df_display[col].apply(
                    lambda v: f"{float(v):.1f}" if str(v) not in ("None", "nan", "") and v is not None else "–"
                )
        for col in colunas_float2:
            if col in df_display.columns:
                df_display[col] = df_display[col].apply(
                    lambda v: f"{float(v):.1f}" if str(v) not in ("None", "nan", "") and v is not None else "–"
                )

        # Renomeia colunas para nomes amigáveis
        rename_rota = {
            "ORDEM_VISITA":   "Ordem",
            "COD_ATIVO":      "Ativo",
            "NUM_TORRE":      "Torre",
            "EMPRESA":        "Empresa",
            "INSTALACAO":     "Instalação",
            "CRITICIDADE_MIN":"Criticidade",
            "QTD_SS":         "Qtd SS",
            "PIOR_SALDO_DIAS":"Pior saldo (dias)",
            "FL_ATRASADO":    "Atrasado",
            "SCORE":          "Score",
            "DIST_PROX_KM":   "Dist. próx (km)",
            "DIST_ACUM_KM":   "Dist. acum (km)",
        }
        df_display = df_display.rename(columns={k: v for k, v in rename_rota.items() if k in df_display.columns})

        # Reconstrói subset com nomes já renomeados
        col_crit    = rename_rota.get("CRITICIDADE_MIN", "Criticidade")
        col_atras   = rename_rota.get("FL_ATRASADO", "Atrasado")
        col_score   = rename_rota.get("SCORE", "Score")

        styled = df_display.style
        if col_crit in df_display.columns:
            styled = styled.map(_style_criticidade, subset=[col_crit])
        if col_atras in df_display.columns:
            styled = styled.map(_style_atrasado, subset=[col_atras])
        if col_score in df_display.columns:
            styled = styled.map(_style_score, subset=[col_score])

        st.dataframe(styled, use_container_width=True, hide_index=True)
        csv = df_rota[colunas_disponiveis].to_csv(index=False).encode("utf-8")
        st.download_button("📥 Exportar rota CSV", csv, "rota_inspecao.csv", "text/csv")
    else:
        st.info("Gere a rota para ver o detalhamento aqui.")

with tab_clima:
    if weather_map:
        # Verifica se há erro de API key em qualquer entrada
        erros_api = [
            info.get("erro", "") for info in weather_map.values()
            if isinstance(info, dict) and not info.get("ok")
        ]
        if erros_api:
            primeiro_erro = erros_api[0]
            if "API_KEY" in primeiro_erro or "api key" in primeiro_erro.lower() or "401" in primeiro_erro:
                st.error(
                    "🔑 **Chave da API OpenWeather não configurada ou inválida.**\n\n"
                    "**No Streamlit Cloud:** acesse *Settings → Secrets* e adicione:\n"
                    "```\nOPENWEATHER_API_KEY = \"sua_chave_aqui\"\n```\n"
                    "**Localmente:** adicione no arquivo `.env`:\n"
                    "```\nOPENWEATHER_API_KEY=sua_chave_aqui\n```"
                )
            else:
                st.warning(f"⚠️ Erro ao consultar clima: {primeiro_erro}")

        dados_clima = []
        for cod, info in weather_map.items():
            if not isinstance(info, dict):
                continue
            dados_clima.append({
                "Torre (COD_ATIVO)": cod,
                "Condição":    info.get("descricao", "N/D") if info.get("ok") else f"❌ {info.get('erro', 'Erro')}",
                "Temp (°C)":   info.get("temperatura", "-") if info.get("ok") else "-",
                "Umidade (%)": info.get("umidade", "-") if info.get("ok") else "-",
                "Vento (km/h)": info.get("vento_kmh", "-") if info.get("ok") else "-",
                "Chuva (mm/h)": info.get("chuva_mm", "-") if info.get("ok") else "-",
                "risco_bool":  bool(info.get("risco", False)),
                "Status":      "RISCO" if info.get("risco", False) else "OK",
            })

        if not dados_clima:
            st.info("Nenhum dado climático disponível para as torres selecionadas.")
        else:
            df_clima = pd.DataFrame(dados_clima)
            n_risco = int(df_clima["risco_bool"].sum())
            if n_risco:
                _modo = st.session_state["modo_conservador"]
                st.markdown(
                    f'<div class="clima-alert">⛔ <b>{n_risco} torres</b> com condição climática adversa '
                    f'{"foram removidas da rota" if _modo else "estão na rota (modo não conservador)"}.</div>',
                    unsafe_allow_html=True,
                )
            df_exibir = df_clima.drop(columns=["risco_bool"]).copy()
            df_exibir["Status"] = df_exibir["Status"].replace({"RISCO": "⛔ RISCO", "OK": "✅ OK"})
            st.dataframe(df_exibir, use_container_width=True, hide_index=True)
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
                    # Renomeia colunas para exibição amigável
                    rename_map = {
                        "COD_SS":           "Cód. SS",
                        "COD_ATIVO":        "Ativo",
                        "NOME_PRIORIDADE":  "Prioridade",
                        "NIVEL_CRITICIDADE":"Criticidade",
                        "DIAS_EM_ABERTO":   "Dias em aberto",
                        "PRAZO_DIAS":       "Prazo (dias)",
                        "SALDO_DIAS":       "Saldo (dias)",
                        "STATUS_PRAZO":     "Status do prazo",
                        "DESCRICAO_SS":     "Descrição",
                        "DESCRICAO":        "Descrição",
                        "DESC_SS":          "Descrição",
                        "OBSERVACAO":       "Observação",
                        "DETALHE":          "Detalhe",
                        "TEXTO_SS":         "Texto SS",
                    }
                    df_exibir_oc = df_oc.rename(columns={
                        k: v for k, v in rename_map.items() if k in df_oc.columns
                    })
                    st.dataframe(df_exibir_oc, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Erro ao carregar ocorrências: {e}")
    else:
        st.info("Gere a rota para consultar ocorrências por torre.")
