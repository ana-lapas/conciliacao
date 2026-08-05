import logging
from datetime import datetime
from app.services.remessa_reader import RemessaReader
import re

# Configuração do Logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RetornoReader:
    def __init__(self, arquivo_path):
        self.arquivo_path = arquivo_path
        self.registros = []

    def processar(self):
        logger.info(f"Iniciando leitura do arquivo: {self.arquivo_path}")
        
        try:
            with open(self.arquivo_path, 'r', encoding='latin-1') as f:
                for num_linha, linha in enumerate(f, 1):
                    # Validação de segurança: linha deve ter ao menos 400 bytes
                    if len(linha.strip()) < 400:
                        logger.warning(f"Linha {num_linha} ignorada: tamanho insuficiente.")
                        continue
                        
                    tipo_registro = linha[0] # Posição 001
                    
                    if tipo_registro == '1':
                        self._processar_detalhe(linha, num_linha)
                    elif tipo_registro == '0':
                        logger.info(f"Cabeçalho identificado na linha {num_linha}.")
                    elif tipo_registro == '9':
                        logger.info("Rodapé (trailler) alcançado.")
                    else:
                        logger.warning(f"Tipo de registro {tipo_registro} desconhecido na linha {num_linha}.")
                        
            logger.info(f"Processamento concluído. Registros processados: {len(self.registros)}")
            
        except FileNotFoundError:
            logger.error(f"Erro: O arquivo {self.arquivo_path} não foi localizado.")
        except Exception as e:
            logger.critical(f"Erro fatal durante o processamento: {e}")

    def _processar_detalhe(self, linha, num_linha):
        try:
            # 1. Nosso Número: 11 dígitos (posições 071 a 081)
            nosso_numero = linha[70:81].strip()
            # 2. DAC: posição 082
            dac = linha[81:82].strip()

            # 3. Código de Ocorrência (posições 109 a 110)
            codigo_ocorrencia = linha[108:110].strip()

            # 4. Data da Ocorrência (posições 111 a 116)
            data_str = linha[110:116].strip()
            if data_str == '' or data_str == '000000':
                raise ValueError("Data de ocorrência vazia ou zerada")
            data_ocorrencia = datetime.strptime(data_str, '%d%m%y').strftime('%Y-%m-%d')

            # 5. Valor Pago (posições 254 a 266)
            valor_pago = float(linha[253:266]) / 100

            # 6. Nome do Pagador (posições 235 a 274)
            nome_pagador = linha[234:274].strip()

            self.registros.append({
                'linha': num_linha,
                'nosso_numero': nosso_numero,
                'dac': dac,
                'valor': valor_pago,
                'data': data_ocorrencia,
                'ocorrencia': codigo_ocorrencia,
                'nome_pagador': nome_pagador
            })
        except Exception as e:
            logger.warning(f"Falha ao processar linha {num_linha}: {e}")