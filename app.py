import streamlit as st
from utils.sofia_api import SofiaAPI
import os
from dotenv import load_dotenv
import requests
import urllib.parse

load_dotenv()
base_url = st.secrets["SOFIA_BASE_URL"]
tenant = st.secrets["SOFIA_TENANT"]
usuario = st.secrets["SOFIA_USUARIO"]
senha = st.secrets["SOFIA_SENHA"]
CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]
REDIRECT_URI = st.secrets["REDIRECT_URI"]

api = SofiaAPI(base_url, tenant, usuario, senha)

st.set_page_config(page_title="Conciliação Financeira", layout="wide")

st.title("🔄 Conciliação Financeira Escolar")

tab_sophia, tab_conta_azul, tab_conciliacao = st.tabs([
    "📂 Sophia (Importação)", 
    "💳 Conta Azul (Exportação)", 
    "⚙️ Conciliação"
])

with tab_sophia:
    st.header("Importação de Dados - Sophia")
    # Futuro input de arquivos CNAB ou busca via API
    if st.button("Buscar Dados Sophia"):
        st.info("Módulo Sophia em pausa técnica.")

with tab_conta_azul:
    st.title("Conciliação Conta Azul")

    params = st.query_params
    code = params.get("code")

    if not code:
        # URL de autorização para o botão
        auth_url = f"https://api.contaazul.com/auth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=accounting"
        st.markdown(f"[Conectar com Conta Azul]({auth_url})")
    else:
        st.write("Código recebido! Trocando por token...")
        
        # 3. Troca do Code pelo Token
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        }
        
        # Auth via Basic (exemplo comum, verifique se a Conta Azul exige header ou body)
        response = requests.post(
            "https://api.contaazul.com/oauth2/token",
            data=data,
            auth=(CLIENT_ID, CLIENT_SECRET)
        )
        
        if response.status_code == 200:
            tokens = response.json()
            st.success("Conectado com sucesso!")
            st.session_state["access_token"] = tokens.get("access_token")
            # Limpa a URL para sumir o code
            st.query_params.clear()
        else:
            st.error(f"Erro: {response.text}")

    # 4. Exibição de Dados (Se tiver token)
    if "access_token" in st.session_state:
        st.write("Token ativo! Agora é só chamar o endpoint:")
        # Exemplo: headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}

with tab_conciliacao:
    st.header("Processar Conciliação")
    st.write("Aguardando dados dos módulos anteriores.")