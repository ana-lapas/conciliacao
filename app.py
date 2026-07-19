import streamlit as st
from utils.sofia_api import SofiaAPI
import os
from dotenv import load_dotenv

load_dotenv()
base_url = st.secrets["SOFIA_BASE_URL"]
tenant = st.secrets["SOFIA_TENANT"]
usuario = st.secrets["SOFIA_USUARIO"]
senha = st.secrets["SOFIA_SENHA"]

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
    # Aqui implementaremos o fluxo OAuth2
    if st.button("Conectar com Conta Azul"):
        st.write("Iniciando fluxo de autenticação OAuth2...")

with tab_conciliacao:
    st.header("Processar Conciliação")
    st.write("Aguardando dados dos módulos anteriores.")