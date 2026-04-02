"""
ui/components/mapa.py
Mapa Folium com marcadores por STATUS_PRAZO e linha de rota.

Cores:
  🔴 ATRASADA    → vermelho
  🟡 ALTA        → amarelo (próxima do vencimento)
  🟢 NORMAL      → verde
"""

from __future__ import annotations

import folium
import pandas as pd
from folium.plugins import MarkerCluster

# Valores de Prioridade (espelha domain/models.py — MAXIMA=1, ALTA=2, NORMAL=3)
_PRIORIDADE_MAXIMA = 1
_PRIORIDADE_ALTA   = 2
_PRIORIDADE_NORMAL = 3

# Paleta por PRIORIDADE (valor int)
_CORES_PRIORIDADE = {
    _PRIORIDADE_MAXIMA: "#FF2D2D",   # 🔴 Atrasada
    _PRIORIDADE_ALTA:   "#FFD700",   # 🟡 Alta (vence em breve)
    _PRIORIDADE_NORMAL: "#4CAF50",   # 🟢 Normal
}

_LABELS_PRIORIDADE = {
    _PRIORIDADE_MAXIMA: "🔴 ATRASADA",
    _PRIORIDADE_ALTA:   "🟡 VENCE EM BREVE",
    _PRIORIDADE_NORMAL: "🟢 NO PRAZO",
}

def _torre_svg(cor: str, label: str = "", size: int = 28, ss_aberta: bool = False) -> str:
    """
    Retorna HTML com SVG de torre de transmissão colorida por prioridade.
    label    : número de ordem para torres da rota (vazio para torres de fundo).
    ss_aberta: se True, adiciona anel laranja pulsante ao redor da torre.
    """
    s = size
    # Anel de destaque para torres com SS N1/N2 em aberto
    ring_html = ""
    if ss_aberta:
        ring_size = s + 14
        ring_html = f"""<div style='position:absolute;top:50%;left:50%;
            transform:translate(-50%,-55%);
            width:{ring_size}px;height:{ring_size}px;
            border-radius:50%;
            border:3px solid #FF8C00;
            box-shadow:0 0 8px 3px #FF8C0088;
            animation:ss-pulse 1.6s ease-in-out infinite;
            pointer-events:none;z-index:0'></div>
        <style>
        @keyframes ss-pulse {{
            0%,100% {{ opacity:1; box-shadow:0 0 8px 3px #FF8C0088; }}
            50%      {{ opacity:0.6; box-shadow:0 0 14px 6px #FF8C00BB; }}
        }}
        </style>"""

    svg = f"""<div style='position:relative;width:{s}px;height:{s}px;'>
{ring_html}
<svg xmlns='http://www.w3.org/2000/svg' width='{s}' height='{s}' viewBox='0 0 28 32' style='position:relative;z-index:1'>
  <g stroke='{cor}' stroke-width='1.4' fill='none' stroke-linecap='round' stroke-linejoin='round'>
    <!-- pés -->
    <line x1='4' y1='30' x2='9' y2='22'/>
    <line x1='24' y1='30' x2='19' y2='22'/>
    <line x1='4' y1='30' x2='24' y2='30'/>
    <!-- base -->
    <line x1='9' y1='22' x2='19' y2='22'/>
    <!-- corpo com X -->
    <line x1='9' y1='22' x2='11' y2='14'/>
    <line x1='19' y1='22' x2='17' y2='14'/>
    <line x1='9' y1='22' x2='17' y2='14'/>
    <line x1='19' y1='22' x2='11' y2='14'/>
    <!-- cintura -->
    <line x1='11' y1='14' x2='17' y2='14'/>
    <!-- topo com X -->
    <line x1='11' y1='14' x2='12.5' y2='8'/>
    <line x1='17' y1='14' x2='15.5' y2='8'/>
    <line x1='11' y1='14' x2='15.5' y2='8'/>
    <line x1='17' y1='14' x2='12.5' y2='8'/>
    <line x1='12.5' y1='8' x2='15.5' y2='8'/>
    <!-- mastro -->
    <line x1='14' y1='8' x2='14' y2='2'/>
    <!-- braços -->
    <line x1='4' y1='10' x2='24' y2='10'/>
    <line x1='7' y1='13' x2='21' y2='13'/>
    <!-- isoladores -->
    <circle cx='4' cy='10' r='1.2' fill='{cor}'/>
    <circle cx='24' cy='10' r='1.2' fill='{cor}'/>
    <circle cx='7' cy='13' r='1.2' fill='{cor}'/>
    <circle cx='21' cy='13' r='1.2' fill='{cor}'/>
    <circle cx='14' cy='2' r='1.2' fill='{cor}'/>
    <!-- fios -->
    <path d='M4 10 Q14 13 24 10' stroke='{cor}' stroke-width='0.7'/>
    <path d='M7 13 Q14 16 21 13' stroke='{cor}' stroke-width='0.7'/>
  </g>
  {"" if not label else f"<circle cx='14' cy='16' r='6' fill='{cor}' opacity='0.9'/><text x='14' y='20' font-size='6' font-family='sans-serif' font-weight='bold' fill='white' text-anchor='middle'>{label}</text>"}
</svg></div>"""
    return svg




