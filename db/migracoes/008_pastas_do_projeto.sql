-- Pastas do projeto: cada anexo fica numa pasta (plantas, memoriais, planilhas, imagens, relatórios, documentos)
-- e o projeto pode ser baixado como um .zip já organizado. Passam a ser aceitos DXF, DOCX, XLSX, JPG e PNG.

ALTER TABLE arquivos_dados DROP CONSTRAINT IF EXISTS arquivos_dados_tipo_check;
ALTER TABLE arquivos_dados ADD CONSTRAINT arquivos_dados_tipo_check CHECK (tipo IN (
  'CSV', 'TXT', 'GPX', 'KML', 'KMZ', 'SHP', 'DXF', 'PDF', 'DOCX', 'XLSX', 'JPG', 'PNG', 'OUTRO'
));

ALTER TABLE arquivos_dados
  ADD COLUMN IF NOT EXISTS pasta text NOT NULL DEFAULT 'documentos' CHECK (pasta IN (
    'plantas', 'memoriais', 'planilhas', 'imagens', 'relatorios', 'documentos'
  ));
