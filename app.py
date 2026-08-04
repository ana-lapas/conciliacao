# app.py
import streamlit as st
import pandas as pd
import os
import tempfile
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from app.services.sofia_api import SofiaAPI
from app.services.conta_azul_utils import listar_contas_financeiras, listar_categorias_receita, definir_configuracao
from app.services.remessa_sync import sincronizar_remessa
from app.services.retorno_sync import sincronizar_retorno
from app.services.matcher import conciliar_retorno
from app.services.conta_azul import get_authorization_url, exchange_code, get_credentials
import logging

load_dotenv(find_dotenv())
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True   # <- força a reconfiguração, mesmo que outro módulo já tenha configurado
)

st.set_page_config(page_title="Conciliação Financeira", layout="wide")
st.title("🔄 Conciliação Financeira Escolar")

# Conexão com o banco
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

st.title("🔄 Conciliação Financeira Escolar")

# --- NOVO: Dashboard Rápido ---
with engine.connect() as conn:
    qtd_pendentes = conn.execute(text("SELECT COUNT(*) FROM payment_match WHERE status = 'PENDENTE_REVISAO'")).scalar()
    qtd_prontos = conn.execute(text("SELECT COUNT(*) FROM payment_match WHERE status = 'CONCILIADO' AND conta_azul_receita_id IS NULL")).scalar()

col1, col2, col3 = st.columns(3)
col1.metric("Pendentes de Revisão", qtd_pendentes, delta_color="inverse")
col2.metric("Prontos p/ Envio Conta Azul", qtd_prontos)
st.divider() 
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
            "Arquivos de Retorno (.ret) - obrigatório para conciliação",
            type=["ret"],
            accept_multiple_files=True,
            key="ret"
        )
    with col2:
        rem_files = st.file_uploader(
            "Arquivos de Remessa (.rem) - obrigatório para conciliação",
            type=["rem"],
            accept_multiple_files=True,
            key="rem"
        )

    if st.button("Processar Conciliação", type="primary"):
        if not ret_files:
            st.error("Pelo menos um arquivo de retorno é obrigatório.")
        elif not rem_files:
            st.error("Pelo menos um arquivo de remessa é obrigatório.")
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
            if st.button("Aprovar e Enviar para Conta Azul"):
                with engine.begin() as conn:
                    # 1. Atualiza nome e status
                    conn.execute(
                        text("UPDATE payment_match SET nome_responsavel = :nome, status = 'CONCILIADO' WHERE id = :id"),
                        {"nome": novo_nome.strip().upper(), "id": selected_id}
                    )
                    # 2. Copia valores do retorno e data de vencimento da remessa (se existir)
                    conn.execute(
                        text("""
                            UPDATE payment_match pm
                            SET
                                valor_pago = r.valor_pago,
                                data_pagamento = r.data_pagamento,
                                data_vencimento = COALESCE(pm.data_vencimento, rem.data_vencimento)
                            FROM retorno r
                            LEFT JOIN remessa rem ON rem.nosso_numero = r.nosso_numero
                            WHERE pm.id = :pm_id AND r.id = :ret_id
                        """),
                        {"pm_id": selected_id, "ret_id": int(row["Retorno ID"])}
                    )

                    # 3. Descrição da remessa
                    descricao = None
                    row_desc = conn.execute(
                        text("SELECT mensagem1, mensagem2, mensagem3, mensagem4 FROM remessa_mensagem WHERE nosso_numero = :nn"),
                        {"nn": row["Nosso Número"]}
                    ).first()
                    if row_desc:
                        partes = [row_desc.mensagem1, row_desc.mensagem2, row_desc.mensagem3, row_desc.mensagem4]
                        descricao = " | ".join(p for p in partes if p)
                        conn.execute(
                            text("UPDATE payment_match SET descricao_pagamento = :desc WHERE id = :id"),
                            {"desc": descricao, "id": selected_id}
                        )

                    # 4. Envio ao Conta Azul
                    try:
                        from app.services.conta_azul_receitas import criar_receita_com_baixa
                        descricao_completa = f"{descricao or 'Boleto'} - Aluno: {novo_nome.strip().upper()}"
                        data_pagamento_str = row["Data Pagamento"].strftime('%Y-%m-%d') if hasattr(row["Data Pagamento"], 'strftime') else str(row["Data Pagamento"])

                        parcela_id = criar_receita_com_baixa(
                            data_vencimento=data_pagamento_str,
                            valor=float(row["Valor Pago"]),
                            descricao=descricao_completa,
                            nome_cliente=novo_nome.strip().upper(),
                            data_pagamento=data_pagamento_str
                        )
                        conn.execute(
                            text("UPDATE payment_match SET conta_azul_receita_id = :caid WHERE id = :id"),
                            {"caid": parcela_id, "id": selected_id}
                        )
                        st.success("Pagamento aprovado e receita criada no Conta Azul!")
                    except Exception as e:
                        conn.execute(
                            text("UPDATE payment_match SET mensagem = :msg WHERE id = :id"),
                            {"msg": f"Erro Conta Azul: {str(e)[:200]}", "id": selected_id}
                        )
                        st.warning(f"Pagamento aprovado, mas houve falha ao enviar para o Conta Azul: {e}. Você pode reenviar depois.")
                st.rerun()
    else:
        st.info("Nenhum pagamento pendente de revisão.")
