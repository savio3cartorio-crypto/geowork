# Arquitetura — GeoWork CAR & SIGEF

## Estado atual

| Parte | Situação |
|---|---|
| Interface GeoWork: topbar (cliente, serviço, +Rev), mapa com modos de base, 4 pilares | Feito, protótipo (`index.html`, `js/app.js`), não testado no navegador |
| Cálculo de área, perímetro, azimute, rumo e distância | Feito (`js/geo.js`) |
| Validação topológica (fechamento, auto-interseção) | Feito (`js/geo.js`) |
| Barra CAD: distância e azimute, `PL`, `FECHAR`, `DESFAZER`, `DIST`, `SNAP`, `LIMPAR` | Feito (`js/cad.js`, `js/app.js`) |
| `OFFSET` (paralela planar no navegador e por `ST_Buffer`/`ST_OffsetCurve` no backend) | Feito |
| `FILLET` | Não iniciado (o comando responde "não implementado") |
| Backend FastAPI + PostGIS (`backend/`): clientes, serviços, revisões, perímetro, camadas, OFFSET | Feito, com testes de integração; não executado (falta PostGIS na máquina) |
| Quadro CAR: RL de referência, módulo fiscal informado, APP por buffer de curso d'água | Feito, com premissas simplificadas (ver README) |
| Memorial descritivo tabular e texto corrido (texto, com confrontantes por trecho) | Feito (`js/memorial.js`) |
| Exportação DXF (R12 mínimo), GeoJSON e planilha SIGEF em CSV | Feito (`js/export.js`, `js/app.js`) |
| Croqui de orçamento (janela para imprimir ou salvar em PDF) | Feito |
| Esquema do banco PostGIS | Desenhado (`db/schema.sql`), não testado em base real |
| Camadas públicas WMS (SIGEF, SICAR, DataGEO) | Endpoints do protótipo, não confirmados |
| Exportadores `.ODS`, `.DOCX`, `.PDF` nativo, `.SHP`, prancha PDF, carta de anuência | Não iniciado |

## Arquitetura de pastas (alvo)

```
GEO_SAVIO/
├── index.html            tela atual (protótipo)
├── css/style.css
├── js/
│   ├── geo.js            cálculos puros (UTM, área, azimute, rumo, validação)
│   ├── memorial.js       gerador de memorial descritivo
│   └── app.js            interface atual
├── db/
│   └── schema.sql        esquema PostgreSQL + PostGIS
├── docs/
│   └── ARQUITETURA.md    este arquivo
└── README.md
```

Quando o protótipo virar aplicação, a estrutura alvo passa a ser a do prompt (React + Vite + TypeScript, com `src/features/` separando os pilares), e a parte de exportação ODS/DOCX/PDF/DXF passa para um backend Python (FastAPI + GeoPandas), que processa os arquivos pesados.

## Decisões técnicas

- **Datum e projeção.** Armazenamento em SIRGAS 2000 geográfico (EPSG:4674). Cálculo planimétrico, azimute e memorial em UTM SIRGAS 2000 da zona do centroide (EPSG:31978 a 31985). Áreas geodésicas (elipsoide) são mostradas junto, para conferência.
- **Azimute.** É o azimute de grade (norte da quadrícula UTM), não o verdadeiro. A convergência meridiana não é corrigida, o que é a prática do memorial de INCRA/cartório para planimetria.
- **Código de vértice.** Padrão `ABCD-M-0001`: quatro letras do imóvel, tipo (`M` marco, `P` ponto, `V` vértice) e sequência.
- **Confrontante.** Ligado ao trecho (vértice inicial e final), não ao vértice. O memorial corrido mostra `[a informar]` enquanto não há cadastro.
- **Persistência.** Hoje, `localStorage` no navegador, só como conveniência. O banco de `db/schema.sql` é a fonte definitiva quando houver backend.

## Próximos passos, em ordem

1. Criar o backend (FastAPI + PostGIS) e aplicar `db/schema.sql` numa base de teste.
2. Dividir a interface nos quatro pilares e adicionar a topbar de cliente e serviço (revisões e clonagem).
3. Barra de comando CAD com entrada de distância e azimute (`PL`, `DIST`) e `SNAP` a vértices.
4. Exportadores: memorial em `.docx`/`.pdf`, SIGEF em `.ods`, `.dxf` e `.shp`.
5. Camadas públicas (SICAR, SIGEF, ANA, ICMBio, FUNAI). Antes, confirmar endpoints WFS/WMS oficiais e as regras de uso de cada um.
