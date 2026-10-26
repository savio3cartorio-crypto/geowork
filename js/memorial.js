/*
 * Gerador de memorial descritivo em texto, a partir dos anéis UTM SIRGAS 2000.
 * Depende de Geo (js/geo.js), que deve ser carregado antes.
 *
 * Opções de build():
 *   format         'tabular' (tabela de vértices) ou 'corrido' (texto notarial)
 *   codes          código de cada vértice (ex.: ABCD-M-0001); usa o padrão se faltar
 *   confrontantes  nome de confrontante por trecho; usa '[a informar]' se faltar
 *
 * Só o contorno externo de cada polígono é descrito; furos ainda não entram no memorial.
 */
const Memorial = (() => {
  const num = (value, digits) =>
    value.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits });

  // Prefixo do código do vértice: quatro letras do nome do imóvel
  function codePrefix(name) {
    const letters = String(name || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    return (letters + 'XXXX').slice(0, 4);
  }

  // Padrão INCRA: ABCD-M-0001 (M = marco inicial, V = vértice)
  function vertexCode(prefix, index) {
    const type = index === 0 ? 'M' : 'V';
    return `${prefix}-${type}-${String(index + 1).padStart(4, '0')}`;
  }

  function describePolygon(rings, prefix, codes) {
    const ring = rings[0];
    const vertices = ring.slice(0, -1).map(([e, n], i) => ({
      code: codes[i] || vertexCode(prefix, i),
      e,
      n,
    }));
    const segments = Geo.ringSegments(ring);
    const perimeterM = segments.reduce((sum, s) => sum + s.distanceM, 0);
    return { vertices, segments, perimeterM };
  }

  function tabular(name, summary, polygons) {
    const lines = [
      'MEMORIAL DESCRITIVO (FORMATO TABULAR)',
      `Imóvel: ${name}`,
      `Sistema de referência: SIRGAS 2000 / UTM zona ${summary.zone}S (EPSG:${summary.epsg})`,
      `Área (planimétrica): ${num(summary.areaUtmHa, 4)} ha · ${num(summary.areaUtmHa * 10000, 2)} m²`,
      `Área (geodésica): ${num(summary.areaGeodesicHa, 4)} ha`,
      '',
    ];
    polygons.forEach((poly, pi) => {
      if (polygons.length > 1) lines.push(`Polígono ${pi + 1}`);
      lines.push('Vértice | E (m) | N (m) | Azimute | Rumo | Distância (m)');
      poly.vertices.forEach((v, i) => {
        const seg = poly.segments[i];
        const tail = seg ? [Geo.dms(seg.azimuth), seg.bearing, num(seg.distanceM, 2)] : ['-', '-', '-'];
        lines.push([v.code, num(v.e, 3), num(v.n, 3), ...tail].join(' | '));
      });
      lines.push(`Perímetro: ${num(poly.perimeterM, 2)} m`, '');
    });
    return lines.join('\n');
  }

  function corrido(name, summary, polygons, confrontantes) {
    const paragraphs = polygons.map((poly, pi) => {
      const { vertices, segments } = poly;
      const head = polygons.length > 1 ? `Polígono ${pi + 1}: ` : '';
      const start = vertices[0];
      let text =
        `${head}Inicia-se a descrição no vértice ${start.code}, de coordenadas E = ${num(start.e, 3)} m ` +
        `e N = ${num(start.n, 3)} m. `;
      segments.forEach((seg, i) => {
        const to = vertices[seg.to % vertices.length]; // o último trecho volta ao vértice inicial
        const confrontante = confrontantes[seg.from] || '[a informar]';
        const verb = i === 0 ? 'Daí segue' : 'Segue';
        text +=
          `${verb} por ${num(seg.distanceM, 2)} metros, com rumo ${seg.bearing} ` +
          `(azimute ${Geo.dms(seg.azimuth)}), confrontando com ${confrontante}, até o vértice ${to.code}, ` +
          `de coordenadas E = ${num(to.e, 3)} m e N = ${num(to.n, 3)} m. `;
      });
      text +=
        `Encerra-se a descrição no vértice inicial ${start.code}, perímetro de ${num(poly.perimeterM, 2)} metros` +
        `, área de ${num(summary.areaUtmHa, 4)} ha (${num(summary.areaUtmHa * 10000, 2)} m²), ` +
        `referidas ao SIRGAS 2000 / UTM zona ${summary.zone}S.`;
      return text;
    });
    return ['MEMORIAL DESCRITIVO (TEXTO CORRIDO)', `Imóvel: ${name}`, '', ...paragraphs].join('\n\n');
  }

  // summary vem de Geo.summarize(feature)
  function build(name, summary, { format = 'tabular', codes = [], confrontantes = [] } = {}) {
    const prefix = codePrefix(name);
    const polygons = summary.utmPolygons.map(rings => describePolygon(rings, prefix, codes));
    return format === 'corrido'
      ? corrido(name, summary, polygons, confrontantes)
      : tabular(name, summary, polygons);
  }

  return { build, codePrefix, vertexCode };
})();
