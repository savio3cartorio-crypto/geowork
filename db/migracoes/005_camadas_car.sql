-- Camadas no padrão do CAR (Módulo de Cadastro do SICAR, etapa Geo): sede ou ponto de referência,
-- cobertura do solo (vegetação nativa, área consolidada, pousio), servidão administrativa, uso restrito
-- e os tipos de Reserva Legal. O serviço passa a guardar a área da matrícula e o percentual mínimo de RL.

ALTER TABLE camadas_desenho DROP CONSTRAINT IF EXISTS camadas_desenho_categoria_check;
ALTER TABLE camadas_desenho ADD CONSTRAINT camadas_desenho_categoria_check
  CHECK (categoria IN (
    'perimetro', 'sede',
    'vegetacao_nativa', 'area_consolidada', 'pousio',
    'servidao', 'app', 'uso_restrito',
    'rl', 'rl_averbada', 'rl_aprovada', 'rl_compensacao',
    'hidro', 'edificacao', 'curvas'
  ));

ALTER TABLE servicos
  ADD COLUMN IF NOT EXISTS area_documental_ha numeric(14, 4) CHECK (area_documental_ha > 0),
  ADD COLUMN IF NOT EXISTS rl_percentual      numeric(5, 2)  CHECK (rl_percentual > 0 AND rl_percentual <= 100);
