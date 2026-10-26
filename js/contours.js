/* Curvas de nível a partir de pontos cotados (CSV com lat/lon ou E/N e uma coluna de altitude).
   Interpola por IDW (inverso do quadrado da distância) numa grade em UTM e extrai as isolinhas com turf.isolines.
   Usa Geo e turf. Não grava nada no servidor: o resultado é um GeoJSON para mostrar e baixar. */
const Contours = (() => {
  const NOS_POR_LADO = 120; // resolução da grade; o lado maior tem 120 nós
  const MAX_NIVEIS = 500;
  const NOMES_ALTITUDE = ['alt', 'altitude', 'z', 'cota', 'elevacao', 'elevação'];

  function parseCsv(text, zone) {
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (lines.length < 2) throw new Error('CSV sem pontos');
    const sep = lines[0].includes(';') ? ';' : ',';
    const header = lines[0].split(sep).map(h => h.trim().toLowerCase());
    const col = names => header.findIndex(h => names.includes(h));
    const iLat = col(['lat', 'latitude']);
    const iLon = col(['lon', 'lng', 'long', 'longitude']);
    const iE = col(['e', 'este', 'x', 'easting']);
    const iN = col(['n', 'norte', 'y', 'northing']);
    const iZ = col(NOMES_ALTITUDE);
    const useLatLon = iLat >= 0 && iLon >= 0;
    if (!useLatLon && !(iE >= 0 && iN >= 0)) throw new Error('CSV precisa de colunas lat/lon ou E/N');
    if (iZ < 0) throw new Error('CSV precisa de uma coluna de altitude (alt, altitude, z ou cota)');

    const num = v => parseFloat(String(v).trim().replace(',', '.'));
    const pontos = [];
    lines.slice(1).forEach(line => {
      const c = line.split(sep);
      const z = num(c[iZ]);
      const [lng, lat] = useLatLon ? [num(c[iLon]), num(c[iLat])] : Geo.fromUtmPoint(num(c[iE]), num(c[iN]), zone);
      if ([lng, lat, z].every(Number.isFinite)) pontos.push({ lng, lat, z });
    });
    if (pontos.length < 3) throw new Error('são necessários pelo menos 3 pontos cotados');
    return pontos;
  }

  // Valor interpolado em (E, N): média ponderada pelo inverso do quadrado da distância
  function idw(pontos, E, N) {
    let soma = 0;
    let pesos = 0;
    for (const p of pontos) {
      const d2 = (p.E - E) ** 2 + (p.N - N) ** 2;
      if (d2 === 0) return p.z;
      const w = 1 / d2;
      soma += w * p.z;
      pesos += w;
    }
    return soma / pesos;
  }

  // Triangulação de Delaunay dos pontos (turf.tin). Cada triângulo guarda os três vértices e as altitudes
  function triangulosTin(utm) {
    const pontos = turf.featureCollection(utm.map(p => turf.point([p.E, p.N], { z: p.z })));
    return turf.tin(pontos, 'z').features.map(f => {
      const [[x0, y0], [x1, y1], [x2, y2]] = f.geometry.coordinates[0];
      const { a, b, c } = f.properties;
      return { x: [x0, x1, x2], y: [y0, y1, y2], z: [a, b, c] };
    });
  }

  // Interpolação linear no triângulo que contém (E, N). Fora da área dos pontos, usa o plano do triângulo
  // mais próximo (extrapolação linear). Retorna null só se não houver triângulos.
  function valorTin(triangulos, E, N) {
    let maisProximo = null;
    let menorDistancia = Infinity;
    for (const t of triangulos) {
      const [x0, x1, x2] = t.x;
      const [y0, y1, y2] = t.y;
      const det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
      if (det === 0) continue;
      const l0 = ((y1 - y2) * (E - x2) + (x2 - x1) * (N - y2)) / det;
      const l1 = ((y2 - y0) * (E - x2) + (x0 - x2) * (N - y2)) / det;
      const l2 = 1 - l0 - l1;
      const valor = l0 * t.z[0] + l1 * t.z[1] + l2 * t.z[2];
      const tolerancia = -1e-9;
      if (l0 >= tolerancia && l1 >= tolerancia && l2 >= tolerancia) return valor;
      const distancia = ((x0 + x1 + x2) / 3 - E) ** 2 + ((y0 + y1 + y2) / 3 - N) ** 2;
      if (distancia < menorDistancia) {
        menorDistancia = distancia;
        maisProximo = valor;
      }
    }
    return maisProximo;
  }

  // Monta as curvas de nível. intervalo em metros (equidistância). A zona UTM vem do centro dos pontos.
  function build(pontos, intervalo) {
    if (!(intervalo > 0)) throw new Error('equidistância precisa ser maior que zero');
    const lngMedio = pontos.reduce((soma, p) => soma + p.lng, 0) / pontos.length;
    const zone = Geo.utmZoneFromLon(lngMedio);
    const utm = pontos.map(p => {
      const [E, N] = Geo.toUtmPoint(p.lng, p.lat, zone);
      return { E, N, z: p.z };
    });
    const minE = Math.min(...utm.map(p => p.E));
    const maxE = Math.max(...utm.map(p => p.E));
    const minN = Math.min(...utm.map(p => p.N));
    const maxN = Math.max(...utm.map(p => p.N));
    const minZ = Math.min(...utm.map(p => p.z));
    const maxZ = Math.max(...utm.map(p => p.z));

    const passo = Math.max(maxE - minE, maxN - minN, 1) / (NOS_POR_LADO - 1);
    const nx = Math.ceil((maxE - minE) / passo) + 1;
    const ny = Math.ceil((maxN - minN) / passo) + 1;

    // Interpolação linear no TIN; IDW só se a triangulação não tiver triângulos válidos
    const triangulos = triangulosTin(utm);
    const valorEm = (E, N) => valorTin(triangulos, E, N) ?? idw(utm, E, N);

    // Grade regular em UTM, linha a linha (o turf.isolines exige a ordem por linhas)
    const nos = [];
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const E = minE + i * passo;
        const N = minN + j * passo;
        nos.push(turf.point([E, N], { z: valorEm(E, N) }));
      }
    }

    const primeiro = Math.ceil(minZ / intervalo) * intervalo;
    const niveis = [];
    for (let cota = primeiro; cota <= maxZ + 1e-9; cota += intervalo) niveis.push(cota);
    if (!niveis.length) throw new Error('nenhuma curva dentro da faixa de altitudes dos pontos');
    if (niveis.length > MAX_NIVEIS) throw new Error(`${niveis.length} curvas; aumente a equidistância (máximo ${MAX_NIVEIS})`);

    const isolinhas = turf.isolines(turf.featureCollection(nos), niveis, { zProperty: 'z' });
    const features = [];
    isolinhas.features.forEach(f => {
      const cota = f.properties.z;
      const partes = f.geometry.coordinates.map(linha =>
        linha.map(([E, N]) => Geo.fromUtmPoint(E, N, zone))
      );
      features.push(turf.multiLineString(partes, { cota: Math.round(cota * 1000) / 1000 }));
    });

    return {
      geojson: turf.featureCollection(features),
      intervalo,
      minZ,
      maxZ,
      pontos: pontos.length,
    };
  }

  return { parseCsv, build };
})();
