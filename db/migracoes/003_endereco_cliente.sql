-- Endereço do cliente (padronizado pela API: CEP xxxxx-xxx, UF com duas letras maiúsculas).
-- Aplicar depois de 002: psql -d geowork -f db/migracoes/003_endereco_cliente.sql

ALTER TABLE clientes
  ADD COLUMN IF NOT EXISTS cep         varchar(9),
  ADD COLUMN IF NOT EXISTS logradouro  text,
  ADD COLUMN IF NOT EXISTS numero      text,
  ADD COLUMN IF NOT EXISTS complemento text,
  ADD COLUMN IF NOT EXISTS bairro      text,
  ADD COLUMN IF NOT EXISTS municipio   text,
  ADD COLUMN IF NOT EXISTS uf          char(2);
