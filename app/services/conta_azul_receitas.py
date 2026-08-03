# services/conta_azul_receitas.py
import logging
import requests
from .conta_azul import _get_valid_access_token

logger = logging.getLogger(__name__)

def criar_receita_no_conta_azul(
    data_vencimento: str,
    valor: float,
    descricao: str,
    nome_cliente: str
) -> dict:
    """Cria uma entrada (conta a receber) no Conta Azul e loga cada passo."""
    logger.info("Tentando criar receita no Conta Azul...")
    logger.info(f"   Data vencimento: {data_vencimento}")
    logger.info(f"   Valor: {valor}")
    logger.info(f"   Descrição: {descricao}")
    logger.info(f"   Cliente: {nome_cliente}")

    try:
        token = _get_valid_access_token()
        logger.info(f"   Token obtido (primeiros 10 caracteres): {token[:10]}...")
    except Exception as e:
        logger.error(f"   Falha ao obter token: {e}")
        raise

    payload = {
        "dataVencimento": data_vencimento,
        "valor": valor,
        "descricao": descricao,
        "cliente": {"nome": nome_cliente}
    }
    logger.info(f"   Payload: {payload}")

    try:
        resposta = requests.post(
            "https://api-v2.contaazul.com/v1/financeiro/contas-a-receber",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            json=payload
        )
        logger.info(f"   Status HTTP: {resposta.status_code}")
        logger.info(f"   Resposta completa: {resposta.text}")

        resposta.raise_for_status()
        return resposta.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"   Erro HTTP: {e}")
        logger.error(f"   Corpo da resposta: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"   Erro inesperado: {e}")
        raise