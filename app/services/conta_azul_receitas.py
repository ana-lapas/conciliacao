# services/conta_azul_receitas.py
import logging
import requests

# Importações de dependências internas do projeto
from .conta_azul import _get_valid_access_token
from .conta_azul_utils import (
    obter_configuracao,
    obter_ou_criar_categoria,
    obter_ou_criar_contato,
)

# Configuração do logger local para rastreamento de operações financeiras
logger = logging.getLogger(__name__)


def criar_receita_com_baixa(
    data_vencimento: str,
    valor: float,
    descricao: str,
    nome_cliente: str,
    data_pagamento: str = None,
    conta_id: str = None,
    categoria_id: str = None,
) -> str:
    """Executa o fluxo completo de lançamento financeiro no Conta Azul.

    1. Resolve as IDs de conta, categoria e contato (via banco local ou API).
    2. Envia o payload de criação de conta a receber (evento financeiro).
    3. Recupera o ID da parcela gerada assincronamente.
    4. Executa a liquidação/baixa imediata da parcela informada.

    Returns:
        str: O ID (UUID) da parcela que foi liquidadas no Conta Azul.
    """

    # --- PASSO 1: RESOLUÇÃO DE CONFIGURAÇÕES E DEPENDÊNCIAS DE ENTIDADES ---

    # Carrega as configurações globais cadastradas no banco de dados local
    config = obter_configuracao() or {}

    # Define a conta bancária e categoria: usa os argumentos explícitos ou recua para o banco
    id_conta_final = conta_id or config.get("conta_financeira_id")
    id_categoria_final = categoria_id or config.get("categoria_id")

    # A conta financeira é obrigatória para registrar o evento e a baixa
    if not id_conta_final:
        raise Exception(
            "Configure a conta financeira padrão antes de enviar receitas."
        )

    # Resolve o UUID da categoria (usa a informada ou busca/cria 'Recebimentos')
    categoria_uuid = id_categoria_final or obter_ou_criar_categoria()

    # Obtém ou cadastra o cliente (Pessoa Física) na API do Conta Azul para vincular a receita
    contato_uuid = obter_ou_criar_contato(nome_cliente)

    # Recupera ou renova o token OAuth2 de acesso à API
    token = _get_valid_access_token()

    # --- PASSO 2: CRIAÇÃO DO EVENTO FINANCEIRO (CONTA A RECEBER) ---

    # Monta a estrutura JSON esperada pela API v2 do Conta Azul
    payload_evento = {
        "data_competencia": data_vencimento,
        "valor": valor,
        "observacao": f"Recebimento de {nome_cliente}",
        "descricao": descricao,
        "contato": contato_uuid,
        "conta_financeira": id_conta_final,
        # O rateio vincula o valor total à categoria de receita informada
        "rateio": [{"id_categoria": categoria_uuid, "valor": valor}],
        "condicao_pagamento": {
            "parcelas": [
                {
                    "descricao": descricao,
                    "data_vencimento": data_vencimento,
                    "nota": f"Recebimento de {nome_cliente}",
                    "conta_financeira": id_conta_final,
                    "detalhe_valor": {
                        "valor_bruto": valor,
                        "multa": 0,
                        "juros": 0,
                        "desconto": 0,
                        "taxa": 0,
                    },
                }
            ]
        },
    }

    logger.info(
        f"Iniciando criação de conta a receber para {nome_cliente} - Valor: R$ {valor:.2f}"
    )

    # Chamada HTTP POST para registrar a conta a receber
    resp = requests.post(
        "https://api-v2.contaazul.com/v1/financeiro/eventos-financeiros/contas-a-receber",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload_evento,
    )
    resp.raise_for_status()

    # A API responde com um número de protocolo (processamento assíncrono no Conta Azul)
    protocolo = resp.json()
    logger.info(
        f"Evento financeiro registrado. Resposta/Protocolo: {protocolo}"
    )

    # --- PASSO 3: RECUPERAÇÃO DA PARCELA GERADA ---

    # Como a API não retorna o ID da parcela no POST, busca-se a parcela por filtros de data e valor
    parcela_id = _buscar_parcela_recem_criada(token, data_vencimento, valor)
    if not parcela_id:
        raise Exception(
            "O evento financeiro foi criado, mas a parcela correspondente não foi localizada para dar baixa."
        )

    # --- PASSO 4: BAIXA/LIQUIDAÇÃO DA PARCELA ---

    # Monta a estrutura de liquidação da parcela criada
    payload_baixa = {
        "data_pagamento": data_pagamento or data_vencimento,
        "composicao_valor": {
            "valor_bruto": valor,
            "multa": 0,
            "juros": 0,
            "desconto": 0,
            "taxa": 0,
        },
        "conta_financeira": id_conta_final,
        "metodo_pagamento": "PIX_PAGAMENTO_INSTANTANEO",
        "observacao": f"Recebimento de {nome_cliente}",
    }

    # Chamada HTTP POST para efetivar o pagamento na parcela do Conta Azul
    resp = requests.post(
        f"https://api-v2.contaazul.com/v1/financeiro/eventos-financeiros/parcelas/{parcela_id}/baixa",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload_baixa,
    )
    resp.raise_for_status()

    logger.info(
        f"Baixa realizada com sucesso no Conta Azul! Parcela UUID: {parcela_id}"
    )
    return parcela_id


def _buscar_parcela_recem_criada(
    token: str, data_vencimento: str, valor: float
) -> str | None:
    """Busca no Conta Azul a parcela recém-criada filtrando por valor e data de vencimento.

    Esta consulta funciona como um mecanismo de transição devido ao processamento
    assíncrono da API v2 do Conta Azul.
    """
    resp = requests.get(
        "https://api-v2.contaazul.com/v1/financeiro/eventos-financeiros/contas-a-receber/buscar",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "data_vencimento_de": data_vencimento,
            "data_vencimento_ate": data_vencimento,
            "valor_de": str(valor),
            "valor_ate": str(valor),
            "tamanho_pagina": 1,
        },
    )
    resp.raise_for_status()

    dados = resp.json()
    itens = dados.get("itens", [])

    # Retorna o ID da primeira parcela encontrada que atenda aos critérios
    if itens:
        return itens[0].get("id")

    return None