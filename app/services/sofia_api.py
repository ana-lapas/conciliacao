import requests
import logging
import os
import time
import random

class SofiaAPI:
    """
    Classe responsável pela comunicação segura, autenticação e consumo 
    dos endpoints da API REST do sistema educacional Sofia.
    """
    
    def __init__(self, base_url: str, tenant: str, usuario: str, senha: str):
        """
        Inicializa a instância da API Sofia.
        
        Args:
            base_url (str): URL base da API (ex: https://escolar.sophia.com.br/SGEW.API)
            tenant (str): Identificador do inquilino/instituição no path da API
            usuario (str): Credencial de acesso específica para integração
            senha (str): Senha correspondente à credencial de integração
        """
        self.base_url = base_url.rstrip('/')
        self.tenant = tenant.strip()
        self._usuario = str(usuario).strip()
        self._senha = str(senha).strip()
        self.token = None
        self.timeout = 30  # segundos
        self.session = None

        # Configuração de logger isolado para rastreabilidade de requisições
        self.logger = logging.getLogger(__name__)
    # ======================================================================
    # Métodos auxiliares internos
    # ======================================================================
    # _mask_token, _get_session, _request, _request_with_retry, close
    @staticmethod
    def _mask_token(token: str) -> str:
        if not token or len(token) < 4:
            return "***"
        return "*" * (len(token) - 4) + token[-4:]

    def autenticar(self) -> bool:
        """
        Realiza a autenticação na API do Sofia utilizando as credenciais fornecidas,
        capturando e armazenando o token de sessão retornado.
        
        Returns:
            bool: True se a autenticação for bem-sucedida.
            
        Raises:
            requests.exceptions.RequestException: Se houver falha de rede ou credenciais inválidas (401/400).
        """
        url = f"{self.base_url}/{self.tenant}/api/v1/Autenticacao"
        
        payload = {
            "usuario": self._usuario,
            "senha": self._senha
        }
        
        # Log seguro: nunca exiba senhas ou dados sensíveis nos logs de produção
        self.logger.info(f"Iniciando processo de autenticação na API para o usuário: {self._usuario}")
        
        try:
            # Envia requisição POST para obtenção do token
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                # O Sofia pode retornar o token como JSON ou texto puro dependendo do header Accept
                content_type = response.headers.get('content-type', '').lower()
                
                if 'application/json' in content_type:
                    data = response.json()
                    # Trata se o retorno for string pura dentro do JSON ou um dicionário estruturado
                    if isinstance(data, str):
                        self.token = data
                    elif isinstance(data, dict):
                        self.token = data.get("token") or data.get("access_token")
                else:
                    # Remove eventuais aspas extras do texto puro retornado
                    self.token = response.text.strip('"').strip()
                
                if not self.token:
                    raise ValueError("A resposta de autenticação foi bem-sucedida, mas o token retornou vazio.")
                
                self.logger.info(f"Autenticação OK. Token armazenado: {self._mask_token(self.token)}")
                return True
            
            else:
                # Log detalhado do erro devolvido pela API (ex: 401 Credenciais inválidas)
                self.logger.error(f"Erro de autenticação [{response.status_code}]: {response.text}")
                response.raise_for_status()
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Falha crítica de conexão ao tentar autenticar na API Sofia: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Erro inesperado durante o parsing do token: {e}")
            raise

    def _request(self, method: str, endpoint: str, params: dict = None, data: dict = None) -> requests.Response:
        """
        Método centralizador (wrapper) para todas as requisições HTTP da API.
        Garante a injeção automática de tokens, Content-Types padronizados e 
        mecanismo de re-tentativa (retry) caso o token expire.
        Uso básico:
            api = SofiaAPI("https://escolar.sophia.com.br/SGEW.API", "meutenant", "user", "pass")
            api.autenticar()
            alunos = api.listar_alunos(pagina=1, tamanho=50)
            lancamentos = api.obter_lancamentos(123)
        
        Args:
            method (str): Verbo HTTP (GET, POST, PUT, etc.)
            endpoint (str): Caminho relativo do recurso (ex: 'Alunos')
            params (dict, optional): Parâmetros de URL (Query Parameters)
            data (dict, optional): Corpo da requisição (Payload JSON)
            
        Returns:
            requests.Response: Objeto de resposta bruto do requests.
        """
        # Garante que possuímos um token válido antes de disparar qualquer chamada
        if not self.token:
            self.autenticar()

        url = f"{self.base_url}/{self.tenant}/api/v1/{endpoint.lstrip('/')}"
        
        # Cabeçalhos obrigatórios exigidos pela arquitetura REST do Sofia (token no header)
        headers = {
            'token': self.token, 
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        self.logger.debug(f"Executando requisição [{method.upper}] para o endpoint: {endpoint}")
        
        # Primeira tentativa de execução da requisição
        response = self._get_session().request(method, url, headers=headers, params=params, json=data, timeout=self.timeout)

        # Tratamento defensivo para token expirado ou invalidado pelo servidor (401 Unauthorized)
        if response.status_code == 401:
            self.logger.warning("Token expirado ou revogado detectado (401). Renovando credenciais...")
            
            # Força nova autenticação para atualizar o token interno
            self.autenticar()
            
            # Atualiza o cabeçalho com o novo token gerado
            headers['token'] = self.token
            
            # Repete a requisição original com o token renovado (Single Retry Pattern)
            response = self._get_session().request(method, url, headers=headers, params=params, json=data, timeout=self.timeout)

            self.logger.debug(f"Token renovado: {self._mask_token(self.token)}")

        # Lança exceção automaticamente para códigos de erro HTTP (4xx, 5xx)
        response.raise_for_status()
        return response
    
    def _request_with_retry(self, method: str, endpoint: str, params: dict = None,
                            data: dict = None, max_retries: int = 3) -> requests.Response:
        """
        Executa _request com política de retentativas Inteligente.
        Faz retry apenas para falhas de rede, timeout e erros de servidor (5xx/429).
        """
        last_exception = None
        for attempt in range(1, max_retries + 1):
            try:
                return self._request(method, endpoint, params, data)
            
            except requests.exceptions.HTTPError as e:
                # 1. Capturamos o status code retornado pelo servidor
                status = e.response.status_code if e.response is not None else None
                
                # 2. Regra de negócio: Só tentamos de novo se for limite de taxa (429) ou erro interno (5xx)
                if status not in [429] and not (status and 500 <= status < 600):
                    self.logger.error(f"Erro HTTP {status} (Sem Retry). Abortando: {e}")
                    raise e # Aborta imediatamente, pois o erro está no payload ou autorização
                
                last_exception = e
                
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # 3. Falhas puramente de infraestrutura/rede entram aqui
                last_exception = e

            # 4. Cálculo de Backoff Exponencial com Jitter (evita sobrecarga no servidor do Sophia)
            wait = (2 ** attempt) + random.uniform(0, 1)
            self.logger.warning(
                f"Tentativa {attempt}/{max_retries} falhou ({type(last_exception).__name__}). "
                f"Tentando novamente em {wait:.1f}s..."
            )
            time.sleep(wait)
            
        # 5. Se esgotar todas as tentativas, levanta a última exceção
        raise last_exception

    def _get_session(self):
        if self.session is None:
            self.session = requests.Session()
            self.logger.debug("Nova sessão HTTP criada.")
        return self.session

    def close(self):
        if self.session:
            self.session.close()
            self.logger.debug("Sessão HTTP fechada.")
    # ---------------------------------------------------------
    # Métodos Públicos de Domínio
    # ---------------------------------------------------------

    def listar_alunos(self, pagina: int = 1, tamanho: int = 50) -> list:
        """
        Retorna a lista paginada de alunos cadastrados no tenant.
        """
        response = self._request_with_retry("GET", "Alunos", params={'Pagina': pagina, 'TamanhoPagina': tamanho})
        return response.json()

    def obter_lancamentos(self, id_aluno: int) -> list:
        """
        Retorna os lançamentos financeiros vinculados a um aluno específico.
        """
        response = self._request_with_retry("GET", f"alunos/{id_aluno}/Lancamentos")
        return response.json()

    