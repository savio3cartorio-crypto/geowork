-- Nova categoria de camada: 'curvas' (curvas de nível geradas a partir de pontos cotados).
-- Cada curva é uma linha com geom MultiLineString e propriedades {"cota": <metros>}.
-- Aplicar em um banco que já tenha o schema.sql original:
--   psql -d geowork -f db/migracoes/002_curvas_de_nivel.sql

ALTER TABLE camadas_desenho DROP CONSTRAINT IF EXISTS camadas_desenho_categoria_check;
ALTER TABLE camadas_desenho ADD CONSTRAINT camadas_desenho_categoria_check
  CHECK (categoria IN ('perimetro', 'rl', 'app', 'hidro', 'edificacao', 'curvas'));
