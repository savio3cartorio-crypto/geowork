/*
 * Camadas públicas oficiais para ver no mapa. São as bases de origem que o Mapa do Registro de Imóveis
 * (ONR) também reúne; o GeoWork lê direto das fontes: IBGE (WMS), PAMGIA/IBAMA e ANM (ArcGIS). O SICAR é
 * instável para pedidos diretos do navegador e passa pelo servidor do GeoWork (/camadas/sicar.png, com cache).
 * Endereços conferidos em 26/10/2026. Depende de Leaflet.
 */
const CamadasPublicas = (() => {
  const PAMGIA = 'https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases';
  const UFS_URL = `${PAMGIA}/lim_est_ibge_250k_2021/MapServer/7/query`;

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

  function criarCamada(c) {
    const comum = { opacity: 0.75, minZoom: c.minZoom || 0, attribution: c.fonte };
    if (c.porUf) return camadaPorUf(c, comum);
    if (c.tipo === 'wms') {
      return L.tileLayer.wms(c.url, { ...comum, layers: c.layers, format: 'image/png', transparent: true });
    }
    return new ArcGisExport(c.url, { ...comum, camada: c.camada });
  }

  return { CATALOGO, criarCamada };
})();
