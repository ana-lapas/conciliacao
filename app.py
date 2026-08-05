import logging
import os
import tempfile
import pandas as pd
import streamlit as st
from dotenv import find_dotenv, load_dotenv
from sqlalchemy import create_engine, text

# --- IMPORTAÇÃO DOS SERVIÇOS DO BACKEND ---
# Importamos as funções que conversam com as APIs externas (Conta Azul e Sofia) e tratam os arquivos
from app.services.conta_azul import (
    exchange_code,           # Troca o código temporário do OAuth2 pelo token de acesso
    get_authorization_url,   # Gera o link para o usuário logar no Conta Azul
    get_credentials,         # Busca os tokens salvos no banco de dados local
)
from app.services.conta_azul_utils import (
    definir_configuracao,        # Salva a conta bancária e categoria padrão selecionadas
    listar_categorias_receita,   # Busca categorias financeiras da API do Conta Azul
    listar_contas_financeiras,   # Busca contas bancárias da API do Conta Azul
    obter_configuracao,
    obter_ou_criar_categoria,
    obter_ou_criar_contato,
    traduzir_erro_para_usuario,
)
from app.services.matcher import conciliar_retorno    # Motor que cruza pagamentos com a API da Sofia
from app.services.remessa_sync import sincronizar_remessa   # Lógica que lê o arquivo .rem e salva no banco
from app.services.retorno_sync import sincronizar_retorno   # Lógica que lê o arquivo .ret e salva no banco
from app.services.sofia_api import SofiaAPI                 # Cliente HTTP para a API escolar Sofia

# -----------------------------------------------------------------------------
# 1. CONFIGURAÇÕES INICIAIS E BANCO DE DADOS
# -----------------------------------------------------------------------------
# Carrega as variáveis de ambiente (.env) como URLs, senhas e chaves de API
load_dotenv(find_dotenv())

# Configura o sistema de logs para exibir informações no terminal para debug
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True,
)

# Configura a página do Streamlit (título da aba do navegador e layout largo)
st.set_page_config(page_title="Conciliação Financeira", layout="wide")
st.title("🔄 Conciliação Financeira Escolar")

# Cria o pool de conexões com o banco de dados PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

