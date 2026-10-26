# GeoWork — backend (FastAPI + PostgreSQL + PostGIS)

API que guarda clientes, serviços, revisões, camadas, perímetro com vértices e confrontantes, e calcula
o OFFSET (paralelas) no próprio banco com PostGIS.

## 1. Instalar o PostgreSQL e o PostGIS (uma vez)

No Manjaro/Arch, com sudo:

```bash
sudo pacman -S postgresql postgis
```

Se o cluster ainda não existir (`/var/lib/postgres/data` vazio), inicialize-o:

```bash
sudo -iu postgres initdb -D /var/lib/postgres/data
```

Inicie o serviço:

```bash
sudo systemctl enable --now postgresql
```

## 2. Criar o banco

Troque `troque-esta-senha` por uma senha sua.

```bash
sudo -iu postgres psql -c "CREATE USER geowork WITH PASSWORD 'troque-esta-senha';"
sudo -iu postgres createdb -O geowork geowork
sudo -iu postgres psql -d geowork -c "CREATE EXTENSION postgis;"
```

A extensão é criada como superusuário; por isso ela vem antes do esquema.

Aplique o esquema como o usuário da aplicação:

```bash
cd /home/saviobig/Documentos/GEO_SAVIO
psql "postgresql://geowork:troque-esta-senha@localhost:5432/geowork" -f db/schema.sql
```

## 3. Rodar a API

```bash
cd /home/saviobig/Documentos/GEO_SAVIO/backend
export GEOWORK_DATABASE_URL="postgresql://geowork:troque-esta-senha@localhost:5432/geowork"
.venv/bin/uvicorn app.main:app --reload
```

Documentação interativa: http://127.0.0.1:8000/docs. Teste de saúde: `GET /health`, que confirma a versão do PostGIS.

## 4. Testes

```bash
cd /home/saviobig/Documentos/GEO_SAVIO/backend
GEOWORK_DATABASE_URL="postgresql://geowork:troque-esta-senha@localhost:5432/geowork" .venv/bin/pytest -q
```

Sem banco disponível, os testes são pulados, sem falhar.

## Rotas

| Método e rota | O que faz |
|---|---|
| `GET /health` | conexão e versão do PostGIS |
| `POST /clientes`, `GET /clientes` | cadastro e listagem de clientes |
| `POST /servicos`, `GET /servicos?cliente_id=` | cria e lista serviços |
| `GET /servicos/{id}` | serviço com camadas, vértices e confrontantes |
| `POST /servicos/{id}/revisoes` | nova revisão: copia camadas, vértices e confrontantes |
| `PUT /servicos/{id}/perimetro` | grava o perímetro, com vértices e confrontantes (substitui o anterior) |
| `PUT /servicos/{id}/camadas/{categoria}` | substitui as feições de `rl`, `app`, `hidro` ou `edificacao` |
| `POST /servicos/{id}/offset` | cria a paralela de uma feição (ver abaixo) |
| `POST /servicos/{id}/arquivos` | anexa um PDF em base64 (ex.: demonstrativo do CAR, categoria `recibo_car`); até 10 MB; o arquivo vai para `GEOWORK_STORAGE_DIR` (padrão `backend/storage/`) |
| `GET /servicos/{id}/arquivos`, `GET /arquivos/{id}` | lista os anexos do serviço e baixa um deles |

Coordenadas são `[lng, lat]` em SIRGAS 2000 geográfico (EPSG:4674).

## OFFSET

`POST /servicos/{id}/offset` com `{"categoria": "rl", "indice": 0, "distancia_m": 50}`.

- **Polígonos** (`rl`, `app`, `edificacao`): `ST_Buffer` com cantos em esquadro. Distância positiva expande; negativa contrai.
- **Linhas** (`hidro`): `ST_OffsetCurve`. Distância positiva vai à esquerda do traçado; negativa, à direita.
- O cálculo é feito na zona UTM SIRGAS 2000 do próprio dado (EPSG:31978 a 31985), então a distância é em metros.
- A feição original é preservada, como no AutoCAD. A nova entra ao fim da categoria.
- O perímetro não recebe paralela: a rota recusa com 422.

## Códigos de resposta

- `404`: serviço, cliente ou feição não encontrada.
- `409`: código de vértice, confrontante ou revisão já existe.
- `422`: entrada inválida, como polígono com menos de 3 vértices, coordenada fora do Brasil ou OFFSET vazio.

## Limites atuais

- Uma base e uma empresa (`Empresa padrão`). Não há login nem multiusuário.
- O frontend ainda salva no `localStorage`; a integração com esta API é o próximo passo.
- Não há cálculo de sobreposição com o SIGEF (depende da camada oficial).
- A conexão é aberta por requisição, sem pool. Adequado ao protótipo.
