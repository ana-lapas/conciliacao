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
    st.header("Conexão Conta Azul")

    # 1. Captura de forma segura
    params = st.query_params
    code = params.get("code")

    if code and "access_token" not in st.session_state:
        with st.spinner("Trocando código por token..."):
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://conciliacao-m6hfug34equljwalpu5xb4.streamlit.app/",
            }
            
            # Conta Azul espera as credenciais via Basic Auth
            response = requests.post(
                "https://api.contaazul.com/oauth2/token",
                data=data,
                auth=(CLIENT_ID, CLIENT_SECRET)
            )
            
            if response.status_code == 200:
                tokens = response.json()
                st.session_state["access_token"] = tokens.get("access_token")
                st.session_state["refresh_token"] = tokens.get("refresh_token")
                
                # A mágica para não quebrar: recarregar sem o parâmetro 'code' na URL
                st.query_params.clear()
                st.rerun() 
            else:
                st.error(f"Erro na autenticação: {response.status_code}")
                st.text(response.text)

    # 2. Exibição do estado
    if "access_token" in st.session_state:
        st.success("Conectado com sucesso!")
        with st.expander("Dados de Debug (Token)"):
            st.write("Access Token:", st.session_state["access_token"])
            if "refresh_token" in st.session_state:
                st.write("Refresh Token:", st.session_state["refresh_token"])
        
        st.write("Token ativo. Pronto para realizar requisições.")
    else:
        # Botão mantido apenas se não estiver logado
        auth_url = f"https://api.contaazul.com/auth/authorize?client_id={CLIENT_ID}&redirect_uri=https://conciliacao-m6hfug34equljwalpu5xb4.streamlit.app&response_type=code&scope=accounting"
        st.markdown(f'<a href="{auth_url}" target="_self">Clique aqui para conectar com Conta Azul</a>', unsafe_allow_html=True)

with tab_conciliacao:
    st.header("Processar Conciliação")
    st.write("Aguardando dados dos módulos anteriores.")