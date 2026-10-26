# Análise do Geomart (geomart.com.br) para o Claude Code

Documento de contexto gerado a partir de leitura da interface e das requisições feitas pelo Claude in Chrome em 26/10/2026, com a conta logada no plano Gratuito. Serve de ponto de partida para o Claude Code criar a aplicação. Tudo o que não foi observado diretamente está marcado como "a confirmar".

## O que é o site

Plataforma de geoprocessamento e consulta de imóveis rurais e urbanos, voltada a regularização fundiária, cartórios e profissionais de topografia e georreferenciamento.

## Stack observada

Aplicação Next.js (chunks em /_next/static), com mapa baseado em tiles do Google Maps (imagens de satélite, lyrs=y). Analytics via Google Analytics e Cloudflare RUM.

## Endpoints de API observados (paths, sem parâmetros)

Todos sob https://geomart.com.br/api/:
- /api/auth/session (sessão de autenticação, chamado várias vezes ao carregar)
- /api/user/sessions
- /api/user/access (permissões e plano do usuário)
- /api/user/avisos (avisos ao usuário)

Os endpoints de busca de imóveis, certificações INCRA e ferramentas não apareceram no carregamento do painel, provavelmente porque são disparados ao usar cada função. A confirmar ao testar cada ferramenta.

## Estrutura observada (área logada)

Menu lateral: Mapa de Imóveis, Localizar Imóvel, Minhas Certificações, Ferramentas, Oportunidades (CRM), Meus Projetos, Meus Arquivos, Ajuda e suporte.

Tela "Localizar Imóvel": abas de busca por Código, Coordenadas, Endereço, SICAR (CAR), INCRA (CCIR), Certificação e Matrícula. Campo de exemplo com formato de código SICAR: SP-3538709-E4A558DDFC5649D0A3524EB344ECC52D (código CAR de São Paulo, município 3538709). Há histórico de consultas por usuário, vazio na conta de teste.

"Minhas Certificações": lista de imóveis do banco de dados vinculados ao INCRA do usuário. No carregamento, mostrou "Consultando banco de dados de certificações..." sem concluir, o que pode indicar lentidão ou falha do serviço externo (a confirmar).

"Meus Arquivos": gerenciador de pastas para KML, DXF e Shapefile, com upload, criação de pasta, busca por nome e anotações.

"Ferramentas": Buscar CIB/NIRF, Conversor de arquivos, Curvas de Nível, Carta do IGC, Memorial descritivo, Leitura de matrícula, Estações NTRIP, Aeroportos e Helipontos, PDF para DOCX.

## Modelo de negócio observado

Plano atual: Gratuito. Vários botões estão marcados como "Disponível nos planos pagos" (seis no painel, a confirmar quais funções são).

## Lacunas para a próxima etapa

Não foram capturados os endpoints das funções de busca, certificação e ferramentas, nem os formatos de requisição e resposta. Para isso é preciso executar cada função na aba logada e ler as requisições. Também falta a documentação oficial da API, se existir.

## Próximos passos sugeridos para o Claude Code

Mapear cada ferramenta do menu e seus parâmetros de entrada e saída. Identificar quais dados vêm de fontes oficiais (SICAR, INCRA, IGC, NIRF) e como são consultados. Verificar termos de uso antes de qualquer raspagem ou automação, já que o site é comercial e tem planos pagos. Definir o escopo da aplicação nova em relação às funções que se quer replicar ou integrar.
