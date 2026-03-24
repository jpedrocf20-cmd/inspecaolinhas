"""
components/mapa.py
Componente de mapa Folium com torres coloridas por criticidade,
ícones de clima e linha da rota otimizada.
"""

import folium
import pandas as pd
from folium.plugins import MarkerCluster

# Paleta de cores por nível de criticidade
CORES_CRITICIDADE = {
    1: "#FF2D2D",   # vermelho vivo   — mais crítico
    2: "#FF6B2D",   # laranja
    3: "#FFA500",   # âmbar
    4: "#FFD700",   # amarelo
    5: "#90EE90",   # verde claro
    6: "#4CAF50",   # verde          — menos crítico
}

ICONE_ATRASADO = "⚠️"


def _cor_criticidade(nivel: int) -> str:
    return CORES_CRITICIDADE.get(int(nivel), "#999999")


def _popup_html(row: pd.Series, clima: dict | None) -> str:
    cor    = _cor_criticidade(row["CRITICIDADE_MIN"])
    clima_html = ""
    if clima and clima.get("ok"):
        risco_badge = "⛔ RISCO" if clima["risco"] else "✅ OK"
        clima_html = f"""
        <hr style='margin:6px 0'>
        <b>🌦️ Clima:</b> {clima['descricao']}<br>
        <b>🌡️ Temp:</b> {clima['temperatura']}°C &nbsp;
        <b>💧 Umid:</b> {clima['umidade']}%<br>
        <b>💨 Vento:</b> {clima['vento_kmh']} km/h &nbsp;
        <b>🌧️ Chuva:</b> {clima['chuva_mm']} mm/h<br>
        <b>Status:</b> {risco_badge}
        """

    atraso_badge = (
        "<span style='color:#FF2D2D;font-weight:bold'>⚠️ ATRASADO</span>"
        if row.get("FL_ATRASADO") == 1
        else "<span style='color:#4CAF50'>✅ No prazo</span>"
    )

    return f"""
    <div style='font-family:sans-serif;font-size:13px;min-width:200px'>
        <div style='background:{cor};color:white;padding:6px 10px;border-radius:4px;
                    font-weight:bold;font-size:14px;margin-bottom:8px'>
            Torre {row.get('NUM_TORRE','–')} &nbsp;|&nbsp; Nível {int(row['CRITICIDADE_MIN'])}
        </div>
        <b>Ativo:</b> {row['COD_ATIVO']}<br>
        <b>Empresa:</b> {row.get('EMPRESA','–')}<br>
        <b>Instalação:</b> {row.get('INSTALACAO','–')}<br>
        <b>Ocorrências:</b> {int(row['QTD_SS'])}<br>
        <b>Maior atraso:</b> {int(row['PIOR_SALDO_DIAS'])} dias<br>
        <b>Status:</b> {atraso_badge}
        {clima_html}
    </div>
    """


def build_map(
    df: pd.DataFrame,
    df_rota: pd.DataFrame | None = None,
    weather_map: dict | None = None,
    usar_cluster: bool = False,
) -> folium.Map:
    """
    Constrói o mapa Folium.

    df        : todas as torres (para mostrar contexto)
    df_rota   : torres na rota otimizada (ordem de visita)
    weather_map: { COD_ATIVO: dict do clima }
    """
    if df.empty:
        return folium.Map(location=[-15.8, -47.9], zoom_start=5)

    center_lat = df["LATITUDE"].mean()
    center_lon = df["LONGITUDE"].mean()

    mapa = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=8,
        tiles="CartoDB dark_matter",
        control_scale=True,
    )

    # ── Camada: todas as torres (fundo) ──
    layer_todas = folium.FeatureGroup(name="Todas as torres", show=True)
    container = MarkerCluster() if usar_cluster else layer_todas

    for _, row in df.iterrows():
        cor   = _cor_criticidade(row["CRITICIDADE_MIN"])
        clima = (weather_map or {}).get(row["COD_ATIVO"])

        # Ícone diferenciado para torres atrasadas
        icon_html = f"""
        <div style='
            background:{cor};
            border:2px solid white;
            border-radius:50%;
            width:14px;height:14px;
            box-shadow:0 0 4px rgba(0,0,0,0.6);
        '></div>
        """
        folium.Marker(
            location=[row["LATITUDE"], row["LONGITUDE"]],
            popup=folium.Popup(_popup_html(row, clima), max_width=280),
            tooltip=f"Torre {row.get('NUM_TORRE','?')} — Nível {int(row['CRITICIDADE_MIN'])}",
            icon=folium.DivIcon(html=icon_html, icon_size=(14, 14), icon_anchor=(7, 7)),
        ).add_to(container if usar_cluster else layer_todas)

    if usar_cluster:
        container.add_to(layer_todas)
    layer_todas.add_to(mapa)

    # ── Camada: rota otimizada ──
    if df_rota is not None and not df_rota.empty:
        layer_rota = folium.FeatureGroup(name="🗺️ Rota otimizada", show=True)
        coords_rota = df_rota[["LATITUDE", "LONGITUDE"]].values.tolist()

        # Linha da rota
        folium.PolyLine(
            coords_rota,
            color="#00CFFF",
            weight=2.5,
            opacity=0.85,
            dash_array="6 4",
        ).add_to(layer_rota)

        # Marcadores numerados da rota
        for _, row in df_rota.iterrows():
            ordem = int(row["ORDEM_VISITA"])
            cor   = _cor_criticidade(row["CRITICIDADE_MIN"])
            clima = (weather_map or {}).get(row["COD_ATIVO"])

            icon_html = f"""
            <div style='
                background:{cor};
                border:2.5px solid white;
                border-radius:50%;
                width:22px;height:22px;
                display:flex;align-items:center;justify-content:center;
                font-size:10px;font-weight:bold;color:white;
                box-shadow:0 0 6px rgba(0,0,0,0.7);
            '>{ordem}</div>
            """
            folium.Marker(
                location=[row["LATITUDE"], row["LONGITUDE"]],
                popup=folium.Popup(_popup_html(row, clima), max_width=280),
                tooltip=f"#{ordem} — Torre {row.get('NUM_TORRE','?')}",
                icon=folium.DivIcon(html=icon_html, icon_size=(22, 22), icon_anchor=(11, 11)),
            ).add_to(layer_rota)

        layer_rota.add_to(mapa)

    # Legenda
    legenda_html = """
    <div style='
        position:fixed;bottom:30px;right:10px;z-index:1000;
        background:rgba(20,20,30,0.92);color:white;
        padding:10px 14px;border-radius:8px;font-size:12px;
        border:1px solid rgba(255,255,255,0.15);
        font-family:sans-serif;line-height:1.8;
    '>
        <b>Criticidade</b><br>
        <span style='color:#FF2D2D'>●</span> Nível 1 — Crítico<br>
        <span style='color:#FF6B2D'>●</span> Nível 2<br>
        <span style='color:#FFA500'>●</span> Nível 3<br>
        <span style='color:#FFD700'>●</span> Nível 4<br>
        <span style='color:#90EE90'>●</span> Nível 5<br>
        <span style='color:#4CAF50'>●</span> Nível 6 — Normal<br>
        <hr style='border-color:rgba(255,255,255,0.2);margin:4px 0'>
        <span style='color:#00CFFF'>- -</span> Rota otimizada
    </div>
    """
    mapa.get_root().html.add_child(folium.Element(legenda_html))
    folium.LayerControl(collapsed=False).add_to(mapa)

    return mapa
