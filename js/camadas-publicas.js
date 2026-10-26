/*
 * Camadas públicas oficiais para ver no mapa. São as bases de origem que o Mapa do Registro de Imóveis
 * (ONR) também reúne; o GeoWork lê direto das fontes: IBGE (WMS), PAMGIA/IBAMA e ANM (ArcGIS). O SICAR é
 * instável para pedidos diretos do navegador e passa pelo servidor do GeoWork (/camadas/sicar.png, com cache).
 * Endereços conferidos em 26/10/2026. Depende de Leaflet.
 */
const CamadasPublicas = (() => {
  const PAMGIA = 'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases';
  const UFS_URL = `${PAMGIA}/lim_est_ibge_250k_2021/MapServer/7/query`;
  const CATI = 'https://geo.cati.sp.gov.br/server/rest/services';
  const LIMITE_FEICOES = 500; // por tela: mais que isso fica pesado; aproximar o mapa mostra o resto

  // tipo 'wms': GeoServer; tipo 'arcgis': imagem do MapServer (export) por ladrilho.
  // minZoom: bases de imóveis só aparecem de perto (cada ladrilho pede milhares de polígonos).
  const CATALOGO = [
    { grupo: 'Fundiário', id: 'sicar', nome: 'Imóveis do CAR (SICAR)', fonte: 'SFB', cor: 'accent-emerald-500',
      tipo: 'wms', url: '/camadas/sicar.png', porUf: uf => `sicar:sicar_imoveis_${uf}`,
      minZoom: 11 },
    { grupo: 'Fundiário', id: 'sigef', nome: 'SIGEF privado (certificações até 04/2022)', fonte: 'INCRA, cópia IBAMA',
      cor: 'accent-amber-500', tipo: 'arcgis', url: `${PAMGIA}/lim_imovel_sigef_privado_a/MapServer`, camada: 9, minZoom: 11 },
    { grupo: 'Fundiário', id: 'sigef_publico', nome: 'SIGEF público (certificações até 04/2022)', fonte: 'INCRA, cópia IBAMA',
      cor: 'accent-amber-500', tipo: 'arcgis', url: `${PAMGIA}/lim_imovel_sigef_publico_a/MapServer`, camada: 10, minZoom: 11 },
    { grupo: 'Fundiário', id: 'assentamento', nome: 'Assentamentos', fonte: 'INCRA', cor: 'accent-orange-500',
      tipo: 'arcgis', url: `${PAMGIA}/assentamentos_incra/MapServer`, camada: 1 },
    { grupo: 'Áreas protegidas', id: 'terra_indigena', nome: 'Terras indígenas', fonte: 'FUNAI', cor: 'accent-rose-500',
      tipo: 'arcgis', url: `${PAMGIA}/lim_terra_indigena_a/MapServer`, camada: 12 },
    { grupo: 'Áreas protegidas', id: 'uc_federal', nome: 'Unidades de conservação federais', fonte: 'ICMBio',
      cor: 'accent-green-500', tipo: 'arcgis', url: `${PAMGIA}/lim_unidade_conservacao_a/MapServer`, camada: 13 },
    { grupo: 'Áreas protegidas', id: 'uc_estadual', nome: 'Unidades de conservação estaduais', fonte: 'SFB',
      cor: 'accent-green-500', tipo: 'arcgis', url: `${PAMGIA}/lim_unidade_conservacao_est_a/MapServer`, camada: 14 },
    { grupo: 'Áreas protegidas', id: 'floresta_publica', nome: 'Florestas públicas', fonte: 'SFB', cor: 'accent-lime-500',
      tipo: 'arcgis', url: `${PAMGIA}/lim_floresta_publica_a/MapServer`, camada: 8 },
    { grupo: 'Restrições', id: 'embargo_ibama', nome: 'Áreas embargadas', fonte: 'IBAMA', cor: 'accent-red-500',
      tipo: 'arcgis', url: `${PAMGIA}/adm_embargos_ibama_a/MapServer`, camada: 0 },
    { grupo: 'Restrições', id: 'embargo_icmbio', nome: 'Embargos em UCs (2022)', fonte: 'ICMBio', cor: 'accent-red-500',
      tipo: 'arcgis', url: `${PAMGIA}/adm_embargo_icmbio_a/MapServer`, camada: 0 },
    { grupo: 'Restrições', id: 'mineracao', nome: 'Processos minerários ativos', fonte: 'ANM', cor: 'accent-yellow-500',
      tipo: 'arcgis', url: 'https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/MapServer', camada: 0 },
    { grupo: 'Limites e hidrografia', id: 'municipios', nome: 'Municípios (BC250 2025)', fonte: 'IBGE',
      cor: 'accent-purple-500', tipo: 'wms', url: 'https://geoservicos.ibge.gov.br/geoserver/ows',
      layers: 'CCAR:BC250_2025_lml_municipio_a' },
    { grupo: 'Limites e hidrografia', id: 'hidrografia', nome: 'Hidrografia (BC250 2025)', fonte: 'IBGE',
      cor: 'accent-sky-500', tipo: 'wms', url: 'https://geoservicos.ibge.gov.br/geoserver/ows',
      layers: 'CCAR:BC250_2025_hid' },
    { grupo: 'Limites e hidrografia', id: 'massa_dagua', nome: "Massas d'água (BC250 2025)", fonte: 'IBGE',
      cor: 'accent-sky-500', tipo: 'wms', url: 'https://geoservicos.ibge.gov.br/geoserver/ows',
      layers: 'CCAR:BC250_2025_hid_massa_dagua_a' },
    // CATI (SAA-SP): serviços hospedados sem imagem de mapa; o GeoWork pede as feições do retângulo visível
    { grupo: 'Estadual SP (CATI)', id: 'sigef_sp_2024', nome: 'SIGEF SP (certificações até 05/2024)', fonte: 'CATI',
      cor: 'accent-amber-500', hex: '#f59e0b', tipo: 'feicoes', minZoom: 13,
      url: `${CATI}/Hosted/SIGEF_SP_07_05_2024/FeatureServer/61`,
      campos: ['nome_area', 'parcela_co', 'codigo_imo', 'status', 'data_aprov', 'registro_m', 'municipio_', 'uf_id'] },
    { grupo: 'Estadual SP (CATI)', id: 'uc_estadual_pi', nome: 'UC estadual de proteção integral', fonte: 'CATI',
      cor: 'accent-green-500', hex: '#16a34a', tipo: 'feicoes', minZoom: 8,
      url: `${CATI}/Hosted/UC_Estadual_PI_20203/FeatureServer/0`,
      campos: ['nome', 'cate', 'grupo', 'ato', 'ano'] },
    { grupo: 'Estadual SP (CATI)', id: 'uc_estadual_us', nome: 'UC estadual de uso sustentável', fonte: 'CATI',
      cor: 'accent-lime-500', hex: '#84cc16', tipo: 'feicoes', minZoom: 8,
      url: `${CATI}/Hosted/UC_Estadual_US_20203/FeatureServer/0`,
      campos: ['nome_uc1', 'categori3', 'grupo4', 'ato_lega9', 'ano_cria6'] },
    { grupo: 'Estadual SP (CATI)', id: 'app_topo_morro', nome: 'APP de topo de morro (relevo)', fonte: 'CATI',
      cor: 'accent-sky-500', hex: '#0ea5e9', tipo: 'feicoes', minZoom: 12,
      url: `${CATI}/Hosted/Relevo_APP_topo_morro3/FeatureServer/0`,
      campos: ['nome', 'tipo_relev', 'referencia', 'area_ha'] },
    { grupo: 'Estadual SP (CATI)', id: 'declividade_45', nome: 'Zonas acima de 45° (relevo)', fonte: 'CATI',
      cor: 'accent-rose-500', hex: '#f43f5e', tipo: 'feicoes', minZoom: 11,
      url: `${CATI}/Hosted/Relevo_zonas_acima_453/FeatureServer/0`,
      campos: ['nome', 'referencia', 'area_ha'] },
    { grupo: 'Estadual SP (CATI)', id: 'incendio_2024', nome: 'Áreas queimadas em 2024', fonte: 'CATI',
      cor: 'accent-red-500', hex: '#dc2626', tipo: 'feicoes', minZoom: 8,
      url: `${CATI}/Hosted/FOGO_2024_TOTAL_COMPL_DISSOLVE_REVISADO2/FeatureServer/42`,
      campos: ['nome', 'uf', 'area_ha'] },
  ];

  // Ladrilho a partir do export do ArcGIS MapServer, na mesma grade Web Mercator do Leaflet
  const ArcGisExport = L.TileLayer.extend({
    getTileUrl(coords) {
      const size = this.getTileSize();
      const nw = coords.scaleBy(size);
      const se = nw.add(size);
      const crs = this._map.options.crs;
      const a = crs.project(this._map.unproject(nw, coords.z));
      const b = crs.project(this._map.unproject(se, coords.z));
      const bbox = [a.x, b.y, b.x, a.y].map(v => v.toFixed(2)).join(',');
      return (
        `${this._url}/export?bbox=${bbox}&bboxSR=3857&imageSR=3857&size=${size.x},${size.y}` +
        `&format=png32&transparent=true&f=image&layers=show:${this.options.camada}`
      );
    },
  });

  // UFs que aparecem na tela (limites estaduais do IBGE, no PAMGIA)
  async function ufsNaTela(map) {
    const b = map.getBounds();
    const params = new URLSearchParams({
      geometry: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(','),
      geometryType: 'esriGeometryEnvelope', inSR: '4674', spatialRel: 'esriSpatialRelIntersects',
      outFields: 'sigla', returnGeometry: 'false', f: 'json',
    });
    const res = await fetch(`${UFS_URL}?${params}`);
    const dados = await res.json();
    return (dados.features || []).map(f => f.attributes.sigla.toLowerCase()).sort();
  }

  // Base publicada em uma camada por UF (SICAR): pede só as UFs da tela. Pedir as 27 juntas deixa cada
  // ladrilho lento e o servidor do CAR recusa a rajada de ladrilhos.
  function camadaPorUf(c, comum) {
    const camada = L.tileLayer.wms(c.url, {
      ...comum, layers: '', format: 'image/png', transparent: true, tileSize: 512, zoomOffset: -1,
    });
    let atual = '';
    const atualizar = async () => {
      const map = camada._map;
      if (!map || map.getZoom() < (c.minZoom || 0)) return;
      try {
        const layers = (await ufsNaTela(map)).map(c.porUf).join(',');
        if (layers && layers !== atual) {
          atual = layers;
          camada.setParams({ layers });
        }
      } catch (err) {
        camada.fire('tileerror');
      }
    };
    camada.createTile = (function (original) {
      return function (coords, done) {
        if (!atual) { // sem UF definida ainda: ladrilho vazio, sem pedir ao servidor
          const vazio = document.createElement('img');
          setTimeout(() => done(null, vazio), 0);
          return vazio;
        }
        return original.call(this, coords, done);
      };
    })(camada.createTile);
    camada.on('add', () => {
      camada._map.on('moveend', atualizar);
      atualizar();
    });
    camada.on('remove', e => e.target._map && e.target._map.off('moveend', atualizar));
    return camada;
  }

  // Feições de um serviço ArcGIS hospedado (sem imagem de mapa): busca em GeoJSON só o retângulo da tela
  function camadaFeicoes(c) {
    const grupo = L.layerGroup();
    let pedido = 0;
    const atualizar = async () => {
      const map = grupo._map;
      if (!map) return;
      const id = ++pedido;
      if (map.getZoom() < c.minZoom) return grupo.clearLayers();
      const b = map.getBounds();
      const params = new URLSearchParams({
        where: '1=1', geometry: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(','),
        geometryType: 'esriGeometryEnvelope', inSR: '4326', spatialRel: 'esriSpatialRelIntersects',
        outFields: c.campos.join(','), outSR: '4326', f: 'geojson', resultRecordCount: LIMITE_FEICOES,
      });
      try {
        const dados = await (await fetch(`${c.url}/query?${params}`)).json();
        if (dados.error) throw new Error(dados.error.message);
        if (id !== pedido) return; // a tela mudou enquanto a resposta vinha
        grupo.clearLayers();
        L.geoJSON(dados, { style: { color: c.hex, weight: 1.5, opacity: 0.85, fillOpacity: 0.12 } }).addTo(grupo);
      } catch (err) {
        if (id === pedido) grupo.fire('tileerror');
      }
    };
    // grupo de feições não tem o atributo de crédito das camadas de imagem: o crédito vai ao rodapé à parte
    grupo.on('add', () => {
      grupo._map.on('moveend', atualizar);
      grupo._map.attributionControl.addAttribution(c.fonte);
      atualizar();
    });
    grupo.on('remove', e => {
      if (!e.target._map) return;
      e.target._map.off('moveend', atualizar);
      e.target._map.attributionControl.removeAttribution(c.fonte);
    });
    return grupo;
  }

  function criarCamada(c) {
    const comum = { opacity: 0.75, minZoom: c.minZoom || 0, attribution: c.fonte };
    if (c.tipo === 'feicoes') return camadaFeicoes(c);
    if (c.porUf) return camadaPorUf(c, comum);
    if (c.tipo === 'wms') {
      return L.tileLayer.wms(c.url, { ...comum, layers: c.layers, format: 'image/png', transparent: true });
    }
    return new ArcGisExport(c.url, { ...comum, camada: c.camada });
  }

  return { CATALOGO, criarCamada };
})();
