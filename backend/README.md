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
| `GET /health` | conexão e versão do PostGIS (público) |
| `GET /login` | tela de entrada (público) |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/eu` | entra (cria a sessão em cookie HTTP-only), sai (revoga a sessão) e informa o usuário logado |
| `POST /clientes`, `GET /clientes`, `PUT /clientes/{id}` | cadastro, listagem e edição de clientes, com endereço padronizado (CEP `xxxxx-xxx`, UF em duas letras) |
| `POST /servicos`, `GET /servicos?cliente_id=` | cria e lista serviços (o nome do imóvel é único por cliente e revisão) |
| `GET /servicos/{id}` | serviço com camadas, vértices e confrontantes |
| `POST /servicos/{id}/revisoes` | nova revisão: copia camadas, vértices e confrontantes |
| `PUT /servicos/{id}/perimetro` | grava o perímetro, com vértices e confrontantes (substitui o anterior) |
| `PUT /servicos/{id}/camadas/{categoria}` | substitui as feições de uma camada (`sede`, `vegetacao_nativa`, `area_consolidada`, `pousio`, `servidao`, `app`, `uso_restrito`, `rl`, `rl_averbada`, `rl_aprovada`, `rl_compensacao`, `hidro`, `edificacao`) |
| `PUT /servicos/{id}/camadas` | substitui várias camadas numa só transação: `{"camadas": {categoria: [feições]}}` |
| `POST /servicos/{id}/offset` | cria a paralela de uma feição (ver abaixo) |
| `POST /servicos/{id}/arquivos` | anexa um PDF em base64 (ex.: demonstrativo do CAR, categoria `recibo_car`); até 10 MB; o arquivo vai para `GEOWORK_STORAGE_DIR` (padrão `backend/storage/`) |
| `GET /servicos/{id}/arquivos`, `GET /arquivos/{id}` | lista os anexos do serviço e baixa um deles |
| `GET /buscar?q=` | busca serviços por nome do imóvel, código CAR, código INCRA/SNCR, matrícula, nome ou documento do cliente |
| `PUT /servicos/{id}/curvas`, `DELETE /servicos/{id}/curvas` | grava (substitui) ou remove as curvas de nível; cada cota é uma linha da categoria `curvas` com `propriedades.cota` |
| `GET /servicos/{id}/exportar.gpkg` | base vetorial em GeoPackage (SIRGAS 2000, EPSG:4674): uma camada por categoria (perímetro, camadas do CAR, `edificacao`, `curvas`) e `vertices` com código, altitude e precisão |
| `GET /servicos/{id}/car` | acompanhamento do CAR: situação atual, prazo, dias restantes, histórico e pendências |
| `PUT /servicos/{id}/car/situacao` | registra nova situação (`inscrito`, `em_analise`, `aguarda_complementacao`, `em_alteracao`, `aprovado`, `aprovado_dispensa_rl`, `invalido`, `cancelado`); `aguarda_complementacao` exige `prazo` (data) ou `prazo_dias` |
| `POST /servicos/{id}/car/pendencias`, `PUT /pendencias/{id}`, `DELETE /pendencias/{id}` | pendências do parecer: incluir, editar ou marcar `resolvida`, remover |
| `GET /servicos/{id}/sobreposicoes` | cruza o perímetro com bases públicas oficiais (CAR/SICAR, SIGEF, terras indígenas, UCs, assentamentos, florestas públicas, embargos IBAMA/ICMBio, processos ANM); devolve achados com área e % do imóvel, fontes consultadas e o módulo fiscal estimado pelos CARs vizinhos |
| `GET /camadas/identificar?lat&lng&camadas=sicar,mineracao&grau_px` | dados das feições das camadas públicas no ponto (botão Identificar do mapa), só campos sem dados pessoais, com a geometria para destaque |
| `GET /car/prazos?dias=30` | CARs aguardando complementação com prazo vencido ou vencendo em `dias` (só a revisão mais recente), com pendências abertas |
| `GET /servicos/{id}/exportar-sicar.zip` | pacote para a etapa Geo do SICAR: um .zip de shapefile por camada do CAR (EPSG:4674, com `area_ha` ou `compr_m`) e um `LEIA-ME.txt` com a categoria de cada arquivo |
| `POST /memorial.docx` | gera o memorial em Word a partir das linhas do texto (`corrido` ou `tabular`) |

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

- Uma base e uma empresa (`Empresa padrão`). Os usuários entram com login próprio, mas todos veem os mesmos dados.
- Sem cadastro pela web: usuários são criados pelo terminal do servidor (ver abaixo).
- O bloqueio de tentativas de login fica em memória: vale enquanto houver um único processo da API.
- Não há cálculo de sobreposição com o SIGEF (depende da camada oficial).
- A conexão é aberta por requisição, sem pool. Adequado ao protótipo.

## Mapa do Google (opcional)

O mapa base padrão é o **Híbrido** da Esri (satélite com vias e nomes). O modo **Híbrido OSM** põe estradas
(as de terra tracejadas), rios e nomes do OpenStreetMap sobre o mesmo satélite, pelo OpenFreeMap (gratuito,
sem chave, uso comercial permitido). Ele usa a MapLibre GL 5.24.0 e o plugin maplibre-gl-leaflet 0.1.4,
em `vendor/maplibre/`, carregados só quando o modo é escolhido. O botão **Google** (satélite híbrido do
Google) só aparece quando o servidor tem uma chave da **Map Tiles API**, o serviço oficial de ladrilhos do Google.
Puxar ladrilhos direto de `mt*.google.com` não é permitido pelos termos do Google Maps.

1. No Google Cloud, num projeto com faturamento ativo, ative a **Map Tiles API** e crie uma chave de API.
2. Restrinja a chave: *Restrições de aplicativo* → sites → `https://geowork.savioescrevente.com/*`;
   *Restrições de API* → só Map Tiles API. A chave vai na URL dos ladrilhos, então a restrição é obrigatória.