def _cor(row: pd.Series) -> str:
    try:
        p = int(row.get("PRIORIDADE", _PRIORIDADE_NORMAL))
        return _CORES_PRIORIDADE.get(p, "#999999")
    except Exception:
        return "#999999"


def _safe(val, fallback="–") -> str:
    try:
        if pd.isna(val):
            return str(fallback)
    except Exception:
        pass
    return str(val) if val is not None else str(fallback)


def _popup_html(row: pd.Series, clima: dict | None, ss_lista: list | None = None) -> str:
    cor         = _cor(row)
    prioridade  = _LABELS_PRIORIDADE.get(int(row.get("PRIORIDADE", 3)), "—")
    dias_atraso = int(row.get("DIAS_ATRASO", 0))

    atraso_html = (
        f"<b>⏳ Dias de atraso:</b> <span style='color:#FF6B6B;font-weight:bold'>{dias_atraso}</span><br>"
        if dias_atraso > 0 else ""
    )

    data_limite = _safe(row.get("DATA_LIMITE"))
    try:
        data_limite = pd.to_datetime(row.get("DATA_LIMITE")).strftime("%d/%m/%Y")
    except Exception:
        pass

    clima_html = ""
    if clima and clima.get("ok"):
        badge = "⛔ RISCO" if clima["risco"] else "✅ OK"
        clima_html = f"""
        <hr style='margin:6px 0'>
        <b>🌦️ Clima:</b> {clima['descricao']}<br>
        <b>🌡️</b> {clima['temperatura']}°C &nbsp;
        <b>💧</b> {clima['umidade']}% &nbsp;
        <b>💨</b> {clima['vento_kmh']} km/h<br>
        <b>Status:</b> {badge}
        """

    # ── Bloco SS (contexto operacional — não afeta prioridade) ──
    ss_html = ""
    if ss_lista:
        cor_nivel = {1: "#FF2D2D", 2: "#FFD700"}
        itens = ""
        for ss in ss_lista:
            nivel = ss.get("NIVEL_SS", "?")
            cor_n = cor_nivel.get(int(nivel) if str(nivel).isdigit() else 0, "#999")
            tipo  = _safe(ss.get("TIPO_DEFEITO", "—"))
            desc  = _safe(ss.get("DESC_SS", "—"))
            status = _safe(ss.get("STATUS_SS", "—"))
            data_ab = ""
            try:
                data_ab = pd.to_datetime(ss.get("DATA_ABERTURA")).strftime("%d/%m/%Y")
            except Exception:
                pass
            itens += f"""
            <div style='border-left:3px solid {cor_n};padding:4px 6px;margin:3px 0;
                        background:rgba(0,0,0,0.04);border-radius:0 4px 4px 0;font-size:12px'>
                <span style='background:{cor_n};color:white;padding:1px 5px;
                             border-radius:3px;font-size:10px;font-weight:bold'>N{nivel}</span>
                &nbsp;<b>{tipo}</b><br>
                <span style='color:#555'>{desc}</span><br>
                <span style='color:#888;font-size:11px'>Status: {status} · {data_ab}</span>
            </div>"""
        ss_html = f"""
        <hr style='margin:6px 0'>
        <b>⚠️ SS vinculadas ({len(ss_lista)})</b>
        <div style='max-height:120px;overflow-y:auto;margin-top:4px'>{itens}</div>
        """

    return f"""
    <div style='font-family:sans-serif;font-size:13px;min-width:240px;max-width:320px'>
        <div style='background:{cor};color:white;padding:6px 10px;border-radius:4px;
                    font-weight:bold;font-size:13px;margin-bottom:8px'>
            {prioridade}
        </div>
        <b>OS:</b> {_safe(row.get('DESC_NUMERO_OS'))} &nbsp;|&nbsp; <b>Ativo:</b> {_safe(row.get('COD_ATIVO'))}<br>
        <b>Torre:</b> {_safe(row.get('NUM_TORRE'))} &nbsp;|&nbsp; <b>Criticidade:</b> {_safe(row.get('CRITICIDADE'))}<br>
        <b>Empresa:</b> {_safe(row.get('SIGLA_EMPRESA', row.get('EMPRESA')))}<br>
        <b>Instalação:</b> {_safe(row.get('INSTALACAO'))}<br>
        <b>Data Limite:</b> {data_limite}<br>
        {atraso_html}
        <b>Estado:</b> {_safe(row.get('DESC_ESTADO'))}
        {clima_html}
        {ss_html}
    </div>
    """


