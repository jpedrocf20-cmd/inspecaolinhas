# ⚡ App de Roteirização de Inspeção — Linhas de Transmissão

Aplicativo Streamlit para planejamento de rotas de inspeção com dados do Microsoft Fabric,
priorização por criticidade e integração com clima (OpenWeather).

---

## 📁 Estrutura do Projeto

```
inspecao_app/
├── app.py                    # App principal (Streamlit)
├── requirements.txt
├── .env.example              # Template de variáveis de ambiente
├── .gitignore
│
├── services/
│   ├── database.py           # Conexão segura com Microsoft Fabric
│   └── weather.py            # Integração OpenWeather API
│
├── components/
│   └── mapa.py               # Componente Folium (mapa interativo)
│
└── utils/
    └── routing.py            # Algoritmo de score + otimização de rota
```

---

## 🚀 Configuração local

### 1. Clonar e instalar dependências

```bash
git clone <seu-repo>
cd inspecao_app
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Instalar o ODBC Driver 18

**Ubuntu/Debian:**
```bash
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list \
    | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
```

**Windows:** Baixar de https://aka.ms/downloadmsodbcsql

### 3. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Edite o .env com suas credenciais reais
```

### 4. Criar Service Principal no Azure AD

```bash
# Via Azure CLI
az ad sp create-for-rbac \
  --name "sp-inspecao-app" \
  --role "Contributor" \
  --scopes /subscriptions/<SUB_ID>/resourceGroups/<RG>
```

Copie `appId` → `AZURE_CLIENT_ID`, `password` → `AZURE_CLIENT_SECRET`, `tenant` → `AZURE_TENANT_ID`.

**Grant no Fabric:** No Fabric Workspace, adicione o Service Principal como **Viewer** (ou **Contributor** se precisar escrever).

### 5. Rodar localmente

```bash
streamlit run app.py
```

---

## ☁️ Deploy no Azure App Service

### Opção rápida via CLI

```bash
az webapp up \
  --name inspecao-app \
  --resource-group rg-inspecao \
  --runtime "PYTHON:3.11" \
  --sku B2
```

### Configurar variáveis de ambiente no App Service

```bash
az webapp config appsettings set \
  --name inspecao-app \
  --resource-group rg-inspecao \
  --settings \
    FABRIC_SERVER="<seu_servidor>" \
    FABRIC_DATABASE="SGM" \
    AZURE_TENANT_ID="<tenant>" \
    AZURE_CLIENT_ID="<client_id>" \
    AZURE_CLIENT_SECRET="<secret>" \
    OPENWEATHER_API_KEY="<key>"
```

> ✅ **Nunca commitar o `.env` real no Git.** O `.gitignore` já o exclui.

### Autenticação de usuários (Azure AD)

No portal Azure → App Service → **Authentication** → Add identity provider → **Microsoft**
→ escolha seu tenant. Isso adiciona login SSO sem nenhuma linha de código extra.

---

## 🔐 Segurança

| Camada | Solução |
|---|---|
| Credenciais DB | Variáveis de ambiente / Azure Key Vault |
| Auth de usuários | Azure App Service Authentication (Azure AD) |
| Conexão Fabric | Service Principal (sem usuário/senha) |
| Tráfego | HTTPS obrigatório no App Service |
| Cache | `@st.cache_data` — reduz chamadas ao DB e à API |

---

## 🧠 Algoritmo de Rota

1. **Score** por torre: criticidade + quantidade de SS + penalidade de atraso
2. **Filtro climático**: remove torres com vento > 36 km/h ou chuva > 5 mm/h
3. **Seleção**: forças torres atrasadas + completa por score até `max_torres`
4. **Sequência**: Nearest Neighbor TSP heurístico (minimiza distância total)

---

## 📦 Dependências principais

| Lib | Uso |
|---|---|
| streamlit | Interface web |
| pyodbc + msal | Conexão autenticada com Fabric |
| folium + streamlit-folium | Mapa interativo |
| pandas + numpy + scipy | Processamento de dados |
| requests | OpenWeather API |
| networkx | (disponível para extensões de grafo) |
