-- GeoWork — esquema PostgreSQL + PostGIS
-- Geometrias armazenadas em SIRGAS 2000 geográfico (EPSG:4674).
-- Cálculos métricos (área, buffer, offset) são feitos em UTM SIRGAS 2000 (EPSG:31978 a 31985),
-- escolhida pela zona do centroide da geometria.
-- Aplicar em uma base vazia: psql -d geowork -f db/schema.sql

CREATE EXTENSION IF NOT EXISTS postgis;

-- Quem presta o serviço ---------------------------------------------------------

CREATE TABLE empresas (
  id            bigserial PRIMARY KEY,
  razao_social  text        NOT NULL,
  cnpj          varchar(18) UNIQUE,
  criado_em     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE responsaveis_tecnicos (
  id          bigserial PRIMARY KEY,
  empresa_id  bigint      REFERENCES empresas(id) ON DELETE SET NULL,
  nome        text        NOT NULL,
  cpf         varchar(14),
  conselho    varchar(10) NOT NULL CHECK (conselho IN ('CREA', 'CFT', 'INCRA', 'OUTRO')),
  registro    text        NOT NULL,
  uf          char(2)
);

-- Cliente e serviço -------------------------------------------------------------

CREATE TABLE clientes (
  id          bigserial PRIMARY KEY,
  empresa_id  bigint      NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  nome        text        NOT NULL,
  cpf_cnpj    varchar(18) NOT NULL,
  email       text,
  telefone    text,
  cep         varchar(9),
  logradouro  text,
  numero      text,
  complemento text,
  bairro      text,
  municipio   text,
  uf          char(2)
);

CREATE TABLE servicos (
  id                bigserial PRIMARY KEY,
  empresa_id        bigint      NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  cliente_id        bigint      REFERENCES clientes(id) ON DELETE SET NULL,
  rt_id             bigint      REFERENCES responsaveis_tecnicos(id) ON DELETE SET NULL,
  tipo              text        NOT NULL CHECK (tipo IN ('GEORREFERENCIAMENTO', 'CAR', 'USUCAPIAO', 'RETIFICACAO', 'ORCAMENTO')),
  nome_imovel       text        NOT NULL,
  codigo_car        text,
  codigo_sncr       text,
  matricula         text,
  modulo_fiscal_ha  numeric(10, 4),                    -- valor do município (INCRA); não é fixo
  area_documental_ha numeric(14, 4) CHECK (area_documental_ha > 0),   -- área da matrícula/título
  rl_percentual     numeric(5, 2) CHECK (rl_percentual > 0 AND rl_percentual <= 100), -- mínimo de RL; nulo = 20%
  -- Revisões (Rev 00, Rev 01...) e clonagem para desmembramento
  revisao           integer     NOT NULL DEFAULT 0,
  servico_origem_id bigint      REFERENCES servicos(id) ON DELETE SET NULL,
  status            text        NOT NULL DEFAULT 'rascunho',
  criado_em         timestamptz NOT NULL DEFAULT now(),
  -- nome do imóvel único por cliente (dois clientes podem ter um "Sítio São José")
  CONSTRAINT servicos_empresa_cliente_nome_revisao_key
    UNIQUE NULLS NOT DISTINCT (empresa_id, cliente_id, nome_imovel, revisao)
);

-- Pilar 1: DADOS ----------------------------------------------------------------

CREATE TABLE arquivos_dados (
  id            bigserial PRIMARY KEY,
  servico_id    bigint      NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
  nome_arquivo  text        NOT NULL,
  tipo          text        NOT NULL CHECK (tipo IN ('CSV', 'TXT', 'GPX', 'KML', 'KMZ', 'SHP', 'DXF', 'PDF', 'OUTRO')),
  categoria     text        CHECK (categoria IN ('insumo', 'matricula', 'ccir', 'itr_cib', 'recibo_car', 'certidao', 'outro')),
  caminho       text        NOT NULL,   -- caminho no storage; o binário não fica no banco
  enviado_em    timestamptz NOT NULL DEFAULT now()
);

-- Feições desenhadas. Uma linha por feição; 'ordem' é o índice dentro da categoria.
-- O perímetro é único por serviço (índice parcial abaixo).
CREATE TABLE camadas_desenho (
  id           bigserial PRIMARY KEY,
  servico_id   bigint      NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
  categoria    text        NOT NULL CHECK (categoria IN (
                 'perimetro', 'sede',
                 'vegetacao_nativa', 'area_consolidada', 'pousio',
                 'servidao', 'app', 'uso_restrito',
                 'rl', 'rl_averbada', 'rl_aprovada', 'rl_compensacao',
                 'hidro', 'edificacao', 'curvas')),
  ordem        integer     NOT NULL DEFAULT 0,
  geom         geometry(Geometry, 4674) NOT NULL,
  propriedades jsonb       NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (servico_id, categoria, ordem)
);
CREATE INDEX camadas_desenho_geom_idx ON camadas_desenho USING GIST (geom);
CREATE UNIQUE INDEX camadas_perimetro_unico ON camadas_desenho (servico_id) WHERE categoria = 'perimetro';

-- Área geodésica de uma feição: SELECT ST_Area(geom::geography) FROM camadas_desenho;

-- Pilar 3: LIMITES (vértices e confrontantes) -----------------------------------

CREATE TABLE vertices_sigef (
  id                    bigserial PRIMARY KEY,
  servico_id            bigint      NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
  codigo                text        NOT NULL,          -- ex.: ABCD-M-0001
  ordem                 integer     NOT NULL,          -- 0 = vértice inicial
  tipo                  char(1)     NOT NULL CHECK (tipo IN ('M', 'P', 'V')),  -- marco, ponto, vértice
  geom                  geometry(Point, 4674) NOT NULL,
  altitude              numeric(10, 3),
  sigma_x               numeric(10, 4),
  sigma_y               numeric(10, 4),
  sigma_z               numeric(10, 4),
  metodo_posicionamento text,                          -- ex.: RTK, PPP, estação total
  UNIQUE (servico_id, codigo),
  UNIQUE (servico_id, ordem)
);
CREATE INDEX vertices_sigef_geom_idx ON vertices_sigef USING GIST (geom);

-- Confrontante por trecho: do vértice inicial ao vértice final (o trecho é a unidade de divisa)
CREATE TABLE confrontantes_limites (
  id                  bigserial PRIMARY KEY,
  servico_id          bigint      NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
  vertice_inicio_id   bigint      NOT NULL REFERENCES vertices_sigef(id) ON DELETE CASCADE,
  vertice_fim_id      bigint      NOT NULL REFERENCES vertices_sigef(id) ON DELETE CASCADE,
  tipo_divisa         text        NOT NULL CHECK (tipo_divisa IN ('PESSOA', 'FAIXA_DOMINIO', 'CORREGO_RIO', 'ESTRADA', 'OUTRO')),
  nome                text        NOT NULL,
  cpf_cnpj            varchar(18),
  matricula           text,
  cns_cartorio        varchar(6),                      -- código CNS do cartório de registro de imóveis
  codigo_incra_sncr   text,
  orgao_faixa         text,                            -- DER/DNIT, quando for faixa de domínio
  UNIQUE (vertice_inicio_id)
);
CREATE INDEX confrontantes_limites_servico_idx ON confrontantes_limites (servico_id);

-- Entregas: documentos gerados ------------------------------------------------

CREATE TABLE entregas (
  id          bigserial PRIMARY KEY,
  servico_id  bigint      NOT NULL REFERENCES servicos(id) ON DELETE CASCADE,
  tipo        text        NOT NULL CHECK (tipo IN ('SIGEF_CSV', 'MEMORIAL_TABULAR', 'MEMORIAL_CORRIDO', 'CARTA_ANUENCIA', 'DXF', 'SHP', 'KML', 'PRANCHA_PDF', 'ORTOMOSAICO')),
  caminho     text        NOT NULL,
  gerado_em   timestamptz NOT NULL DEFAULT now()
);

-- Login do sistema ------------------------------------------------------------

CREATE TABLE usuarios (
  id          bigserial PRIMARY KEY,
  email       text        NOT NULL UNIQUE,
  senha_hash  text        NOT NULL,
  papel       text        NOT NULL DEFAULT 'usuario' CHECK (papel IN ('master', 'usuario')),
  ativo       boolean     NOT NULL DEFAULT true,
  criado_em   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessoes (
  token_hash  char(64)    PRIMARY KEY,
  usuario_id  bigint      NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  criada_em   timestamptz NOT NULL DEFAULT now(),
  expira_em   timestamptz NOT NULL
);

CREATE INDEX sessoes_expira_em_idx ON sessoes (expira_em);

-- Controle de migrações -------------------------------------------------------
-- O deploy (scripts/deploy-remoto.sh) aplica só os arquivos de db/migracoes ainda não listados aqui.
-- Este schema já contém o efeito das migrações abaixo.

CREATE TABLE schema_migracoes (
  arquivo      text        PRIMARY KEY,
  aplicada_em  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migracoes (arquivo) VALUES
  ('002_curvas_de_nivel.sql'),
  ('003_endereco_cliente.sql'),
  ('004_usuarios_e_sessoes.sql'),
  ('005_camadas_car.sql'),
  ('006_servico_unico_por_cliente.sql');
