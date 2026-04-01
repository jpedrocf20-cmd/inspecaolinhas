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


def _popup_html(row: pd.Series, clima: dict | None) -> str:
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

    return f"""
    <div style='font-family:sans-serif;font-size:13px;min-width:220px'>
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
    </div>
    """


def build_map(
    df: pd.DataFrame,
    df_rota: pd.DataFrame | None = None,
    weather_map: dict | None     = None,
    usar_cluster: bool           = False,
) -> folium.Map:
    """
    df        : dataset consolidado (JOIN VIEW_PLANO + VW_TORRES via COD_ATIVO)
    df_rota   : OS na sequência de rota otimizada
    weather_map: { COD_ATIVO: dict_clima }
    """
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
        cor   = _cor(row)
        clima = (weather_map or {}).get(row["COD_ATIVO"])

        icon_html = f"""
        <div style='background:{cor};border:2px solid white;border-radius:50%;
                    width:12px;height:12px;box-shadow:0 0 4px rgba(0,0,0,0.6);'></div>
        """
        folium.Marker(
            location=[row["LATITUDE"], row["LONGITUDE"]],
            popup=folium.Popup(_popup_html(row, clima), max_width=300),
            tooltip=f"OS {_safe(row.get('DESC_NUMERO_OS'))} — {_safe(row.get('COD_ATIVO'))}",
            icon=folium.DivIcon(html=icon_html, icon_size=(12, 12), icon_anchor=(6, 6)),
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
                ordem  = _safe(row.get("ORDEM_VISITA", "?"))
                cor    = _cor(row)
                clima  = (weather_map or {}).get(row["COD_ATIVO"])

                icon_html = f"""
                <div style='background:{cor};border:2.5px solid white;border-radius:50%;
                            width:22px;height:22px;display:flex;align-items:center;
                            justify-content:center;font-size:10px;font-weight:bold;
                            color:white;box-shadow:0 0 6px rgba(0,0,0,0.7);'>{ordem}</div>
                """
                folium.Marker(
                    location=[row["LATITUDE"], row["LONGITUDE"]],
                    popup=folium.Popup(_popup_html(row, clima), max_width=300),
                    tooltip=f"#{ordem} — {_safe(row.get('DESC_NUMERO_OS'))}",
                    icon=folium.DivIcon(html=icon_html, icon_size=(22, 22), icon_anchor=(11, 11)),
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
        <span style='color:#00CFFF'>- -</span> Rota otimizada
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
