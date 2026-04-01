# ⚡ Roteirização de Inspeção — LT v2.0

Sistema de roteirização de inspeção de linhas de transmissão.

---

## 🏗️ Arquitetura

```
inspecao_v2/
│
├── app.py                     ← UI principal (Streamlit)
│
├── data/
│   └── database.py            ← Conexão Fabric + queries SQL
│                                 JOIN via COD_ATIVO
│
├── domain/
│   ├── models.py              ← Dataclass Inspecao + Enum Prioridade
│   └── priorizacao.py         ← Regras de negócio (priorizar, selecionar)
│
├── services/
│   └── weather.py             ← OpenWeather atual + previsão 5 dias
│
├── utils/
│   └── routing.py             ← Nearest Neighbor TSP
│
├── ui/
│   └── components/
│       └── mapa.py            ← Mapa Folium com cores por prioridade
│
└── api/
    └── main.py                ← FastAPI (evolução futura)
```

---

## 🔑 REGRA MAIS IMPORTANTE

```
VIEW_PLANO_CONSOLIDADO_INSPECAO.COD_ATIVO
            ↕  JOIN EXCLUSIVO
VW_TORRES_COM_CRITICIDADE.COD_ATIVO
```

**Nunca** usar `COD_OS` como chave de ligação entre as duas views.

---

## 🎯 Lógica de Priorização

| Prioridade | Condição | Cor |
|---|---|---|
| 1 — MÁXIMA | `STATUS_PRAZO = 'ATRASADA'` | 🔴 Vermelho |
| 2 — ALTA | `DATA_LIMITE` em ≤ 7 dias | 🟡 Amarelo |
| 3 — NORMAL | Demais OS | 🟢 Verde |

**Ordenação da rota:**
```
PRIORIDADE ASC → DIAS_ATRASO DESC → DATA_LIMITE ASC
```

---

## 🌦️ Clima

- Clima **não filtra** nem **remove** OS da rota
- É **apenas apoio** ao inspetor
- Exibe **previsão de 5 dias** por OS
- Destaca risco visualmente (⛔) sem alterar a rota

---

## 🚀 Rodar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## 📡 Evolução para API

Ver `api/main.py` para blueprint FastAPI pronto para produção.

```bash
pip install fastapi uvicorn
uvicorn api.main:app --reload
```
