# app.py
import streamlit as st
import pandas as pd
import os
import tempfile
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text

from app.services.sofia_api import SofiaAPI
from app.services.remessa_sync import sincronizar_remessa
from app.services.retorno_sync import sincronizar_retorno
from app.services.matcher import conciliar_retorno
from app.services.conta_azul import get_authorization_url, exchange_code, get_credentials

load_dotenv(find_dotenv())

st.set_page_config(page_title="Conciliação Financeira", layout="wide")
st.title("🔄 Conciliação Financeira Escolar")

# Conexão com o banco
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

# -----------------------------------------------------------------------------
# Abas
# -----------------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "📂 Processar Arquivos",
    "🔍 Revisão Manual",
    "📋 Conciliações Prontas",
    "💳 Conta Azul"
])

# =============================================================================
# Aba 1: Upload e Conciliação Automática
# =============================================================================
with tab1:
    st.header("Upload e Conciliação")

    pasta_destino = st.text_input(
        "Pasta para salvar os arquivos (caminho completo no computador):",
        value=os.path.expanduser("~/documentos_recebidos")
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
            try:
                os.makedirs(pasta_destino, exist_ok=True)
            except Exception as e:
                st.error(f"Não foi possível criar/acessar a pasta: {e}")
                st.stop()

            arquivos_salvos = []
            try:
                with st.spinner("Salvando e importando retorno(s)..."):
                    for ret_file in ret_files:
                        caminho = os.path.join(pasta_destino, ret_file.name)
                        with open(caminho, "wb") as f:
                            f.write(ret_file.getbuffer())
                        arquivos_salvos.append(caminho)
                        sincronizar_retorno(caminho)
                    st.success(f"{len(ret_files)} arquivo(s) de retorno importado(s).")

                if rem_files:
                    with st.spinner("Salvando e importando remessa(s)..."):
                        for rem_file in rem_files:
                            caminho = os.path.join(pasta_destino, rem_file.name)
                            with open(caminho, "wb") as f:
                                f.write(rem_file.getbuffer())
                            arquivos_salvos.append(caminho)
                            sincronizar_remessa(caminho)
                        st.success(f"{len(rem_files)} arquivo(s) de remessa importado(s).")

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

                st.info(f"📁 Arquivos salvos em: {pasta_destino}")
            except Exception as e:
                st.error(f"Erro durante o processamento: {e}")

# =============================================================================
# Aba 2: Revisão Manual
# =============================================================================
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
        st.dataframe(df, width='stretch')

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
# =============================================================================
# Aba 3: Conciliações Prontas (revisão final e exportação)
# =============================================================================
with tab3:
    st.header("Pagamentos Conciliados (Prontos para Exportação)")

    with engine.connect() as conn:
        conciliados = conn.execute(
            text("""
                SELECT pm.id, pm.retorno_id, r.nosso_numero, pm.nome_responsavel,
                       pm.cpf_responsavel, pm.valor_pago, pm.data_pagamento,
                       pm.data_vencimento, pm.mensagem
                FROM payment_match pm
                JOIN retorno r ON r.id = pm.retorno_id
                WHERE pm.status = 'CONCILIADO'
            """)
        ).fetchall()

    if conciliados:
        df_conciliados = pd.DataFrame(
            conciliados,
            columns=["ID", "Retorno ID", "Nosso Número", "Nome Responsável",
                     "CPF", "Valor Pago", "Data Pagamento", "Vencimento", "Mensagem"]
        )
        st.dataframe(df_conciliados, width='stretch')

        st.subheader("Ações")
        col1, col2 = st.columns(2)

        with col1:
            selected_id = st.selectbox("Selecione um registro para reabrir:", df_conciliados["ID"].tolist())
            if st.button("Reabrir Registro (voltar para Revisão)"):
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE payment_match SET status = 'PENDENTE_REVISAO' WHERE id = :id"),
                        {"id": selected_id}
                    )
                    conn.execute(
                        text("UPDATE retorno SET status = 'PENDENTE_REVISAO' WHERE id = :rid"),
                        {"rid": int(df_conciliados[df_conciliados["ID"] == selected_id]["Retorno ID"].iloc[0])}
                    )
                st.success("Registro reaberto para revisão.")
                st.experimental_rerun()

        with col2:
            if st.button("Exportar CSV (todos os conciliados)"):
                csv = df_conciliados.to_csv(index=False, sep=";")
                st.download_button(
                    label="Baixar CSV",
                    data=csv,
                    file_name="conciliações_prontas.csv",
                    mime="text/csv"
                )
    else:
        st.info("Nenhum pagamento conciliado no momento.")

# =============================================================================
# Aba 4: Conta Azul (conexão OAuth)
# =============================================================================
with tab4:
    st.header("Conexão Conta Azul")

    # Capturar code da URL (callback)
    params = st.query_params
    code = params.get("code", None)
    state = params.get("state", None)

    if code:
        with st.spinner("Autorizando..."):
            try:
                if "oauth_state" not in st.session_state or state != st.session_state["oauth_state"]:
                    st.error("Falha de segurança: state inválido.")
                else:
                    exchange_code(code, state)
                    st.success("Autorizado com sucesso!")
                    # Remove apenas os parâmetros de autenticação
                    new_params = {k: v for k, v in st.query_params.items() if k not in ("code", "state")}
                    st.query_params.clear()
                    st.query_params.update(new_params)
                    st.rerun()
            except Exception as e:
                st.error(f"Erro na autorização: {e}")
                st.exception(e)

    # Verificar se já está conectado
    creds = get_credentials()
    if creds and creds.get("access_token"):
        st.success(f"Conectado! Token válido até {creds['expires_at'].strftime('%d/%m/%Y %H:%M')}")
        # Futuramente: opção de exportar via API ou gerar lote
    else:
        auth_url, state = get_authorization_url()
        st.session_state["oauth_state"] = state
        st.link_button("Conectar com Conta Azul", url=auth_url)
