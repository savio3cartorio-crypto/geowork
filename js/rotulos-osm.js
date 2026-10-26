/*
 * Estradas e nomes do OpenStreetMap sobre o satélite (modo "Híbrido OSM").
 * Fonte: OpenFreeMap (tiles.openfreemap.org), vetorial no esquema OpenMapTiles, gratuito, sem chave e com
 * uso comercial permitido; exige o crédito ao OpenStreetMap. O estilo abaixo desenha só vias, cursos d'água
 * e nomes, em cores que aparecem sobre a imagem; estradas de terra (track) ficam tracejadas.
 * A MapLibre (1 MB, em vendor/maplibre) só é carregada na primeira vez que o modo é escolhido.
 */
const RotulosOsm = (() => {
  const OFM = 'https://tiles.openfreemap.org';
  // O plugin do Leaflet lê o crédito da fonte do estilo e o põe no rodapé do mapa
  const CREDITO =
    '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> · ' +
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
  const NOME = ['coalesce', ['get', 'name:pt'], ['get', 'name']];
  const HALO = { 'text-color': '#ffffff', 'text-halo-color': 'rgba(0,0,0,0.85)', 'text-halo-width': 1.4 };
  // largura da via por zoom: [zoom, largura em px]
  const largura = pares => ['interpolate', ['linear'], ['zoom'], ...pares.flat()];
  const classe = lista => ['in', ['get', 'class'], ['literal', lista]];

  const VIAS = [
    { id: 'principal', classes: ['motorway', 'trunk'], cor: '#fbbf24', w: [[6, 1], [12, 2.5], [17, 8]] },
    { id: 'primaria', classes: ['primary'], cor: '#fde68a', w: [[8, 0.8], [12, 2], [17, 7]] },
    { id: 'secundaria', classes: ['secondary', 'tertiary'], cor: '#ffffff', w: [[9, 0.6], [12, 1.5], [17, 6]] },
    { id: 'local', classes: ['minor', 'service'], cor: '#e5e7eb', w: [[12, 0.6], [14, 1.2], [17, 4]], min: 12 },
    { id: 'terra', classes: ['track'], cor: '#e9c891', w: [[12, 0.6], [14, 1.1], [17, 3]], min: 12, traco: [3, 2] },
  ];

  const ESTILO = {
    version: 8,
    glyphs: `${OFM}/fonts/{fontstack}/{range}.pbf`,
    sources: { omt: { type: 'vector', url: `${OFM}/planet`, attribution: CREDITO } },
    layers: [
      { id: 'rios', type: 'line', source: 'omt', 'source-layer': 'waterway', minzoom: 10,
        filter: classe(['river', 'stream', 'canal']),
        paint: { 'line-color': '#7dd3fc', 'line-opacity': 0.8, 'line-width': largura([[10, 0.5], [14, 1.2], [17, 3]]) } },
      ...VIAS.map(v => ({ id: `${v.id}-borda`, type: 'line', source: 'omt', 'source-layer': 'transportation',
        minzoom: v.min || 0, filter: classe(v.classes), layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': 'rgba(0,0,0,0.55)', 'line-width': largura(v.w.map(([z, w]) => [z, w + 1.5])) } })),
      ...VIAS.map(v => ({ id: v.id, type: 'line', source: 'omt', 'source-layer': 'transportation',
        minzoom: v.min || 0, filter: classe(v.classes), layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': v.cor, 'line-width': largura(v.w), ...(v.traco ? { 'line-dasharray': v.traco } : {}) } })),
      { id: 'nome-rio', type: 'symbol', source: 'omt', 'source-layer': 'waterway', minzoom: 12,
        layout: { 'symbol-placement': 'line', 'text-field': NOME, 'text-font': ['Noto Sans Italic'], 'text-size': 11 },
        paint: { ...HALO, 'text-color': '#bae6fd' } },
      { id: 'nome-agua', type: 'symbol', source: 'omt', 'source-layer': 'water_name', minzoom: 10,
        layout: { 'text-field': NOME, 'text-font': ['Noto Sans Italic'], 'text-size': 12 },
        paint: { ...HALO, 'text-color': '#bae6fd' } },
      { id: 'nome-via', type: 'symbol', source: 'omt', 'source-layer': 'transportation_name', minzoom: 13,
        layout: { 'symbol-placement': 'line', 'text-field': NOME, 'text-font': ['Noto Sans Regular'],
                  'text-size': largura([[13, 10], [17, 13]]) },
        paint: HALO },
      { id: 'lugares', type: 'symbol', source: 'omt', 'source-layer': 'place',
        filter: classe(['city', 'town', 'village', 'suburb', 'neighbourhood', 'hamlet', 'isolated_dwelling', 'locality']),
        layout: { 'text-field': NOME, 'text-font': ['Noto Sans Bold'],
                  'text-size': ['match', ['get', 'class'], 'city', 16, 'town', 14, 'village', 13, 11] },
        paint: HALO },
    ],
  };

  let carregando = null;

  function carregarScript(src) {
    return new Promise((ok, falha) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = ok;
      s.onerror = () => falha(new Error(`não foi possível carregar ${src}`));
      document.head.append(s);
    });
  }

  // MapLibre e o plugin do Leaflet, uma vez só
  function carregar() {
    if (!carregando) {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = 'vendor/maplibre/maplibre-gl.css';
      document.head.append(css);
      carregando = carregarScript('vendor/maplibre/maplibre-gl.js').then(() =>
        carregarScript('vendor/maplibre/leaflet-maplibre-gl.js')
      );
    }
    return carregando;
  }

  // Camada vetorial numa pane própria: acima do satélite (tilePane, 200) e abaixo dos desenhos (overlayPane, 400)
  async function criarCamada(map) {
    await carregar();
    if (!map.getPane('rotulosOsm')) map.createPane('rotulosOsm').style.zIndex = 250;
    return L.maplibreGL({
      style: ESTILO,
      pane: 'rotulosOsm',
      interactive: false,
    });
  }

  return { criarCamada };
})();