3. No servidor: acrescente `GEOWORK_GOOGLE_MAPS_KEY=<chave>` em `/etc/geowork/env` e rode
   `systemctl restart geowork`.

O GeoWork mostra o logotipo do Google e a atribuição da área visível, como os termos exigem.

## Login

Todas as rotas de dados exigem sessão; sem ela respondem `401`. A página principal redireciona para `/login`.

- A senha é guardada com scrypt; o cookie `gw_sessao` tem `HttpOnly`, `SameSite=Strict` e `Secure`.
- O banco guarda só o hash SHA-256 do token. Sair apaga a sessão no servidor.
- Sessões duram `GEOWORK_SESSAO_HORAS` horas (padrão 8).
- Após 5 falhas seguidas para o mesmo e-mail, novas tentativas são recusadas por 15 minutos.
- `GEOWORK_COOKIE_SEGURO=false` só para testar em HTTP local; em produção deixe ligado (HTTPS).

Criar o usuário master no servidor (a senha é pedida sem aparecer na tela):

```bash
cd /opt/geowork/backend
.venv/bin/python -m app.usuarios criar voce@exemplo.com --papel master
```

Trocar a senha de um usuário (encerra as sessões abertas dele):

```bash
.venv/bin/python -m app.usuarios senha voce@exemplo.com
```

## Produção (VPS)

O sistema roda em https://geowork.savioescrevente.com: Caddy (HTTPS automático) na frente da API em
`127.0.0.1:8000`, serviço `geowork.service`, código em `/opt/geowork` e credenciais em `/etc/geowork/env`
(fora do repositório). Firewall libera só 22, 80 e 443. SSH só por chave.

### Publicar uma versão

```bash
git commit ...            # o deploy publica só o que está commitado
scripts/deploy.sh
```

O script envia o commit para `/opt/geowork-staging`, roda os testes no banco `geowork_test`, faz backup,
aplica as migrações pendentes na produção, troca os arquivos e reinicia. Se os testes falharem, nada muda
na produção. A versão publicada fica em `/opt/geowork/VERSAO`.

### Backup

`geowork-backup.timer` roda todo dia às 03:00 UTC (meia-noite em Brasília) e guarda 14 dias em
`/var/backups/geowork`: `banco-AAAAMMDD-HHMM.dump` (pg_dump) e `anexos-AAAAMMDD-HHMM.tar.gz`.

Backup manual: `/usr/local/bin/geowork-backup`. Restaurar o banco (apaga os dados atuais):

```bash
systemctl stop geowork
sudo -u postgres dropdb geowork && sudo -u postgres createdb -O geowork geowork
sudo -u postgres psql -d geowork -c "CREATE EXTENSION postgis;"
sudo -u postgres pg_restore --no-owner --role=geowork --dbname=geowork < /var/backups/geowork/banco-AAAAMMDD-HHMM.dump
systemctl start geowork
```

O `pg_restore` termina com 2 erros esperados e inofensivos: `must be owner of extension postgis` e
`permission denied for table spatial_ref_sys` (a extensão já foi criada no passo anterior).

Os backups ficam na própria VPS: protegem contra erro humano e falha da aplicação, mas não contra a perda
do servidor. Para isso, copie-os para fora (ex.: `scp` para o seu computador) ou ative os snapshots do painel.

### Interface

O CSS é gerado pelo Tailwind CLI (`scripts/build-css.sh`) e commitado em `css/tailwind.css`. As bibliotecas
de terceiros ficam em `vendor/` com versões fixas; a página não depende de CDN, só dos mapas de fundo.

## Migrações

O deploy aplica automaticamente os arquivos novos de `db/migracoes/` e registra cada um em `schema_migracoes`.
Para um banco antigo, sem essa tabela, os arquivos são:

- `002_curvas_de_nivel.sql`: adiciona a categoria `curvas` em `camadas_desenho`.
- `003_endereco_cliente.sql`: adiciona as colunas de endereço em `clientes`.
- `004_usuarios_e_sessoes.sql`: cria as tabelas `usuarios` e `sessoes` do login.