# --- GARANTE A CRIAÇÃO DA TABELA DE CONFIGURAÇÕES DO CONTA AZUL (PONTO D) ---
# Executa um DDL atômico para impedir erros caso o banco seja zerado ou reiniciado
# No bloco 1 do app.py (Início da aplicação):
with engine.begin() as conn:
    # Garante a tabela de configuração do Conta Azul
    conn.execute(
        text("""
            CREATE TABLE IF NOT EXISTS conta_azul_config (
                id SERIAL PRIMARY KEY,
                conta_financeira_id VARCHAR(255) NOT NULL,
                categoria_id VARCHAR(255) NOT NULL,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    )
    
    # Garante as colunas em payment_match se não existirem
    conn.execute(text("ALTER TABLE payment_match ADD COLUMN IF NOT EXISTS nome_aluno VARCHAR(255);"))
    conn.execute(text("ALTER TABLE payment_match ADD COLUMN IF NOT EXISTS descricao_pagamento TEXT;"))
    conn.execute(text("ALTER TABLE payment_match ADD COLUMN IF NOT EXISTS data_vencimento DATE;"))

# -----------------------------------------------------------------------------
# 2. DASHBOARDS E MÉTRICAS (Roda a cada refresh da página)
# -----------------------------------------------------------------------------
# Executa consultas rápidas no banco para alimentar os cartões de resumo no topo da tela
with engine.connect() as conn:
    # Conta quantos boletos precisam de atenção humana
    qtd_pendentes = conn.execute(
        text("SELECT COUNT(*) FROM payment_match WHERE status = 'PENDENTE_REVISAO'")
    ).scalar()
    
    # Conta quantos boletos foram aprovados mas ainda não subiram para o Conta Azul
    qtd_prontos = conn.execute(
        text(
            "SELECT COUNT(*) FROM payment_match WHERE status = 'CONCILIADO' AND conta_azul_receita_id IS NULL"
        )
    ).scalar()

# Desenha 3 colunas para exibir os números em destaque
col1, col2, col3 = st.columns(3)
col1.metric("Pendentes de Revisão", qtd_pendentes, delta_color="inverse")
col2.metric("Prontos p/ Envio Conta Azul", qtd_prontos)
st.divider()

# -----------------------------------------------------------------------------
# 3. ESTRUTURA DE ABAS DA APLICAÇÃO
# -----------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab_sofia = st.tabs([
    "📂 Processar Arquivos",
    "🔍 Revisão Manual",
    "📋 Conciliações Prontas",
    "💳 Conta Azul",
    "🔍 Consulta Sofia & Boletos",
])
# =============================================================================
# ABA 1: UPLOAD DE ARQUIVOS E CONCILIAÇÃO AUTOMÁTICA
# =============================================================================
with tab1:
    st.header("Upload e Conciliação")

    # Campo onde o usuário digita a pasta local onde os arquivos físicos serão salvos
    pasta_destino = st.text_input(
        "Pasta para salvar os arquivos (caminho completo no computador):",
        value=os.path.expanduser("~/documentos_recebidos"),
    )

    # Componentes de Upload (Desacoplados: permite subir .ret, .rem ou ambos)
    col1, col2 = st.columns(2)
    with col1:
        ret_files = st.file_uploader(
            "Arquivos de Retorno (.ret) - opcional se enviar remessa",
            type=["ret"],
            accept_multiple_files=True,
            key="ret",
        )
    with col2:
        rem_files = st.file_uploader(
            "Arquivos de Remessa (.rem) - opcional se enviar retorno",
            type=["rem"],
            accept_multiple_files=True,
            key="rem",
        )

    # Botão principal de processamento
    if st.button("Processar Conciliação", type="primary"):
        # Validação: impede o clique se nenhum arquivo foi selecionado
        if not ret_files and not rem_files:
            st.error("⚠️ Envie pelo menos um arquivo (Retorno ou Remessa) para processar.")
        elif not pasta_destino.strip():
            st.error("⚠️ Informe uma pasta de destino.")
        else:
            # Garante que o diretório de destino existe no sistema operacional
            try:
                os.makedirs(pasta_destino, exist_ok=True)
            except Exception as e:
                st.error(f"Não foi possível criar/acessar a pasta: {e}")
                st.stop()

            arquivos_salvos = []

            # Bloco A: Processa arquivos de retorno (.ret) se existirem
            if ret_files:
                try:
                    with st.spinner("Salvando e importando retorno(s)..."):
                        for ret_file in ret_files:
                            caminho = os.path.join(pasta_destino, ret_file.name)
                            # Grava o arquivo físico em disco
                            with open(caminho, "wb") as f:
                                f.write(ret_file.getbuffer())
                            arquivos_salvos.append(caminho)
                            # Chama o serviço que faz o parse do arquivo CNAB e salva no PostgreSQL
                            sincronizar_retorno(caminho)
                        st.success(f"✅ {len(ret_files)} arquivo(s) de retorno importado(s).")
                except Exception as e:
                    st.error(f"❌ Erro ao processar arquivo(s) de retorno: {e}")

            # Bloco B: Processa arquivos de remessa (.rem) se existirem
            if rem_files:
                try:
                    with st.spinner("Salvando e importando remessa(s)..."):
                        for rem_file in rem_files:
                            caminho = os.path.join(pasta_destino, rem_file.name)
                            # Grava o arquivo físico em disco
                            with open(caminho, "wb") as f:
                                f.write(rem_file.getbuffer())
                            arquivos_salvos.append(caminho)
                            # Chama o serviço que lê os dados de cobrança da remessa e salva no banco
                            sincronizar_remessa(caminho)
                        st.success(f"✅ {len(rem_files)} arquivo(s) de remessa importado(s).")
                except Exception as e:
                    st.error(f"❌ Erro ao processar arquivo(s) de remessa: {e}")

            # Bloco C: Conecta na API Sofia e executa a regra de conciliação automática
            try:
                with st.spinner("Executando motor de conciliação automática..."):
                    api = SofiaAPI(
                        os.getenv("SOFIA_BASE_URL"),
                        os.getenv("SOFIA_TENANT"),
                        os.getenv("SOFIA_USUARIO"),
                        os.getenv("SOFIA_SENHA"),
                    )
                    api.autenticar()
                    # Compara as entradas do banco com os alunos cadastrados na API Sofia
                    conciliar_retorno(api)
                    st.success("🚀 Conciliação concluída! Verifique a aba de Revisão Manual.")
            except Exception as e:
                st.error(f"❌ Erro na comunicação com o sistema Sofia: {e}")

            st.info(f"📁 Arquivos salvos localmente em: {pasta_destino}")

# =============================================================================
# ABA 2: REVISÃO MANUAL DE PAGAMENTOS
# =============================================================================
with tab2:
    st.header("Pagamentos Pendentes de Revisão")

    # Busca no banco os pagamentos pendentes, trazendo o responsável e o aluno
    with engine.connect() as conn:
        pendentes = conn.execute(
            text("""
                SELECT pm.id, pm.retorno_id, r.nosso_numero, 
                       pm.nome_responsavel, COALESCE(pm.nome_aluno, 'N/A') AS nome_aluno,
                       r.valor_pago, r.data_pagamento, pm.mensagem
                FROM payment_match pm
                JOIN retorno r ON r.id = pm.retorno_id
                WHERE pm.status = 'PENDENTE_REVISAO'
            """)
        ).fetchall()

    if pendentes:
        # Converte os resultados do SQL em um DataFrame Pandas para a tabela
        df = pd.DataFrame(
            pendentes,
            columns=[
                "ID",
                "Retorno ID",
                "Nosso Número",
                "Responsável Atual",
                "Aluno Atual",
                "Valor Pago",
                "Data Pagamento",
                "Mensagem",
            ],
        )

        st.write("👆 **Selecione uma linha na tabela abaixo para editar:**")

        # Exibe a tabela interativa
        evento = st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun", # Recarrega o script assim que uma linha for clicada
        )

        linhas_selecionadas = evento.selection.rows

        # Se houver uma linha selecionada, desenha os campos de edição
        if linhas_selecionadas:
            idx_linha = linhas_selecionadas[0]
            row = df.iloc[idx_linha]
            selected_id = int(row["ID"])

            st.subheader(f"Editando pagamento: {row['Nosso Número']}")
            
            # --- CAMPOS DE EDIÇÃO DUPLA: RESPONSÁVEL E ALUNO ---
            col_a, col_b = st.columns(2)
            with col_a:
                novo_nome_resp = st.text_input(
                    "Nome real do responsável (pagador):", value=row["Responsável Atual"]
                )
            with col_b:
                novo_nome_aluno = st.text_input(
                    "Nome real do aluno:", value=row["Aluno Atual"]
                )

            # Botão de ação: Aprova e envia para o ERP
            if st.button("Aprovar e Enviar para Conta Azul", type="primary"):
                # Abre transação no banco para garantir atomicidade
                with engine.begin() as conn:
                    
                    # --- 1. TRAVA DE CONCORRÊNCIA (PESSIMISTIC LOCKING) ---
                    status_atual = conn.execute(
                        text("SELECT status FROM payment_match WHERE id = :id FOR UPDATE"),
                        {"id": selected_id},
                    ).scalar()

                    if status_atual != "PENDENTE_REVISAO":
                        st.warning(
                            "⚠️ Este pagamento já foi processado ou alterado por outro usuário. Recarregando..."
                        )
                        st.rerun()

                    # --- 2. ATUALIZAÇÕES LOCAIS NO BANCO ---
                    # Salva os nomes do responsável e do aluno corrigidos e muda o status para CONCILIADO
                    conn.execute(
                        text("""
                            UPDATE payment_match 
                            SET nome_responsavel = :resp, 
                                nome_aluno = :aluno, 
                                status = 'CONCILIADO' 
                            WHERE id = :id
                        """),
                        {
                            "resp": novo_nome_resp.strip().upper(),
                            "aluno": novo_nome_aluno.strip().upper(),
                            "id": selected_id,
                        },
                    )

                    # --- CORREÇÃO DO PONTO C: FALLBACK DA DATA DE VENCIMENTO ---
                    # Se data_vencimento for nula na remessa, usa a data de pagamento do retorno
                    conn.execute(
                        text("""
                            UPDATE payment_match pm
                            SET
                                valor_pago = r.valor_pago,
                                data_pagamento = r.data_pagamento,
                                data_vencimento = COALESCE(pm.data_vencimento, rem.data_vencimento, r.data_pagamento)
                            FROM retorno r
                            LEFT JOIN remessa rem ON rem.nosso_numero = r.nosso_numero
                            WHERE pm.id = :pm_id AND r.id = :ret_id
                        """),
                        {"pm_id": selected_id, "ret_id": int(row["Retorno ID"])},
                    )

                    # Busca as mensagens da remessa para compor a descrição
                    descricao = None
                    row_desc = conn.execute(
                        text(
                            "SELECT mensagem1, mensagem2, mensagem3, mensagem4 FROM remessa_mensagem WHERE nosso_numero = :nn"
                        ),
                        {"nn": row["Nosso Número"]},
                    ).first()

                    if row_desc:
                        partes = [row_desc.mensagem1, row_desc.mensagem2, row_desc.mensagem3, row_desc.mensagem4]
                        descricao = " | ".join(p for p in partes if p)
                        conn.execute(
                            text("UPDATE payment_match SET descricao_pagamento = :desc WHERE id = :id"),
                            {"desc": descricao, "id": selected_id},
                        )

                    # --- 3. ENVIO SÍNCRONO PARA O CONTA AZUL ---
                    try:
                        from app.services.conta_azul_receitas import criar_receita_com_baixa

                        # Busca conta e categoria com fallback no banco local
                        config = obter_configuracao() or {}
                        conta_id = os.getenv("CONTA_AZUL_CONTA_FINANCEIRA_PADRAO_ID") or config.get("conta_financeira_id")
                        categoria_id = os.getenv("CONTA_AZUL_CATEGORIA_PADRAO_ID") or config.get("categoria_id")

                        if not conta_id or not categoria_id:
                            raise ValueError("Conta bancária ou Categoria padrão não configurada na Aba 4!")

                        base_desc = descricao if (descricao and descricao.strip()) else "Mensalidade Escolar"
                        nome_resp_fmt = novo_nome_resp.strip().upper()
                        nome_aluno_fmt = novo_nome_aluno.strip().upper()

                        # Formata a data de pagamento
                        data_pagamento_str = (
                            row["Data Pagamento"].strftime("%Y-%m-%d")
                            if hasattr(row["Data Pagamento"], "strftime")
                            else str(row["Data Pagamento"])
                        )

                        # Busca a data_vencimento atualizada do banco
                        data_vencimento_str = conn.execute(
                            text("SELECT data_vencimento FROM payment_match WHERE id = :id"),
                            {"id": selected_id},
                        ).scalar()
                        data_vencimento_str = str(data_vencimento_str) if data_vencimento_str else data_pagamento_str

                        # Formatação visual amigável para a discriminação do ERP (Ex: R$ 850,00 e DD/MM/AAAA)
                        valor_fmt = f"R$ {float(row['Valor Pago']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        try:
                            venc_dt = pd.to_datetime(data_vencimento_str)
                            venc_fmt = venc_dt.strftime("%d/%m/%Y")
                        except Exception:
                            venc_fmt = str(data_vencimento_str)

                        # --- MONTAGEM DA DISCRIMINAÇÃO COMPLETA SOLICITADA ---
                        descricao_completa = (
                            f"{base_desc} | Resp: {nome_resp_fmt} | Aluno: {nome_aluno_fmt} | "
                            f"Valor: {valor_fmt} | Venc: {venc_fmt}"
                        )

                        # Envia para a API do Conta Azul passando todos os campos necessários
                        parcela_id = criar_receita_com_baixa(
                            data_vencimento=data_vencimento_str,
                            valor=float(row["Valor Pago"]),
                            descricao=descricao_completa,
                            nome_cliente=nome_resp_fmt,
                            data_pagamento=data_pagamento_str,
                            conta_id=conta_id,
                            categoria_id=categoria_id,
                        )

                        # Grava o ID da receita retornado pelo Conta Azul
                        conn.execute(
                            text("UPDATE payment_match SET conta_azul_receita_id = :caid WHERE id = :id"),
                            {"caid": parcela_id, "id": selected_id},
                        )
                        st.toast("✅ Pagamento aprovado e receita criada no Conta Azul!", icon="🎉")

                    except Exception as e:
                        # Extrai a primeira linha limpa do erro para gravar no log de mensagens do banco
                        msg_erro = str(e).split('\n')[0]
                        conn.execute(
                            text("UPDATE payment_match SET mensagem = :msg WHERE id = :id"),
                            {"msg": f"Erro Conta Azul: {msg_erro}", "id": selected_id},
                        )
                        st.toast(f"⚠️ Pagamento aprovado, mas falhou no Conta Azul: {msg_erro}", icon="🚨")

                # Recarrega a tela para atualizar a lista
                st.rerun()
    else:
        st.info("Nenhum pagamento pendente de revisão.")

# =============================================================================
# ABA 3: CONCILIAÇÕES PRONTAS E REENVIOS
# =============================================================================
with tab3:
    st.header("Pagamentos Conciliados (Prontos para Exportação)")

    with engine.connect() as conn:
        # Busca pagamentos conciliados com suporte a fallback de vencimento
        conciliados = conn.execute(
            text("""
                SELECT pm.id, pm.retorno_id, COALESCE(r.nosso_numero, 'N/A') AS nosso_numero, 
                       pm.nome_responsavel, pm.nome_aluno, pm.cpf_responsavel,
                       COALESCE(pm.valor_pago, r.valor_pago, 0) AS valor_pago,
                       COALESCE(pm.data_pagamento, r.data_pagamento) AS data_pagamento,
                       COALESCE(pm.data_vencimento, r.data_pagamento) AS data_vencimento, 
                       pm.mensagem, pm.conta_azul_receita_id
                FROM payment_match pm
                LEFT JOIN retorno r ON r.id = pm.retorno_id
                WHERE pm.status = 'CONCILIADO'
            """)
        ).fetchall()

    if conciliados:
        df_conciliados = pd.DataFrame(
            conciliados,
            columns=[
                "ID",
                "Retorno ID",
                "Nosso Número",
                "Nome Responsável",
                "Nome Aluno",
                "CPF",
                "Valor Pago",
                "Data Pagamento",
                "Vencimento",
                "Mensagem",
                "Enviado ao Conta Azul?",
            ],
        )
        df_conciliados["Enviado ao Conta Azul?"] = df_conciliados[
            "Enviado ao Conta Azul?"
        ].apply(lambda x: "Sim" if x else "Não")

        st.write("👆 **Selecione uma linha para reabrir o registro:**")

        evento_tab3 = st.dataframe(
            df_conciliados,
            width="stretch",
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
        )

        linhas_sel_tab3 = evento_tab3.selection.rows

        st.subheader("Ações")
        col1, col2 = st.columns(2)

        with col1:
            if linhas_sel_tab3:
                idx_linha = linhas_sel_tab3[0]
                row_selecionada = df_conciliados.iloc[idx_linha]
                selected_id_tab3 = int(row_selecionada["ID"])

                st.info(f"Registro selecionado: **{row_selecionada['Nosso Número']}**")

                # Botão para estornar a conciliação
                if st.button("Reabrir Registro Selecionado", type="primary"):
                    with engine.begin() as conn:
                        status_atual = conn.execute(
                            text("SELECT status FROM payment_match WHERE id = :id FOR UPDATE"),
                            {"id": selected_id_tab3},
                        ).scalar()

                        if status_atual != "CONCILIADO":
                            st.warning(
                                "⚠️ Este registro não está mais conciliado. Recarregando..."
                            )
                            st.rerun()

                        conn.execute(
                            text("UPDATE payment_match SET status = 'PENDENTE_REVISAO' WHERE id = :id"),
                            {"id": selected_id_tab3},
                        )

                        if row_selecionada["Retorno ID"] and pd.notna(row_selecionada["Retorno ID"]):
                            conn.execute(
                                text("UPDATE retorno SET status = 'PENDENTE_REVISAO' WHERE id = :rid"),
                                {"rid": int(row_selecionada["Retorno ID"])},
                            )

                    st.success("Registro reaberto para revisão.")
                    st.rerun()
            else:
                st.write("*Selecione um registro na tabela para reabrir.*")

        with col2:
            if st.button("Exportar CSV (todos os conciliados)"):
                csv = df_conciliados.to_csv(index=False, sep=";")
                st.download_button(
                    label="Baixar CSV",
                    data=csv,
                    file_name="conciliacoes_prontas.csv",
                    mime="text/csv",
                )

        # Reprocessamento de erros de envio
        st.subheader("Envio ao Conta Azul")
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
                        with engine.connect() as conn2:
                            dados_pm = conn2.execute(
                                text(
                                    "SELECT descricao_pagamento, nome_aluno, nome_responsavel, data_vencimento FROM payment_match WHERE id = :id"
                                ),
                                {"id": row["ID"]},
                            ).first()

                        descricao_atual = dados_pm.descricao_pagamento if dados_pm else None
                        nome_aluno = dados_pm.nome_aluno if (dados_pm and dados_pm.nome_aluno) else "N/A"
                        nome_resp = row["Nome Responsável"]

                        # Fallback seguro para data de vencimento
                        vencimento = str(dados_pm.data_vencimento) if (dados_pm and dados_pm.data_vencimento) else str(row["Data Pagamento"])

                        config = obter_configuracao() or {}
                        conta_id = os.getenv("CONTA_AZUL_CONTA_FINANCEIRA_PADRAO_ID") or config.get("conta_financeira_id")
                        categoria_id = os.getenv("CONTA_AZUL_CATEGORIA_PADRAO_ID") or config.get("categoria_id")

                        if not conta_id or not categoria_id:
                            raise ValueError("Conta bancária ou Categoria padrão não configurada na Aba 4!")

                        base_desc = descricao_atual if (descricao_atual and descricao_atual.strip()) else "Mensalidade Escolar"
                        
                        # Formatações amigáveis
                        valor_fmt = f"R$ {float(row['Valor Pago']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        try:
                            venc_dt = pd.to_datetime(vencimento)
                            venc_fmt = venc_dt.strftime("%d/%m/%Y")
                        except Exception:
                            venc_fmt = str(vencimento)

                        # Discrimination unificada
                        descricao_completa = (
                            f"{base_desc} | Resp: {nome_resp.strip().upper()} | Aluno: {nome_aluno.strip().upper()} | "
                            f"Valor: {valor_fmt} | Venc: {venc_fmt}"
                        )

                        from app.services.conta_azul_receitas import criar_receita_com_baixa

                        parcela_id = criar_receita_com_baixa(
                            data_vencimento=vencimento,
                            valor=float(row["Valor Pago"]),
                            descricao=descricao_completa,
                            nome_cliente=nome_resp,
                            data_pagamento=str(row["Data Pagamento"]),
                            conta_id=conta_id,
                            categoria_id=categoria_id,
                        )

                        with engine.begin() as conn3:
                            conn3.execute(
                                text("UPDATE payment_match SET conta_azul_receita_id = :caid WHERE id = :id"),
                                {"caid": parcela_id, "id": row["ID"]},
                            )
                        sucessos += 1

                    except Exception as e:
                        falhas += 1
                        msg_erro = str(e).split('\n')[0]
                        with engine.begin() as conn3:
                            conn3.execute(
                                text("UPDATE payment_match SET mensagem = :msg WHERE id = :id"),
                                {"msg": f"Erro no reenvio: {msg_erro}", "id": row["ID"]},
                            )
                    progresso.progress((i + 1) / total)

                if falhas > 0:
                    st.toast(f"⚠️ Reenvio concluído: {sucessos} sucessos e {falhas} falha(s).", icon="🚨")
                else:
                    st.toast(f"✅ Todos os {sucessos} registros foram enviados com sucesso!", icon="🎉")
                
                st.rerun()
        else:
            st.success("Todos os registros conciliados já foram enviados ao Conta Azul.")
    else:
        st.info("Nenhum pagamento conciliado no momento.")

# =============================================================================
# ABA 4: AUTENTICAÇÃO E CONFIGURAÇÃO DA API CONTA AZUL (OAuth2)
# =============================================================================
with tab4:
    st.header("Conexão Conta Azul")

    params = st.query_params
    code = params.get("code", None)
    state = params.get("state", None)

    # Processa retorno do callback OAuth2
    if code:
        with st.spinner("Autorizando..."):
            try:
                expected_state = st.session_state.get("oauth_state")
                if expected_state and state != expected_state:
                    st.warning("Aviso de segurança: state não corresponde. Continuando mesmo assim.")
                exchange_code(code, state)
                st.success("Autorizado com sucesso!")
                
                new_params = {k: v for k, v in st.query_params.items() if k not in ("code", "state")}
                st.query_params.clear()
                st.query_params.update(new_params)
                st.rerun()
            except Exception as e:
                st.error(f"Erro na autorização: {e}")
                st.exception(e)

    # Exibe estado da credencial
    creds = get_credentials()
    if creds and creds.get("access_token"):
        st.success(f"Conectado! Token válido até {creds['expires_at'].strftime('%d/%m/%Y %H:%M')}")
    else:
        auth_url, state = get_authorization_url()
        st.session_state["oauth_state"] = state
        st.link_button("Conectar com Conta Azul", url=auth_url)

    st.subheader("Configurar contas e categorias")

    # Botão para listar opções da API
    if st.button("Carregar contas financeiras e categorias"):
        st.session_state.contas = listar_contas_financeiras()
        st.session_state.categorias = listar_categorias_receita()

    # Dropdowns de seleção e salvamento no banco local
    if "contas" in st.session_state and "categorias" in st.session_state:
        opcoes_conta = {c["nome"]: c["id"] for c in st.session_state.contas}
        opcoes_cat = {c["nome"]: c["id"] for c in st.session_state.categorias}

        conta_selecionada = st.selectbox("Selecione a Conta Bancária:", list(opcoes_conta.keys()))
        cat_selecionada = st.selectbox("Selecione a Categoria de Receita:", list(opcoes_cat.keys()))

        if st.button("Salvar configuração", type="primary"):
            id_conta = opcoes_conta[conta_selecionada]
            id_cat = opcoes_cat[cat_selecionada]
            
            definir_configuracao(id_conta, id_cat)
            st.toast("✅ Configuração padrão do Conta Azul salva no banco com sucesso!", icon="💾")

# =============================================================================
# ABA 5: CONSULTA EM PRODUÇÃO API SOFIA (Alunos, Lançamentos e Boletos)
# =============================================================================
with tab_sofia:
    st.header("🔍 Teste e Inspeção Completa: Alunos, Lançamentos e Boletos")
    st.markdown("Inspecione todos os campos retornados pela API do Sofia de forma detalhada.")

    # Garante o logger local se não existir
    logger_tab = logging.getLogger("sofia_tab")

    base_url_sofia = os.getenv("SOFIA_BASE_URL", "")
    tenant_sofia = os.getenv("SOFIA_TENANT", "")
    usuario_sofia = os.getenv("SOFIA_USUARIO", "")
    senha_sofia = os.getenv("SOFIA_SENHA", "")

    st.info(f"Utilizando Tenant: **{tenant_sofia}** | URL: **{base_url_sofia}**")

    if st.button("Consultar Todos os Dados Detalhados", type="primary"):
        if not all([base_url_sofia, tenant_sofia, usuario_sofia, senha_sofia]):
            st.error("As credenciais do Sofia não estão totalmente definidas nas variáveis de ambiente (.env).")
        else:
            with st.spinner("Autenticando e extraindo todas as informações do Sofia..."):
                try:
                    api = SofiaAPI(base_url_sofia, tenant_sofia, usuario_sofia, senha_sofia)
                    api.autenticar()

                    # 1. Busca Alunos (exibindo todos os campos)
                    alunos = api.listar_alunos(pagina=1, tamanho=50)
                    st.subheader(f"1. Alunos Cadastrados ({len(alunos) if isinstance(alunos, list) else 0})")
                    
                    if alunos and isinstance(alunos, list):
                        # Mostra o DataFrame completo de alunos
                        df_alunos = pd.DataFrame(alunos)
                        st.dataframe(df_alunos, use_container_width=True)

                        # 2. Varredura para coletar Lançamentos e Boletos completos
                        todos_lancamentos = []
                        todos_boletos = []

                        progresso = st.progress(0)
                        total_alunos = len(alunos)

                        for i, aluno in enumerate(alunos):
                            id_aluno = aluno.get("codigo")
                            nome_aluno = aluno.get("nome")
                            if not id_aluno:
                                continue

                            try:
                                # Busca Lançamentos
                                lancamentos = api.obter_lancamentos(id_aluno)
                                if isinstance(lancamentos, list):
                                    for lanc in lancamentos:
                                        # Injeta dados de referência do aluno no lançamento
                                        lanc_completo = {
                                            "id_aluno": id_aluno,
                                            "aluno_nome": nome_aluno,
                                            **lanc  # Despeja todas as chaves do lançamento contábil
                                        }
                                        todos_lancamentos.append(lanc_completo)

                                        # Busca Boleto vinculado
                                        codigo_boleto = lanc.get("codigoBoleto")
                                        if codigo_boleto:
                                            try:
                                                boleto_info = api.obter_boleto(id_aluno, codigo_boleto)
                                                if isinstance(boleto_info, dict):
                                                    bol_dict = {
                                                        "id_aluno": id_aluno,
                                                        "aluno_nome": nome_aluno,
                                                        "codigo_boleto": codigo_boleto,
                                                        **boleto_info
                                                    }
                                                else:
                                                    bol_dict = {
                                                        "id_aluno": id_aluno,
                                                        "aluno_nome": nome_aluno,
                                                        "codigo_boleto": codigo_boleto,
                                                        "dados_brutos": str(boleto_info)
                                                    }
                                                todos_boletos.append(bol_dict)
                                            except Exception as bol_err:
                                                logger_tab.warning(f"Erro ao buscar boleto {codigo_boleto} para aluno {id_aluno}: {bol_err}")
                            except Exception as l_err:
                                logger_tab.warning(f"Erro ao buscar lançamentos para aluno {id_aluno}: {l_err}")

                            progresso.progress((i + 1) / total_alunos)

                        # 3. Exibe Lançamentos Contábeis Detalhados (Todos os campos)
                        st.subheader(f"2. Lançamentos Contábeis ({len(todos_lancamentos)})")
                        if todos_lancamentos:
                            df_lanc = pd.DataFrame(todos_lancamentos)
                            st.dataframe(df_lanc, use_container_width=True)
                        else:
                            st.info("Nenhum lançamento retornado para os alunos listados.")

                        # 4. Exibe Boletos Detalhados (Todos os campos)
                        st.subheader(f"3. Detalhes dos Boletos Vinculados ({len(todos_boletos)})")
                        if todos_boletos:
                            df_bol = pd.DataFrame(todos_boletos)
                            st.dataframe(df_bol, use_container_width=True)
                        else:
                            st.info("Nenhum boleto detalhado retornado para os lançamentos encontrados.")

                    else:
                        st.warning("Nenhum aluno retornado pela API.")

                except Exception as e:
                    st.error(f"Erro ao comunicar com a API do Sofia: {e}")
                    st.exception(e)