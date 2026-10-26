-- Login do sistema: usuários e sessões guardadas no servidor (revogáveis no logout).
-- Aplicar depois de 003: psql -d geowork -f db/migracoes/004_usuarios_e_sessoes.sql
-- O token da sessão nunca é guardado: só o hash SHA-256 dele.

CREATE TABLE IF NOT EXISTS usuarios (
  id          bigserial PRIMARY KEY,
  email       text        NOT NULL UNIQUE,
  senha_hash  text        NOT NULL,
  papel       text        NOT NULL DEFAULT 'usuario' CHECK (papel IN ('master', 'usuario')),
  ativo       boolean     NOT NULL DEFAULT true,
  criado_em   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessoes (
  token_hash  char(64)    PRIMARY KEY,
  usuario_id  bigint      NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  criada_em   timestamptz NOT NULL DEFAULT now(),
  expira_em   timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS sessoes_expira_em_idx ON sessoes (expira_em);