def build_map(
    df: pd.DataFrame,
    df_rota: pd.DataFrame | None = None,
    weather_map: dict | None     = None,
    ss_map: dict | None          = None,
    ss_abertas_set: set | None   = None,
    usar_cluster: bool           = False,
) -> folium.Map:
    """
    df             : dataset consolidado (JOIN VIEW_PLANO + VW_TORRES via COD_ATIVO)
    df_rota        : OS na sequência de rota otimizada
    weather_map    : { COD_ATIVO: dict_clima }
    ss_map         : { COD_ATIVO: [lista de dicts SS] }  — contexto operacional
    ss_abertas_set : set de COD_ATIVO com SS N1/N2 em aberto — recebem destaque no mapa
    """
    ss_abertas_set = ss_abertas_set or set()
    df_valido = df.dropna(subset=["LATITUDE", "LONGITUDE"]) if not df.empty else df

    if df_valido.empty:
        mapa = folium.Map(location=[-15.8, -47.9], zoom_start=5, tiles=None)
        _add_tiles(mapa)
        return mapa

    center_lat = df_valido["LATITUDE"].mean()
    center_lon = df_valido["LONGITUDE"].mean()

    mapa = folium.Map(location=[center_lat, center_lon], zoom_start=8, tiles=None, control_scale=True)
    _add_tiles(mapa)
    _add_dark_mode_fix(mapa)

    # ── Todas as OS (background) ──
    layer_todas = folium.FeatureGroup(name="Todas as OS", show=True)
    container   = MarkerCluster() if usar_cluster else layer_todas

    for _, row in df_valido.iterrows():
        cor       = _cor(row)
        clima     = (weather_map or {}).get(row["COD_ATIVO"])
        tem_ss    = row["COD_ATIVO"] in ss_abertas_set

        icon_html = _torre_svg(cor, label="", size=28, ss_aberta=tem_ss)
        ss_lista  = (ss_map or {}).get(row["COD_ATIVO"], [])
        _n_ss     = len(ss_lista)
        _tooltip_ss = f" | ⚠️ {_n_ss} SS" if _n_ss else ""
        _tooltip_ss += " 🔶 SS ABERTA" if tem_ss else ""
        folium.Marker(
            location=[row["LATITUDE"], row["LONGITUDE"]],
            popup=folium.Popup(_popup_html(row, clima, ss_lista), max_width=340),
            tooltip=f"OS {_safe(row.get('DESC_NUMERO_OS'))} — {_safe(row.get('COD_ATIVO'))}{_tooltip_ss}",
            icon=folium.DivIcon(html=icon_html, icon_size=(42, 46), icon_anchor=(14, 30)),
        ).add_to(container if usar_cluster else layer_todas)

    if usar_cluster:
        container.add_to(layer_todas)
    layer_todas.add_to(mapa)

    # ── Rota otimizada ──
    if df_rota is not None and not df_rota.empty:
        df_rota_v = df_rota.dropna(subset=["LATITUDE", "LONGITUDE"])
        if not df_rota_v.empty:
            layer_rota   = folium.FeatureGroup(name="🗺️ Rota otimizada", show=True)
            coords_rota  = df_rota_v[["LATITUDE", "LONGITUDE"]].values.tolist()

            folium.PolyLine(
                coords_rota, color="#00CFFF", weight=2.5, opacity=0.85, dash_array="6 4"
            ).add_to(layer_rota)

            for _, row in df_rota_v.iterrows():
                ordem    = _safe(row.get("ORDEM_VISITA", "?"))
                cor      = _cor(row)
                clima    = (weather_map or {}).get(row["COD_ATIVO"])
                tem_ss_r = row["COD_ATIVO"] in ss_abertas_set

                icon_html  = _torre_svg(cor, label=str(ordem), size=36, ss_aberta=tem_ss_r)
                ss_lista_r = (ss_map or {}).get(row["COD_ATIVO"], [])
                _n_ss_r    = len(ss_lista_r)
                _tt_ss_r   = f" | ⚠️ {_n_ss_r} SS" if _n_ss_r else ""
                _tt_ss_r  += " 🔶 SS ABERTA" if tem_ss_r else ""
                folium.Marker(
                    location=[row["LATITUDE"], row["LONGITUDE"]],
                    popup=folium.Popup(_popup_html(row, clima, ss_lista_r), max_width=340),
                    tooltip=f"#{ordem} — {_safe(row.get('DESC_NUMERO_OS'))}{_tt_ss_r}",
                    icon=folium.DivIcon(html=icon_html, icon_size=(50, 54), icon_anchor=(18, 38)),
                ).add_to(layer_rota)

            layer_rota.add_to(mapa)

    # ── Legenda ──
    legenda_html = """
    <div style='position:fixed;bottom:30px;right:10px;z-index:1000;
                background:rgba(20,20,30,0.92);color:white;
                padding:10px 14px;border-radius:8px;font-size:12px;
                border:1px solid rgba(255,255,255,0.15);font-family:sans-serif;line-height:1.9;'>
        <b>Prioridade</b><br>
        <span style='color:#FF2D2D'>●</span> Atrasada<br>
        <span style='color:#FFD700'>●</span> Vence em breve<br>
        <span style='color:#4CAF50'>●</span> No prazo<br>
        <hr style='border-color:rgba(255,255,255,0.2);margin:4px 0'>
        <span style='color:#00CFFF'>- -</span> Rota otimizada<br>
        <hr style='border-color:rgba(255,255,255,0.2);margin:4px 0'>
        <span style='color:#FFD700'>⚠️</span> SS vinculadas (tooltip)<br>
        <hr style='border-color:rgba(255,255,255,0.2);margin:4px 0'>
        <span style='display:inline-block;width:14px;height:14px;border-radius:50%;
                     border:2px solid #FF8C00;box-shadow:0 0 6px #FF8C00;
                     vertical-align:middle;margin-right:4px'></span>
        <b style='color:#FF8C00'>SS N1/N2 em aberto</b>
    </div>
    """
    mapa.get_root().html.add_child(folium.Element(legenda_html))
    folium.LayerControl(collapsed=False).add_to(mapa)

    return mapa


def _add_tiles(mapa: folium.Map) -> None:
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="🛰️ Satélite", overlay=False, control=True,
    ).add_to(mapa)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="🏷️ Rótulos", overlay=True, control=True, show=True,
    ).add_to(mapa)
    folium.TileLayer(
        tiles="CartoDB dark_matter", name="🌑 Mapa escuro", overlay=False, control=True,
    ).add_to(mapa)


def _add_dark_mode_fix(mapa: folium.Map) -> None:
    mapa.get_root().header.add_child(folium.Element("""
<style>
    :root { color-scheme: light only !important; }
    html, body { color-scheme: light only !important; background: transparent !important; }
    .leaflet-container { color-scheme: light only !important; }
    .leaflet-tile-pane img { filter: none !important; opacity: 1 !important; }
</style>
"""))
