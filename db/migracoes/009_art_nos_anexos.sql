-- ART/RRT do responsável técnico passa a ser um tipo de anexo, para a conferência do projeto.

ALTER TABLE arquivos_dados DROP CONSTRAINT IF EXISTS arquivos_dados_categoria_check;
ALTER TABLE arquivos_dados ADD CONSTRAINT arquivos_dados_categoria_check CHECK (categoria IN (
  'insumo', 'matricula', 'ccir', 'itr_cib', 'recibo_car', 'certidao', 'art', 'outro'
));
