# services/conta_azul_receitas.py
import logging
import requests
from .conta_azul import _get_valid_access_token
from .conta_azul_utils import obter_configuracao, obter_ou_criar_contato

logger = logging.getLogger(__name__)

def criar_receita_com_baixa(data_vencimento: str, valor: float, descricao: str,
                            nome_cliente: str, data_pagamento: str = None):
    """Fluxo completo: cria conta a receber, obtém parcela e baixa."""
    config = obter_configuracao()
    if not config or not config.get("conta_financeira"):
        raise Exception("Configure a conta financeira antes de enviar receitas.")

    categoria_uuid = obter_ou_criar_categoria()          # obtém/cria categoria "Recebimentos"
    contato_uuid = obter_ou_criar_contato(nome_cliente)  # obtém/cria contato
    token = _get_valid_access_token()

    # ---------- 1. Criar evento financeiro ----------
    payload_evento = {
        "data_competencia": data_vencimento,
        "valor": valor,
        "observacao": f"Recebimento de {nome_cliente}",
        "descricao": descricao,
        "contato": contato_uuid,
        "conta_financeira": config["conta_financeira"],
        "rateio": [                                   # ← array com 1 item
            {
                "id_categoria": categoria_uuid,       # ← nome correto do campo
                "valor": valor
                # rateio_centro_custo é opcional
            }
        ],
        "condicao_pagamento": {
            "parcelas": [
                {
                    "descricao": descricao,           # obrigatório
                    "data_vencimento": data_vencimento,
                    "nota": f"Recebimento de {nome_cliente}",  # obrigatório
                    "conta_financeira": config["conta_financeira"],
                    "detalhe_valor": {                # obrigatório dentro da parcela
                        "valor_bruto": valor,
                        "multa": 0,
                        "juros": 0,
                        "desconto": 0,
                        "taxa": 0
                    }
                    # "metodo_pagamento" é opcional
                }
            ]
        }
    }

    logger.info("Criando evento financeiro...")
    resp = requests.post(
        "https://api-v2.contaazul.com/v1/financeiro/eventos-financeiros/contas-a-receber",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload_evento
    )
    resp.raise_for_status()
    protocolo = resp.json()  # ex: {"protocolo": "...", "status": "SUCCESS", "data_criacao": "..."}
    logger.info(f"Evento criado. Protocolo: {protocolo}")

    # O ID do evento não é retornado imediatamente – precisamos buscá-lo via protocolo?
    # A documentação mostra que o POST retorna apenas um protocolo (processamento assíncrono).
    # Para simplificar, faremos uma consulta das parcelas por protocolo? Infelizmente não há endpoint.
    # Uma alternativa segura é usar o endpoint de busca de receitas com filtro pelo valor e data.
    # Vamos implementar uma busca simples: GET /v1/financeiro/eventos-financeiros/contas-a-receber/buscar
    # com a data de vencimento e valor, e pegar o primeiro resultado.
    # Essa é uma abordagem paliativa até que a API síncrona seja suportada.
    parcela_id = _buscar_parcela_recem_criada(token, data_vencimento, valor)
    if not parcela_id:
        raise Exception("Não foi possível localizar a parcela criada.")

    # ---------- 2. Baixar a parcela ----------
    payload_baixa = {
        "data_pagamento": data_pagamento or data_vencimento,
        "composicao_valor": {
            "valor_bruto": valor,
            "multa": 0,
            "juros": 0,
            "desconto": 0,
            "taxa": 0
        },
        "conta_financeira": config["conta_financeira"],
        "metodo_pagamento": "PIX_PAGAMENTO_INSTANTANEO",
        "observacao": f"Recebimento de {nome_cliente}"
    }
    resp = requests.post(
        f"https://api-v2.contaazul.com/v1/financeiro/eventos-financeiros/parcelas/{parcela_id}/baixa",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload_baixa
    )
    resp.raise_for_status()
    logger.info(f"Baixa realizada com sucesso. Parcela: {parcela_id}")
    return parcela_id


def _buscar_parcela_recem_criada(token: str, data_vencimento: str, valor: float) -> str | None:
    """
    Busca a parcela recém-criada usando o endpoint de busca de receitas.
    Retorna o ID da parcela ou None.
    """
    resp = requests.get(
        "https://api-v2.contaazul.com/v1/financeiro/eventos-financeiros/contas-a-receber/buscar",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "data_vencimento_de": data_vencimento,
            "data_vencimento_ate": data_vencimento,
            "valor_de": str(valor),
            "valor_ate": str(valor),
            "tamanho_pagina": 1
        }
    )
    resp.raise_for_status()
    dados = resp.json()
    itens = dados.get("itens", [])
    if itens:
        # Cada item tem a estrutura de parcela; o campo "id" é o ID da parcela
        return itens[0].get("id")
    return None