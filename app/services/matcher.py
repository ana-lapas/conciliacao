# services/matcher.py
"""
Módulo de Conciliação Financeira Automatizada.

Este módulo é o coração da integração entre o Retorno Bancário (CNAB 400 Bradesco),
o histórico de Remessas, o banco de dados do Sophia ERP e a API do Conta Azul.

FLUXO EXECUTIVO:
1. Lê retornos em aberto ('PENDENTE' ou 'PENDENTE_REVISAO').
2. Realiza o De-Para com o arquivo de Remessa usando o Nosso Número como chave.
3. Localiza o responsável financeiro no cache local (student_responsible).
4. Valida o título no Sofia ERP (via API) batendo número do boleto, valor e vencimento.
5. Registra o match definitivo e envia a liquidação ao Conta Azul.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.sofia_api import SofiaAPI
from app.services.cache_sync import SessionLocal 
from app.services.conta_azul_receitas import criar_receita_com_baixa

logger = logging.getLogger(__name__)

# ==============================================================================
# FUNÇÕES AUXILIARES DE SANITIZAÇÃO DE DADOS
# ==============================================================================

def normalizar_nosso_numero(nn: str) -> str:
    """
    Remove zeros à esquerda e caracteres não numéricos.
    
    Motivo: O Bradesco grava no arquivo remessa/retorno o número '000000094851' (11 dígitos
    com pad de zeros), enquanto a API REST do Sofia e as interfaces de banco salvam '94851'.
    Esta função iguala ambas as pontas para permitir comparação direta de strings.
    """
    if not nn:
        return ""
    # Remove qualquer caractere que não seja dígito numérico (ex: traços ou pontos)
    apenas_numeros = re.sub(r'\D', '', str(nn))
    # lstrip('0') remove os zeros do início sem alterar zeros internos (ex: '00101' vira '101')
    return apenas_numeros.lstrip('0')


def normalizar_texto(texto: str) -> str:
    """
    Padroniza strings de texto para comparação segura em caixa alta.
    
    Realiza o trim de bordas, converte para UPPER e substitui múltiplos espaços em
    branco internos por um único espaço. Evita que 'MARIA  SILVA' seja diferente de 'MARIA SILVA'.
    """
    if not texto:
        return ""
    return " ".join(texto.strip().upper().split())


# ==============================================================================
# CONSULTAS DE BANCO DE DADOS E LOOKUPS DE DE-PARA
# ==============================================================================

def buscar_responsaveis(session: Session, nome: Optional[str] = None, cpf: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Localiza os responsáveis financeiros na tabela local `student_responsible`.
    
    Estratégia de Busca:
    1. Prioridade pelo CPF (limpo de pontuação).
    2. Fallback pelo Nome Completo ou Parcial (Primeiro Nome + Sobrenome).
    """
    # 1. Tentativa de match por CPF (100% de precisão)
    if cpf:
        cpf_limpo = re.sub(r'\D', '', str(cpf))
        query = text("""
            SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
            FROM student_responsible sr
            WHERE REPLACE(REPLACE(REPLACE(sr.cpf, '.', ''), '-', ''), ' ', '') = :cpf
              AND sr.responsavel_financeiro = true
        """)
        rows = session.execute(query, {"cpf": cpf_limpo}).fetchall()
        if rows:
            return [dict(row._mapping) for row in rows]

    # 2. Tentativa de match por Nome (tolerante a divergências de sobrenome)
    if nome:
        nome_limpo = normalizar_texto(nome)
        partes = nome_limpo.split()
        primeiro_nome = partes[0] if partes else nome_limpo

        query = text("""
            SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
            FROM student_responsible sr
            WHERE (UPPER(sr.nome) LIKE :nome_full OR UPPER(sr.nome) LIKE :nome_parcial)
              AND sr.responsavel_financeiro = true
        """)
        params = {
            "nome_full": f"%{nome_limpo}%",
            "nome_parcial": f"%{primeiro_nome}%"
        }
        rows = session.execute(query, params).fetchall()
        return [dict(row._mapping) for row in rows]

    return []


