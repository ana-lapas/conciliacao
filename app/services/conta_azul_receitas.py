# services/conta_azul_receitas.py
import logging
import requests
from .conta_azul import _get_valid_access_token

logger = logging.getLogger(__name__)

def criar_receita_no_conta_azul(
    data_vencimento: str,    # "YYYY-MM-DD"
    valor: float,
    descricao: str,
    nome_cliente: str
) -> dict:
    """Cria uma entrada (conta a receber) no Conta Azul."""
    token = _get_valid_access_token()
    resposta = requests.post(
        "https://api-v2.contaazul.com/v1/financeiro/contas-a-receber",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={
            "dataVencimento": data_vencimento,
            "valor": valor,
            "descricao": descricao,
            "cliente": {"nome": nome_cliente}
        }
    )
    resposta.raise_for_status()
    return resposta.json()