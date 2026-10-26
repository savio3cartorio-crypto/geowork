# GeoWork — CAR, SIGEF & Notarial

Workspace de georreferenciamento para parcelas rurais: cadastro de cálculo, quadro CAR, memorial
descritivo e entregas. Roda no navegador, sem servidor e sem etapa de build.

## Como abrir

Abra `index.html` no navegador (Chrome, Edge ou Firefox), com internet, porque Leaflet, Tailwind,
proj4, Turf, shpjs e o conversor KML são carregados de CDNs.

Os dados ficam no `localStorage` deste navegador. Não há backend nem login.

## O que faz

**Topbar.** Seleção de cliente e serviço, criação de revisão (`+Rev`, que clona o serviço ativo),
busca por coordenadas decimais (`-23.02, -45.55`) ou por município (Nominatim/OSM), croqui de orçamento
e zona UTM atual.

**Mapa.** Modos de base (satélite Esri, ruas OSM, modo CAD com grade) e camadas WMS públicas.
Clique para marcar vértices. Com `SNAP` ligado, o clique gruda no vértice mais próximo.

**Barra CAD (rodapé).**

| Entrada | Efeito |
|---|---|
| `120.50 85.25` | novo vértice a 120,50 m com azimute de 85,25° a partir do último ponto |
| `PL` | modo polilinha |
| `FECHAR` | confirma o desenho na camada ativa |
| `DESFAZER` | remove o último ponto do rascunho |
| `DIST` | distância e azimute entre os dois últimos pontos do rascunho |
| `SNAP` | liga ou desliga a atração a vértices |
| `LIMPAR` | limpa a camada ativa |
| `OFFSET 10` | paralela da última feição da camada ativa (positivo expande, negativo contrai). A original é mantida |
| `FILLET` | ainda não implementado |

**Pilar 1, Dados.** Importa GeoJSON, KML, Shapefile (`.zip`) e CSV de pontos (`lat,lon` ou `E,N`).
Guarda matrícula, código INCRA/SNCR e recibo do CAR.

**Pilar 2, Desenho.** Camadas: perímetro, Reserva Legal, APP, curso d'água e edificações. Quadro CAR
com RL de referência, RL desenhada, APP e módulos fiscais. APP por faixa fixa de 30 ou 50 m em volta do curso.

**Pilar 3, Limites.** Área (ha e m²), perímetro e vértices, com código editável, método, altitude,
sigma e, por trecho, o confrontante (nome, CPF/CNPJ, matrícula, código INCRA/SNCR e tipo de divisa).
Validador: mínimo de 3 vértices, auto-interseção e trechos sem confrontante.

**Pilar 4, Entregas.** Memorial notarial em texto corrido e memorial tabular, ambos com confrontantes,
planilha SIGEF em CSV, `.DXF` na zona UTM do perímetro e `.GeoJSON`.

## Estrutura

```
index.html        tela única (topbar, pilares, mapa, barra CAD)
css/style.css     complementos ao Tailwind (modo CAD, rótulos)
js/geo.js         UTM SIRGAS 2000, área, azimute, rumo, distância, validação
js/cad.js         interpretação dos comandos da barra CAD (puro)
js/export.js      DXF e CSV do SIGEF (puro)
js/memorial.js    memorial descritivo tabular e corrido
js/app.js         interface: estado, mapa, pilares, importação, entregas
db/schema.sql     esquema PostgreSQL + PostGIS (ainda não aplicado)
docs/ARQUITETURA.md  estado do projeto, decisões técnicas e próximos passos
```

## Premissas e limites

- **Cálculo.** Área e memorial em UTM SIRGAS 2000, zona escolhida pelo centroide. O azimute é o de
  grade, sem correção de convergência meridiana. Coordenadas em WGS84 e SIRGAS 2000 geográfico são
  tratadas como iguais (diferença sub-métrica).
- **Reserva Legal.** O percentual de 20% é só referência. Ele depende do bioma e da região; confirme com o RT.
- **Módulo fiscal.** Não é fixo. Informe o valor do município, obtido no INCRA.
- **APP.** A faixa de 30 ou 50 m é simplificada. A largura legal depende do curso d'água e das nascentes.
- **Camadas WMS.** Os endpoints vêm do protótipo original e não foram confirmados. Podem não carregar.
- **Fontes de imagem.** Satélite trocado para o Esri World Imagery. Os tiles do Google, do protótipo original, têm
  restrições de uso.
- **Planilha SIGEF.** Sai em CSV, não em ODS, e não segue o layout oficial de ponta a ponta.
- **Sem backend na interface.** A interface ainda salva no navegador, com clientes de demonstração. A API está pronta em `backend/` (FastAPI + PostGIS), mas não está ligada à tela. Veja `backend/README.md`.
- **Não testado no navegador.** O código foi revisado por leitura; não houve execução automatizada.

## Versões das bibliotecas

| Biblioteca | Versão | Origem |
|---|---|---|
| Tailwind (play CDN) | atual | cdn.tailwindcss.com |
| Leaflet | 1.9.4 | unpkg |
| proj4js | 2.9.2 | cdnjs |
| Turf | 6.5.0 | jsdelivr |
| shpjs | 4.0.4 | jsdelivr |
| @tmcw/togeojson | 5.8.1 | jsdelivr |

Essas versões não foram confirmadas no registro npm, porque o ambiente não tem npm. Confira antes de fixar em produção.
