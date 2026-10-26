-- Acompanhamento do CAR depois da inscrição (situações do Módulo de Análise do SICAR-SP).
-- A situação atual fica no serviço; car_situacoes guarda o histórico e car_pendencias o que o órgão apontou.

ALTER TABLE servicos
  ADD COLUMN IF NOT EXISTS car_situacao text NOT NULL DEFAULT 'nao_inscrito' CHECK (car_situacao IN (
    'nao_inscrito', 'inscrito', 'em_analise', 'aguarda_complementacao', 'em_alteracao',
    'aprovado', 'aprovado_dispensa_rl', 'invalido', 'cancelado'
  )),
  ADD COLUMN IF NOT EXISTS car_situacao_data date,
  ADD COLUMN IF NOT EXISTS car_prazo date;   -- prazo para complementar, quando 'aguarda_complementacao'

CREATE TABLE IF NOT EXISTS car_situacoes (
  id            bigserial PRIMARY KEY,
  servico_id    bigint      NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
  situacao      text        NOT NULL,
  data          date        NOT NULL,
  prazo         date,
  observacao    text,
  registrado_em timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS car_situacoes_servico ON car_situacoes (servico_id, data);

CREATE TABLE IF NOT EXISTS car_pendencias (
  id           bigserial PRIMARY KEY,
  servico_id   bigint      NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
  descricao    text        NOT NULL CHECK (length(trim(descricao)) > 0),
  criada_em    date        NOT NULL DEFAULT current_date,
  resolvida_em date
);
CREATE INDEX IF NOT EXISTS car_pendencias_servico ON car_pendencias (servico_id);
CREATE INDEX IF NOT EXISTS servicos_car_prazo ON servicos (car_prazo) WHERE car_prazo IS NOT NULL;
