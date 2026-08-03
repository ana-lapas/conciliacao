import os
import logging
import pandas as pd 
from supabase import create_client
from datetime import datetime

# Configuração básica do log: exibe a hora, o nível (INFO/ERROR) e a mensagem
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class RemessaReader:
    """
    Classe para processar arquivos de remessa bancária no padrão CNAB 400.
    """
    
    LAYOUT = {
        "nosso_numero": (71, 82),
        "valor_titulo": (126, 139),
        "data_vencimento": (120, 126),
        "nome_pagador": (234, 274),
        "cpf_pagador": (220, 234),
    }

    def __init__(self, file_path):
        self.file_path = file_path
        self.registros = []
        self.erros = []
        self.mensagens = []
        logging.info(f"Leitor inicializado para o arquivo: {file_path}")

    def _extrair_campo(self, linha, inicio, fim):
        return linha[inicio:fim].strip()

    def _processar_mensagem(self, linha):
        """
        Extrai as 4 mensagens do registro tipo 2 e o nosso número.
        Layout CNAB 400 – registro tipo 2:
          Posições 002 a 081 → mensagem 1
          Posições 082 a 161 → mensagem 2
          Posições 162 a 241 → mensagem 3
          Posições 242 a 321 → mensagem 4
          Posições 383 a 394 → nosso número (com dígito)
        """
        return {
            "nosso_numero": linha[382:394].strip(),
            "mensagem1": linha[2:82].strip(),
            "mensagem2": linha[82:162].strip(),
            "mensagem3": linha[162:242].strip(),
            "mensagem4": linha[242:322].strip(),
        }

    def processar(self):
        logging.info("Iniciando processamento do arquivo...")
        try:
            with open(self.file_path, 'r', encoding='latin-1') as f:
                for num_linha, linha in enumerate(f, 1):
                    if not linha.strip():
                        continue
                    
                    tipo_registro = linha[0]
                    
                    if tipo_registro == '1':
                        self._processar_registro_tipo_1(linha, num_linha)
                    elif tipo_registro == '2':
                        self.mensagens.append(self._processar_mensagem(linha))
            
            logging.info(f"Processamento concluído. Registros válidos: {len(self.registros)}. Erros: {len(self.erros)}")
        
        except FileNotFoundError:
            logging.error(f"Arquivo não encontrado em: {self.file_path}")
            self.erros.append("Erro: Arquivo não encontrado.")
        except Exception as e:
            logging.error(f"Erro crítico no processamento: {str(e)}")
            self.erros.append(f"Erro inesperado ao ler arquivo: {str(e)}")
            
        return self.registros

    def _processar_registro_tipo_1(self, linha, num_linha):
        """Processa e valida internamente um registro do tipo 1."""
        try:
            nosso_numero = self._extrair_campo(linha, *self.LAYOUT["nosso_numero"])
            valor_raw = self._extrair_campo(linha, *self.LAYOUT["valor_titulo"])
            valor_processado = float(valor_raw) / 100
            data_raw = self._extrair_campo(linha, *self.LAYOUT["data_vencimento"])
            data_formatada = datetime.strptime(data_raw, '%d%m%y').strftime('%Y-%m-%d')
            # Normalização do CPF/CNPJ (preserva zeros à esquerda)
            cpf_raw = self._extrair_campo(linha, *self.LAYOUT["cpf_pagador"])
            cpf = None
            if cpf_raw and cpf_raw.isdigit():
                if len(cpf_raw) == 11 or len(cpf_raw) == 14:
                    cpf = cpf_raw

            registro = {
                "nosso_numero": nosso_numero,
                "nome_pagador": self._extrair_campo(linha, *self.LAYOUT["nome_pagador"]),
                "cpf": cpf,
                "valor": valor_processado,
                "vencimento": data_formatada,
                "status": "PENDENTE_REGISTRO"
            }
            self.registros.append(registro)
            
            # Log de acompanhamento para cada registro processado com sucesso
            logging.debug(f"Linha {num_linha}: Processado pagador {registro['nome_pagador']}")
            
        except ValueError:
            msg_erro = f"Linha {num_linha}: Erro ao converter valor '{valor_raw}'."
            logging.warning(msg_erro) # Log como aviso
            self.erros.append(msg_erro)
            
    def carregar_remessa(caminho_rem):
        if not caminho_rem:
            return {}
        reader = RemessaReader(caminho_rem)
        reader.processar()
        return {reg['nosso_numero']: reg for reg in reader.registros}