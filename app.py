# app.py
import streamlit as st
import pandas as pd
import os
import tempfile
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.services.sofia_api import SofiaAPI
from app.services.remessa_sync import sincronizar_remessa
from app.services.retorno_sync import sincronizar_retorno
from app.services.matcher import conciliar_retorno

load_dotenv()

st.set_page_config(page_title="Conciliação Financeira", layout="wide")
st.title("🔄 Conciliação Financeira Escolar")

# Conexão com o banco
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

# =============================================================================
# Abas
# =============================================================================
tab1, tab2 = st.tabs(["📂 Processar Arquivos", "🔍 Revisão Manual"])

# -----------------------------------------------------------------------------
# Aba 1: Upload e Conciliação Automática
# -----------------------------------------------------------------------------
# app.py (trecho da Aba 1)
with tab1:
    st.header("Upload e Conciliação")

    # Escolha do diretório de trabalho
    pasta_destino = st.text_input(
        "Pasta para salvar os arquivos (caminho completo no computador):",
        value=os.path.expanduser("~/documentos_recebidos")  # sugestão padrão
    )

    col1, col2 = st.columns(2)
    with col1:
        ret_files = st.file_uploader(
            "Arquivos de Retorno (.ret)",
            type=["ret"],
            accept_multiple_files=True,
            key="ret"
        )
    with col2:
        rem_files = st.file_uploader(
            "Arquivos de Remessa (.rem) [opcional]",
            type=["rem"],
            accept_multiple_files=True,
            key="rem"
        )

    if st.button("Processar Conciliação", type="primary"):
        if not ret_files:
            st.error("Pelo menos um arquivo de retorno é obrigatório.")
        elif not pasta_destino.strip():
            st.error("Informe uma pasta de destino.")
        else:
            # Cria a pasta, se não existir
            try:
                os.makedirs(pasta_destino, exist_ok=True)
            except Exception as e:
                st.error(f"Não foi possível criar/acessar a pasta: {e}")
                st.stop()

            # Lista para armazenar os caminhos completos dos arquivos salvos
            arquivos_salvos = []

            try:
                # 1. Salvar e processar cada retorno
                with st.spinner("Salvando e importando retorno(s)..."):
                    for ret_file in ret_files:
                        caminho = os.path.join(pasta_destino, ret_file.name)
                        with open(caminho, "wb") as f:
                            f.write(ret_file.getbuffer())
                        arquivos_salvos.append(caminho)
                        sincronizar_retorno(caminho)
                    st.success(f"{len(ret_files)} arquivo(s) de retorno importado(s).")

                # 2. Salvar e processar cada remessa (se houver)
                if rem_files:
                    with st.spinner("Salvando e importando remessa(s)..."):
                        for rem_file in rem_files:
                            caminho = os.path.join(pasta_destino, rem_file.name)
                            with open(caminho, "wb") as f:
                                f.write(rem_file.getbuffer())
                            arquivos_salvos.append(caminho)
                            sincronizar_remessa(caminho)
                        st.success(f"{len(rem_files)} arquivo(s) de remessa importado(s).")

                # 3. Conciliação automática
                with st.spinner("Executando conciliação automática..."):
                    api = SofiaAPI(
                        os.getenv("SOFIA_BASE_URL"),
                        os.getenv("SOFIA_TENANT"),
                        os.getenv("SOFIA_USUARIO"),
                        os.getenv("SOFIA_SENHA")
                    )
                    api.autenticar()
                    conciliar_retorno(api)
                    st.success("Conciliação concluída! Verifique a aba de Revisão Manual.")

                # Informa onde os arquivos foram salvos
                st.info(f"📁 Arquivos salvos em: {pasta_destino}")

            except Exception as e:
                st.error(f"Erro durante o processamento: {e}")
                # Opcional: remover arquivos que foram salvos em caso de erro crítico?
                # for caminho in arquivos_salvos:
                #     try:
                #         os.unlink(caminho)
                #     except: pass
# -----------------------------------------------------------------------------
# Aba 2: Revisão Manual de Pendentes
# -----------------------------------------------------------------------------
with tab2:
    st.header("Pagamentos Pendentes de Revisão")

    with engine.connect() as conn:
        pendentes = conn.execute(
            text("""
                SELECT pm.id, pm.retorno_id, r.nosso_numero, pm.nome_responsavel,
                       r.valor_pago, r.data_pagamento, pm.mensagem
                FROM payment_match pm
                JOIN retorno r ON r.id = pm.retorno_id
                WHERE pm.status = 'PENDENTE_REVISAO'
            """)
        ).fetchall()

    if pendentes:
        df = pd.DataFrame(pendentes, columns=["ID", "Retorno ID", "Nosso Número", "Nome Atual", "Valor Pago", "Data Pagamento", "Mensagem"])
        st.dataframe(df, use_container_width=True)

        st.subheader("Editar Pagamento")
        selected_id = st.selectbox("Selecione o ID do registro:", df["ID"].tolist())

        if selected_id:
            row = df[df["ID"] == selected_id].iloc[0]
            novo_nome = st.text_input("Nome real do pagador (conforme boleto/PDF):", value=row["Nome Atual"])
            if st.button("Aprovar e Liberar para Conta Azul"):
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE payment_match SET nome_responsavel = :nome, status = 'CONCILIADO' WHERE id = :id"),
                        {"nome": novo_nome.strip().upper(), "id": selected_id}
                    )
                    conn.execute(
                        text("UPDATE retorno SET status = 'CONCILIADO' WHERE id = :rid"),
                        {"rid": int(row["Retorno ID"])}
                    )
                st.success("Pagamento aprovado! Agora ele está CONCILIADO.")
                st.experimental_rerun()
    else:
        st.info("Nenhum pagamento pendente de revisão.")