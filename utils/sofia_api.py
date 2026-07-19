import requests
import logging
import os

class SofiaAPI:
    def __init__(self, base_url, tenant, usuario, senha):
        self.base_url = base_url.rstrip('/')
        self.tenant = tenant
        self._usuario = usuario
        self._senha = senha
        self.token = None
        self.logger = logging.getLogger(__name__)

    def autenticar(self):
        url = f"{self.base_url}/{self.tenant}/api/v1/Autenticacao"
        # Certifique-se de que usuario e senha não possuem espaços em branco
        payload = {
            "usuario": str(self._usuario).strip(),
            "senha": str(self._senha).strip()
        }
        
        self.logger.info(f"Tentando autenticar na URL: {url} com usuário: {self._usuario}")
        
        response = requests.post(url, json=payload)
        
        if response.status_code == 401:
            self.logger.error(f"Resposta 401: {response.text}") # Isso mostrará o motivo detalhado
            
        

    def _request(self, method, endpoint, params=None, data=None):
        """Método privado único para todas as requisições (GET/PUT/POST)."""
        if not self.token:
            self.autenticar()

        # A URL agora utiliza a base correta encontrada no Swagger
        url = f"{self.base_url}/{self.tenant}/api/v1/{endpoint}"
        headers = {'token': self.token, 'Content-Type': 'application/json'}
        self.logger.debug(f"Headers enviados: {headers}")
        # Primeira tentativa
        response = requests.request(method, url, headers=headers, params=params, json=data)

        # Se 401, tenta renovar token e repetir a requisição
        if response.status_code == 401:
            self.autenticar()
            headers['token'] = self.token
            response = requests.request(method, url, headers=headers, params=params, json=data)

        response.raise_for_status()
        return response

    # Agora os métodos públicos ficam muito simples e limpos:
    def listar_alunos(self, pagina=1, tamanho=50):
        response = self._request("GET", "Alunos", params={'Pagina': pagina, 'TamanhoPagina': tamanho})
        return response.json()

    def obter_lancamentos(self, id_aluno):
        return self._request("GET", f"alunos/{id_aluno}/Lancamentos").json()

    def atualizar_responsavel_financeiro(self, id_aluno, novo_id_responsavel):
        """Agora utiliza a mesma lógica de segurança de token que os outros."""
        try:
            self._request("PUT", f"alunos/{id_aluno}/responsavelFinanceiro", data=novo_id_responsavel)
            return True
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Erro ao atualizar responsável: {e}")
            return False