import logging
from datetime import datetime
from app.services.remessa_reader import RemessaReader

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
            # Posições conforme Manual Bradesco Págs. 31-32
            nosso_numero_raw = linha[70:82].strip()
            nosso_numero = nosso_numero_raw.lstrip('0')  # Limpa zeros à esquerda (ex: "94851")
            # DAC do Nosso Número: Posição 83 (Índice Python 82:83)
            dac = linha[82:83].strip()

            # Código de Ocorrência: Posições 109 a 110 (Índice Python 108:110)
            codigo_ocorrencia = linha[108:110].strip()

            # Data da Ocorrência (Liquidação): Posições 111 a 116 - DDMMAA (Índice Python 109:115)
            data_ocorrencia_str = linha[109:115].strip()
            data_ocorrencia = datetime.strptime(data_ocorrencia_str, '%d%m%y').strftime('%Y-%m-%d')

            # Valor Pago: Posições 254 a 266 (Índice Python 253:266)
            valor_pago = float(linha[253:266]) / 100

            self.registros.append({
                'linha': num_linha,
                'nosso_numero': nosso_numero,
                'dac': dac,
                'valor': valor_pago,
                'data': data_ocorrencia,
                'ocorrencia': codigo_ocorrencia,
                'nome_pagador': None  # No CNAB 400 Retorno, o nome é obtido via De-Para com a Remessa
            })
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Falha ao processar linha de detalhe {num_linha}: {e}")