-- Áreas declaradas em outros cadastros, para comparar com a área desenhada: CCIR/SNCR e recibo do CAR.

ALTER TABLE servicos
  ADD COLUMN IF NOT EXISTS area_ccir_ha numeric(14, 4) CHECK (area_ccir_ha > 0),
  ADD COLUMN IF NOT EXISTS area_car_declarada_ha numeric(14, 4) CHECK (area_car_declarada_ha > 0);