# =============================================================================
# Aba 3: Conciliações Prontas (revisão final, exportação e envio ao Conta Azul)
# =============================================================================
with tab3:
    st.header("Pagamentos Conciliados (Prontos para Exportação)")

    with engine.connect() as conn:
        conciliados = conn.execute(
            text("""
                SELECT pm.id, pm.retorno_id, r.nosso_numero, pm.nome_responsavel,
                    pm.cpf_responsavel,
                    COALESCE(pm.valor_pago, r.valor_pago) AS valor_pago,
                    COALESCE(pm.data_pagamento, r.data_pagamento) AS data_pagamento,
                    pm.data_vencimento,
                    pm.mensagem, pm.conta_azul_receita_id
                FROM payment_match pm
                JOIN retorno r ON r.id = pm.retorno_id
                WHERE pm.status = 'CONCILIADO'
            """)
        ).fetchall()

    if conciliados:
        df_conciliados = pd.DataFrame(
            conciliados,
            columns=["ID", "Retorno ID", "Nosso Número", "Nome Responsável",
                     "CPF", "Valor Pago", "Data Pagamento", "Vencimento",
                     "Mensagem", "Enviado ao Conta Azul?"]
        )
        # Substitui None por "Não" e preenche com "Sim" se houver ID
        df_conciliados["Enviado ao Conta Azul?"] = df_conciliados["Enviado ao Conta Azul?"].apply(
            lambda x: "Sim" if x else "Não"
        )
        st.dataframe(df_conciliados, width='stretch')

        # Seção de ações rápidas
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
                st.rerun()

        with col2:
            if st.button("Exportar CSV (todos os conciliados)"):
                csv = df_conciliados.to_csv(index=False, sep=";")
                st.download_button(
                    label="Baixar CSV",
                    data=csv,
                    file_name="conciliações_prontas.csv",
                    mime="text/csv"
                )

        # Seção de envio ao Conta Azul
        st.subheader("Envio ao Conta Azul")
        # Conta quantos ainda não foram enviados
        nao_enviados = df_conciliados[df_conciliados["Enviado ao Conta Azul?"] == "Não"]
        if not nao_enviados.empty:
            st.info(f"Existem {len(nao_enviados)} registro(s) ainda não enviados ao Conta Azul.")
            if st.button("Reenviar todos os não enviados"):
                progresso = st.progress(0)
                sucessos = 0
                falhas = 0
                total = len(nao_enviados)
                for i, (idx, row) in enumerate(nao_enviados.iterrows()):
                    try:
                        # Busca a descrição (se houver)
                        descricao = row.get("descricao_pagamento")  # coluna ainda não existe no DataFrame, mas vamos ignorar
                        # Como não temos a descrição no select, faremos um novo select rápido
                        with engine.connect() as conn2:
                            desc_row = conn2.execute(
                                text("SELECT descricao_pagamento FROM payment_match WHERE id = :id"),
                                {"id": row["ID"]}
                            ).first()
                            descricao_atual = desc_row[0] if desc_row else None
                        from app.services.conta_azul_receitas import criar_receita_com_baixa

                        descricao_completa = f"{descricao_atual or 'Boleto'} - Aluno: {row['Nome Responsável']}"
                        parcela_id = criar_receita_com_baixa(
                            data_vencimento=str(row["Data Pagamento"]),
                            valor=float(row["Valor Pago"]),
                            descricao=descricao_completa,
                            nome_cliente=row["Nome Responsável"],
                            data_pagamento=str(row["Data Pagamento"])      # pago na mesma data
                        )
                        conn3.execute(
                            text("UPDATE payment_match SET conta_azul_receita_id = :caid WHERE id = :id"),
                            {"caid": parcela_id, "id": row["ID"]}
                        )
                        sucessos += 1
                    except Exception as e:
                        falhas += 1
                        with engine.begin() as conn3:
                            conn3.execute(
                                text("UPDATE payment_match SET mensagem = :msg WHERE id = :id"),
                                {"msg": f"Erro no reenvio: {str(e)[:200]}", "id": row["ID"]}
                            )
                    progresso.progress((i + 1) / total)
                st.success(f"Reenvio concluído: {sucessos} sucessos, {falhas} falhas.")
                st.rerun()
        else:
            st.success("Todos os registros conciliados já foram enviados ao Conta Azul.")
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
                # Em vez de bloquear, apenas avisa se o state não bater
                expected_state = st.session_state.get("oauth_state")
                if expected_state and state != expected_state:
                    st.warning("Aviso de segurança: state não corresponde. Continuando mesmo assim.")
                exchange_code(code, state)
                st.success("Autorizado com sucesso!")
                # Limpa os parâmetros da URL
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

    st.subheader("Configurar contas e categorias")
    
    # 1. Carrega e exibe as contas financeiras (você já tinha isso)
    if st.button("Carregar contas financeiras e categorias"):
        st.session_state.contas = listar_contas_financeiras()
        st.session_state.categorias = listar_categorias_receita() # Nova função do backend!

    if "contas" in st.session_state and "categorias" in st.session_state:
        # Cria os dicionários para o selectbox
        opcoes_conta = {c["nome"]: c["id"] for c in st.session_state.contas}
        opcoes_cat = {c["nome"]: c["id"] for c in st.session_state.categorias}
        
        # O usuário seleciona visualmente pelo nome, mas o sistema guarda o UUID
        conta_selecionada = st.selectbox("Selecione a Conta Bancária:", list(opcoes_conta.keys()))
        cat_selecionada = st.selectbox("Selecione a Categoria de Receita:", list(opcoes_cat.keys()))
        
        if st.button("Salvar configuração"):
            # Salva no banco de dados local os UUIDs exatos que a Conta Azul espera
            definir_configuracao(opcoes_conta[conta_selecionada], opcoes_cat[cat_selecionada])
            st.success("Configuração salva com sucesso!")
