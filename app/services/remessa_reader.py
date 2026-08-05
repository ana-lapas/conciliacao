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
        "nosso_numero": (70, 81),
        "dac_nosso_numero": (81, 82),
        "data_vencimento": (120, 126),
        "valor_titulo": (126, 139),
        "cpf_pagador": (220, 234),        
        "nome_pagador": (234, 274),
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
        Extrai as 4 mensagens do registro tipo 2, o nosso número e o DAC (posições 383 a 394).
        """
        # Posições 383 a 393: Nosso Número (11 posições)[cite: 1]
        nosso_numero_raw = linha[382:393].strip()
        
        # Posição 394: DAC do Nosso Número (1 posição - numérico ou 'P')[cite: 1]
        dac = linha[393:394].strip()

        return {
            "nosso_numero": nosso_numero_raw,
            "dac": dac,
            "mensagem1": linha[1:81].strip(),
            "mensagem2": linha[81:161].strip(),
            "mensagem3": linha[161:241].strip(),
            "mensagem4": linha[241:321].strip(),
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
            # Extração do Nosso Número (11 dígitos) e DAC (1 dígito)
            nosso_numero_raw = self._extrair_campo(linha, *self.LAYOUT["nosso_numero"])
            dac_raw = self._extrair_campo(linha, *self.LAYOUT["dac_nosso_numero"])
            
            # ← ADICIONE ESTA LINHA (preserva zeros à esquerda)
            nosso_numero = nosso_numero_raw

            # Conversão de Valor (Posições 127 a 139)
            valor_raw = self._extrair_campo(linha, *self.LAYOUT["valor_titulo"])
            valor_processado = float(valor_raw) / 100

            # Formatação de Data de Vencimento DDMMAA (Posições 121 a 126)
            data_raw = self._extrair_campo(linha, *self.LAYOUT["data_vencimento"])
            data_formatada = datetime.strptime(data_raw, '%d%m%y').strftime('%Y-%m-%d')

            # Normalização do CPF/CNPJ
            cpf_raw = self._extrair_campo(linha, *self.LAYOUT["cpf_pagador"])
            cpf = None
            if cpf_raw and cpf_raw.isdigit():
                if len(cpf_raw) in (11, 14):
                    cpf = cpf_raw

            # Normalização do Nome do Pagador (Posições 235 a 274)
            nome_raw = self._extrair_campo(linha, *self.LAYOUT["nome_pagador"])
            nome_pagador = " ".join(nome_raw.upper().split()) if nome_raw else ""

            registro = {
                "nosso_numero": nosso_numero,   # ← agora funciona
                "dac": dac_raw,
                "nome_pagador": nome_pagador,
                "cpf": cpf,
                "valor": valor_processado,
                "vencimento": data_formatada,
                "status": "PENDENTE_REGISTRO"
            }
            self.registros.append(registro)
            
            logging.debug(f"Linha {num_linha}: Processado pagador {registro['nome_pagador']} | N/N: {nosso_numero}-{dac_raw}")
            
        except ValueError as e:
            msg_erro = f"Linha {num_linha}: Erro ao processar dados da linha. Detalhe: {e}"
            logging.warning(msg_erro)
            self.erros.append(msg_erro)

    # Função utilitária externa corrigida e alinhada ao escopo global
    def carregar_remessa(caminho_rem):
        if not caminho_rem:
            return {}
        reader = RemessaReader(caminho_rem)
        reader.processar()
        return {reg['nosso_numero']: reg for reg in reader.registros}