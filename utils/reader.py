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
        "valor_titulo": (126, 139),
        "data_vencimento": (120, 126),
        "nome_pagador": (234, 274),
    }

    def __init__(self, file_path):
        self.file_path = file_path
        self.registros = []
        self.erros = []
        logging.info(f"Leitor inicializado para o arquivo: {file_path}")

    def _extrair_campo(self, linha, inicio, fim):
        return linha[inicio:fim].strip()

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
            valor_raw = self._extrair_campo(linha, *self.LAYOUT["valor_titulo"])
            valor_processado = float(valor_raw) / 100
            data_raw = self._extrair_campo(linha, *self.LAYOUT["data_vencimento"])
            data_formatada = datetime.strptime(data_raw, '%d%m%y').strftime('%Y-%m-%d')
            
            registro = {
                "nome_pagador": self._extrair_campo(linha, *self.LAYOUT["nome_pagador"]),
                "valor": valor_processado,
                "vencimento": data_formatada
            }
            self.registros.append(registro)
            
            # Log de acompanhamento para cada registro processado com sucesso
            logging.debug(f"Linha {num_linha}: Processado pagador {registro['nome_pagador']}")
            
        except ValueError:
            msg_erro = f"Linha {num_linha}: Erro ao converter valor '{valor_raw}'."
            logging.warning(msg_erro) # Log como aviso
            self.erros.append(msg_erro)