def obter_remessa_por_nosso_numero(session: Session, nosso_numero: str):
    """
    Realiza a busca da Remessa original no banco pelo Nosso Número.
    
    Utiliza a função `LTRIM(nosso_numero, '0')` no SQL do PostgreSQL para garantir que
    registros salvos com ou sem zeros à esquerda sejam encontrados corretamente.
    """
    nn_limpo = normalizar_nosso_numero(nosso_numero)
    query = text("""
        SELECT * FROM remessa 
        WHERE LTRIM(nosso_numero, '0') = :nn_limpo 
           OR nosso_numero = :nn_raw
        LIMIT 1
    """)
    return session.execute(query, {"nn_limpo": nn_limpo, "nn_raw": nosso_numero}).first()


def obter_descricao_pagamento(session: Session, nosso_numero: str) -> Optional[str]:
    """
    Recupera as mensagens descritivas do Registro Tipo 2 (opcional) enviado na Remessa.
    Concatena as 4 mensagens em uma única linha para compor o histórico no Conta Azul.
    """
    nn_limpo = normalizar_nosso_numero(nosso_numero)
    row = session.execute(
        text("""
            SELECT mensagem1, mensagem2, mensagem3, mensagem4 
            FROM remessa_mensagem 
            WHERE LTRIM(nosso_numero, '0') = :nn_limpo OR nosso_numero = :nn_raw
            LIMIT 1
        """),
        {"nn_limpo": nn_limpo, "nn_raw": nosso_numero}
    ).first()

    if not row:
        return None
    
    # Junta apenas as linhas de mensagens que não estiverem vazias
    partes = [row.mensagem1, row.mensagem2, row.mensagem3, row.mensagem4]
    return " | ".join(p.strip() for p in partes if p and p.strip())


# ==============================================================================
# LÓGICA DE MATCHING FINANCEIRO (SOFIA API)
# ==============================================================================

def encontrar_lancamento(
    api: SofiaAPI, student_id: int, nosso_numero: str,
    valor_pago: float, data_pagamento: Any
) -> Optional[Dict[str, Any]]:
    """
    Consulta a API do Sofia para localizar o lançamento aberto que bate com o pagamento.
    
    Critérios de Validação:
    - O lançamento deve estar em aberto (`recebido == 0`).
    - Prioridade 1: Match exato pelo `numeroBoleto`.
    - Prioridade 2: Match por proximidade de valor (tolerância R$ 0,01) e data de vencimento (janela de 5 dias).
    """
    try:
        # Busca lançamentos vinculados ao ID do aluno no Sofia
        lancamentos = api.obter_lancamentos(student_id)
    except Exception as e:
        logger.error(f"Erro ao obter lançamentos do aluno {student_id}: {e}")
        return None

    # Normaliza o tipo do parâmetro data_pagamento para garantir compatibilidade
    if isinstance(data_pagamento, datetime):
        data_pgto = data_pagamento
    else:
        data_pgto = datetime.strptime(str(data_pagamento)[:10], '%Y-%m-%d')

    nn_limpo = normalizar_nosso_numero(nosso_numero)
    melhor_lancamento = None
    melhor_diferenca = timedelta.max

    for lanc in lancamentos:
        # Descarta parcelas que já foram marcadas como recebidas
        if lanc.get("recebido") != 0:
            continue

        # 1. Teste do Nosso Número no boleto (Match Direto)
        num_boleto_api = normalizar_nosso_numero(str(lanc.get("numeroBoleto", "")))
        if num_boleto_api and num_boleto_api == nn_limpo:
            return lanc

        # 2. Teste do Valor Previsto vs Valor Pago
        try:
            valor_previsto = float(lanc.get("valorPrevisto", 0))
        except (TypeError, ValueError):
            continue

        # Tolerância de 1 centavo para variações de arredondamento
        if abs(valor_previsto - valor_pago) > 0.01:
            continue

        # 3. Teste de Proximidade da Data de Vencimento (Tolerância para liquidação com atraso/antecipação)
        try:
            data_venc = datetime.strptime(str(lanc.get("dataVencimento", ""))[:10], '%Y-%m-%d')
        except (ValueError, TypeError):
            continue

        diferenca = abs((data_venc - data_pgto).days)
        if diferenca <= 5 and diferenca < melhor_diferenca:
            melhor_diferenca = diferenca
            melhor_lancamento = lanc

    return melhor_lancamento


# ==============================================================================
# GRAVAÇÃO DE RESULTADOS E INTEGRAÇÃO EXTERNA
# ==============================================================================

