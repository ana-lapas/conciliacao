import logging
from datetime import datetime

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
            # Mapeamento conforme Manual Bradesco CNAB 400 (Pág 31-32)
            nosso_numero = linha[70:82].strip()  # Posições 071-082
            valor_pago = float(linha[253:266]) / 100 # Posições 254-266
            data_ocorrencia = linha[110:116] # Posições 111-116
            codigo_ocorrencia = linha[108:110] # Posições 109-110
            
            self.registros.append({
                'linha': num_linha,
                'nosso_numero': nosso_numero,
                'valor': valor_pago,
                'data': datetime.strptime(data_ocorrencia, '%d%m%y').strftime('%Y-%m-%d'),
                'ocorrencia': codigo_ocorrencia
            })
            
            logger.debug(f"Linha {num_linha} OK: N/N {nosso_numero} | Valor: {valor_pago}")
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Falha ao processar linha {num_linha}: dados corrompidos. Erro: {e}")