def registrar_conciliacao(session: Session, ret, rem, resp: dict, lanc: dict) -> None:
    """
    Efetiva a conciliação: atualiza o status local para CONCILIADO e envia a receita baixada para o Conta Azul.
    """
    nome = normalizar_texto(resp.get('nome')) or "NÃO INFORMADO"

    # Verifica se já existe um registro na tabela payment_match para evitar duplicações
    existente = session.execute(
        text("SELECT id FROM payment_match WHERE retorno_id = :rid"),
        {"rid": ret.id}
    ).first()

    params_match = {
        "remid": rem.id if rem else None,
        "sid": resp["student_id"],
        "srid": resp["student_responsible_id"],
        "nome_resp": nome,
        "cpf_resp": resp.get("cpf"),
        "lcod": lanc["codigo"],
        "vpago": float(ret.valor_pago),
        "vprev": float(lanc.get("valorPrevisto", ret.valor_pago)),
        "dpgto": ret.data_pagamento,
        "dvenc": lanc.get("dataVencimento", ret.data_pagamento),
        "rid": ret.id
    }

    if existente:
        params_match["id"] = existente.id
        session.execute(
            text("""
                UPDATE payment_match SET
                    remessa_id = :remid, student_id = :sid, student_responsible_id = :srid,
                    nome_responsavel = :nome_resp, cpf_responsavel = :cpf_resp,
                    lancamento_codigo = :lcod, valor_pago = :vpago, valor_previsto = :vprev,
                    data_pagamento = :dpgto, data_vencimento = :dvenc, status = 'CONCILIADO', mensagem = NULL
                WHERE id = :id
            """), params_match
        )
    else:
        session.execute(
            text("""
                INSERT INTO payment_match (
                    retorno_id, remessa_id, student_id, student_responsible_id,
                    nome_responsavel, cpf_responsavel, lancamento_codigo, valor_pago,
                    valor_previsto, data_pagamento, data_vencimento, status
                ) VALUES (
                    :rid, :remid, :sid, :srid, :nome_resp, :cpf_resp,
                    :lcod, :vpago, :vprev, :dpgto, :dvenc, 'CONCILIADO'
                )
            """), params_match
        )

    # Atualiza o ciclo de vida das tabelas filhas para CONCILIADO e PAGO
    session.execute(text("UPDATE retorno SET status = 'CONCILIADO' WHERE id = :rid"), {"rid": ret.id})
    if rem:
        session.execute(text("UPDATE remessa SET status = 'PAGO' WHERE id = :remid"), {"remid": rem.id})

    # Disparo de baixa imediata na API do Conta Azul
    descricao_remessa = obter_descricao_pagamento(session, ret.nosso_numero)
    try:
        data_pgto_str = ret.data_pagamento.strftime('%Y-%m-%d') if isinstance(ret.data_pagamento, datetime) else str(ret.data_pagamento)[:10]
        descricao_completa = f"{descricao_remessa or 'Mensalidade Escolar'} - Resp: {nome}"
        
        # Cria a conta a receber e realiza o POST de baixa no ERP Conta Azul
        parcela_id = criar_receita_com_baixa(
            data_vencimento=data_pgto_str,
            valor=float(ret.valor_pago),
            descricao=descricao_completa,
            nome_cliente=nome,
            data_pagamento=data_pgto_str
        )
        
        # Salva o UUID da parcela retornada pelo Conta Azul para rastreabilidade
        session.execute(
            text("UPDATE payment_match SET conta_azul_receita_id = :caid WHERE retorno_id = :rid"),
            {"caid": parcela_id, "rid": ret.id}
        )
        logger.info(f"Retorno {ret.id} CONCILIADO e enviado ao Conta Azul (ID Parcela: {parcela_id})")
        
    except Exception as e:
        # Tratamento Defensivo: Em caso de falha da API externa, o log é gravado sem derrubar a transação local
        logger.error(f"Falha na integração Conta Azul para o retorno {ret.id}: {e}")
        session.execute(
            text("UPDATE payment_match SET mensagem = :msg WHERE retorno_id = :rid"),
            {"msg": f"Erro Conta Azul: {str(e)[:200]}", "rid": ret.id}
        )


def registrar_pendente_revisao(session: Session, ret, mensagem: str, nome_pagador: Optional[str] = None) -> None:
    """
    Registra pendência de revisão manual caso o pagamento não consiga ser reconciliado sozinho.
    """
    nome = normalizar_texto(nome_pagador) if nome_pagador else f"PAGADOR NÃO IDENTIFICADO – Nosso Número {ret.nosso_numero}"

    existente = session.execute(
        text("SELECT id FROM payment_match WHERE retorno_id = :rid"),
        {"rid": ret.id}
    ).first()

    if existente:
        session.execute(
            text("UPDATE payment_match SET nome_responsavel = :nome, status = 'PENDENTE_REVISAO', mensagem = :msg WHERE id = :id"),
            {"nome": nome, "msg": mensagem, "id": existente.id}
        )
    else:
        session.execute(
            text("""
                INSERT INTO payment_match (retorno_id, nome_responsavel, status, mensagem)
                VALUES (:rid, :nome, 'PENDENTE_REVISAO', :msg)
            """),
            {"rid": ret.id, "nome": nome, "msg": mensagem}
        )

    session.execute(text("UPDATE retorno SET status = 'PENDENTE_REVISAO' WHERE id = :rid"), {"rid": ret.id})


# ==============================================================================
# FUNÇÃO PRINCIPAL DE ORQUESTRAÇÃO
# ==============================================================================

def conciliar_retorno(api: SofiaAPI) -> None:
    """
    Ponto de entrada principal para a execução em lote da conciliação.
    Itera sobre todos os retornos pendentes e executa a esteira de validação.
    """
    session = SessionLocal()
    try:
        # Carrega os registros pendentes de processamento
        retornos = session.execute(
            text("SELECT * FROM retorno WHERE status IN ('PENDENTE', 'PENDENTE_REVISAO')")
        ).fetchall()

        if not retornos:
            logger.info("Nenhum retorno pendente para conciliar.")
            return

        logger.info(f"Iniciando conciliação de {len(retornos)} retornos...")

        for ret in retornos:
            # PASSO 1: Busca a Remessa correspondente usando o Nosso Número
            rem = obter_remessa_por_nosso_numero(session, ret.nosso_numero)

            if not rem:
                registrar_pendente_revisao(
                    session, ret,
                    f"Remessa não localizada para o Nosso Número {ret.nosso_numero}."
                )
                continue

            nome_busca = normalizar_texto(rem.nome_pagador)
            cpf_busca = rem.cpf_pagador

            # PASSO 2: Valida se a remessa possui o nome do pagador
            if not nome_busca or nome_busca.isdigit():
                registrar_pendente_revisao(
                    session, ret,
                    "Nome do pagador inválido na remessa.",
                    nome_pagador=None
                )
                continue

            # PASSO 3: Localiza os responsáveis financeiros cadastrados no cache do banco
            resp_rows = buscar_responsaveis(session, nome=nome_busca, cpf=cpf_busca)
            if not resp_rows:
                registrar_pendente_revisao(
                    session, ret,
                    f"Responsável '{nome_busca}' não localizado no banco local.",
                    nome_pagador=nome_busca
                )
                continue

            # PASSO 4: Consulta a API do Sofia para bater o lançamento em aberto
            match_resp = None
            match_lanc = None
            for resp in resp_rows:
                lanc = encontrar_lancamento(
                    api, resp["student_id"], ret.nosso_numero,
                    float(ret.valor_pago), ret.data_pagamento
                )
                if lanc:
                    match_resp = resp
                    match_lanc = lanc
                    break

            # PASSO 5: Conclui a conciliação ou registra pendência para análise manual na tela
            if match_resp and match_lanc:
                registrar_conciliacao(session, ret, rem, match_resp, match_lanc)
            else:
                registrar_pendente_revisao(
                    session, ret,
                    "Lançamento financeiro não localizado no Sofia.",
                    nome_pagador=nome_busca
                )

        # Efetiva todas as operações no banco PostgreSQL
        session.commit()
        logger.info("Processo de conciliação finalizado com sucesso.")
        
    except Exception:
        # Em caso de erro não tratado, desfaz as operações para manter a integridade dos dados
        session.rollback()
        logger.exception("Erro crítico durante a conciliação. Rollback executado.")
        raise
    finally:
        session.close()