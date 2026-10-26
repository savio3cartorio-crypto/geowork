/*
 * Funções geoespaciais puras (sem tocar no DOM).
 * Dependências globais: proj4 e turf (carregadas no index.html).
 *
 * Sistema de referência de trabalho: SIRGAS 2000 / UTM (EPSG:31978 a 31985),
 * o padrão exigido pelo CAR, SIGEF e INCRA para cálculo planimétrico de área.
 */
const Geo = (() => {
  const SIRGAS_ELLPS = '+ellps=GRS80 +units=m +no_defs';

  // Zona UTM a partir da longitude: 18S = EPSG:31978 ... 25S = EPSG:31985
  function utmZoneFromLon(lon) {
    return Math.floor((lon + 180) / 6) + 1;
  }

  function utmEpsg(zone) {
    return 31978 + (zone - 18);
  }

  function ensureUtmDef(zone) {
    const code = `EPSG:${utmEpsg(zone)}`;
    if (!proj4.defs(code)) {
      proj4.defs(code, `+proj=utm +zone=${zone} +south ${SIRGAS_ELLPS}`);
    }
    return code;
  }

  // Polígono é um array de anéis (o primeiro é o contorno externo, os demais são furos)
  function polygonsOf(geometry) {
    return geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
  }

  // Área planimétrica (fórmula de Gauss) de um anel fechado
  function ringArea(ring) {
    let sum = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const [x1, y1] = ring[i];
      const [x2, y2] = ring[i + 1];
      sum += x1 * y2 - x2 * y1;
    }
    return Math.abs(sum) / 2;
  }

  function projectedAreaM2(polygons) {
    return polygons.reduce((total, rings) => {
      const [outer, ...holes] = rings;
      return total + ringArea(outer) - holes.reduce((s, h) => s + ringArea(h), 0);
    }, 0);
  }

  // Projeta um Feature WGS84 para a zona UTM SIRGAS 2000 do seu centroide
  function toUtm(feature) {
    const [lon] = turf.centroid(feature).geometry.coordinates;
    const zone = utmZoneFromLon(lon);
    const code = ensureUtmDef(zone);
    const polygons = polygonsOf(feature.geometry).map(rings =>
      rings.map(ring => ring.map(([x, y]) => proj4('EPSG:4326', code, [x, y])))
    );
    return { zone, epsg: utmEpsg(zone), polygons };
  }

  function summarize(feature) {
    const utm = toUtm(feature);
    const vertices = utm.polygons.reduce(
      (n, rings) => n + rings.reduce((m, ring) => m + ring.length - 1, 0),
      0
    );
    return {
      zone: utm.zone,
      epsg: utm.epsg,
      vertices,
      areaGeodesicHa: turf.area(feature) / 10000,
      areaUtmHa: projectedAreaM2(utm.polygons) / 10000,
      perimeterKm: turf.length(turf.polygonToLine(feature), { units: 'kilometers' }),
      utmPolygons: utm.polygons,
    };
  }

  // Pares de parcelas que se sobrepõem (acima de 1 m²)
  function findOverlaps(parcels) {
    const overlaps = [];
    for (let i = 0; i < parcels.length; i++) {
      for (let j = i + 1; j < parcels.length; j++) {
        let inter = null;
        try {
          inter = turf.intersect(parcels[i].feature, parcels[j].feature);
        } catch (err) {
          inter = null;
        }
        if (inter) {
          const areaHa = turf.area(inter) / 10000;
          if (areaHa > 0.0001) overlaps.push({ a: parcels[i], b: parcels[j], areaHa });
        }
      }
    }
    return overlaps;
  }

  // Verifica se as coordenadas parecem estar em graus (WGS84/SIRGAS 2000 geográficas)
  function looksGeographic(geometry) {
    return polygonsOf(geometry).every(rings =>
      rings.every(ring => ring.every(([x, y]) => Math.abs(x) <= 180 && Math.abs(y) <= 90))
    );
  }

  // Azimute de grade (norte da quadrícula UTM), de 0 a 360 graus no sentido horário
  function gridAzimuth(from, to) {
    const dE = to[0] - from[0];
    const dN = to[1] - from[1];
    return ((Math.atan2(dE, dN) * 180) / Math.PI + 360) % 360;
  }

  // Graus decimais -> 45°30'15,2"
  function dms(decimal) {
    const tenths = Math.round(decimal * 36000); // precisão de 0,1 segundo
    const d = Math.floor(tenths / 36000);
    const m = Math.floor((tenths - d * 36000) / 600);
    const s = (tenths - d * 36000 - m * 600) / 10;
    return `${d}°${String(m).padStart(2, '0')}'${s.toFixed(1).replace('.', ',').padStart(4, '0')}"`;
  }

  // Rumo de levantamento: N 45°30'15,2" E
  function bearingLabel(azimuth) {
    if (azimuth <= 90) return `N ${dms(azimuth)} E`;
    if (azimuth <= 180) return `S ${dms(180 - azimuth)} E`;
    if (azimuth <= 270) return `S ${dms(azimuth - 180)} W`;
    return `N ${dms(360 - azimuth)} W`;
  }

  // Segmentos de um anel UTM: azimute, rumo e distância plana (m) entre vértices consecutivos
  function ringSegments(ring) {
    const segments = [];
    for (let i = 0; i < ring.length - 1; i++) {
      const from = ring[i];
      const to = ring[i + 1];
      const azimuth = gridAzimuth(from, to);
      segments.push({
        from: i,
        to: i + 1,
        azimuth,
        bearing: bearingLabel(azimuth),
        distanceM: Math.hypot(to[0] - from[0], to[1] - from[1]),
      });
    }
    return segments;
  }

  // Validação topológica básica: anel fechado e sem auto-interseção
  function validate(feature) {
    const issues = new Set();
    polygonsOf(feature.geometry).forEach(rings =>
      rings.forEach(ring => {
        const first = ring[0];
        const last = ring[ring.length - 1];
        if (first[0] !== last[0] || first[1] !== last[1]) issues.add('anel não fechado');
      })
    );
    const kinks = turf.kinks(feature).features.length;
    if (kinks) issues.add(`${kinks} ponto(s) de auto-interseção`);
    return Array.from(issues);
  }

  // [lng, lat] <-> [E, N] na zona UTM indicada (SIRGAS 2000; a diferença para WGS84 é sub-métrica)
  function toUtmPoint(lng, lat, zone) {
    return proj4('EPSG:4326', ensureUtmDef(zone), [lng, lat]);
  }

  function fromUtmPoint(E, N, zone) {
    return proj4(ensureUtmDef(zone), 'EPSG:4326', [E, N]);
  }

  // Área com sinal (shoelace): positiva se o anel gira no sentido anti-horário
  function signedArea(points) {
    let sum = 0;
    for (let i = 0; i < points.length; i++) {
      const [x1, y1] = points[i];
      const [x2, y2] = points[(i + 1) % points.length];
      sum += x1 * y2 - x2 * y1;
    }
    return sum / 2;
  }

  // Ângulos internos (graus) de cada vértice de um anel planar (UTM). Aceita anel com ou sem o ponto final repetido.
  // Um vértice convexo tem ângulo interno < 180°; um côncavo (reentrante), > 180°.
  function interiorAngles(ring) {
    const pts = ring.length > 1 && ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1]
      ? ring.slice(0, -1)
      : ring;
    const orientation = Math.sign(signedArea(pts)) || 1;
    return pts.map((c, i) => {
      const p = pts[(i - 1 + pts.length) % pts.length];
      const n = pts[(i + 1) % pts.length];
      const ux = p[0] - c[0];
      const uy = p[1] - c[1];
      const vx = n[0] - c[0];
      const vy = n[1] - c[1];
      const dot = ux * vx + uy * vy;
      const cross = (c[0] - p[0]) * (n[1] - c[1]) - (c[1] - p[1]) * (n[0] - c[0]);
      const alpha = (Math.acos(Math.max(-1, Math.min(1, dot / (Math.hypot(ux, uy) * Math.hypot(vx, vy))))) * 180) / Math.PI;
      return cross * orientation > 0 ? alpha : 360 - alpha;
    });
  }

  // Paralela planar (em UTM) por deslocamento das arestas e interseção das retas vizinhas.
  // closed = true: contorno fechado (pode vir com o ponto final repetido).
  // Distância positiva expande o polígono, ou vai à esquerda do traçado (linha).
  // Distância negativa faz o contrário. Cantos agudos podem gerar auto-interseção: validar depois.
  function offsetPlanar(points, d, closed) {
    const pts = closed && points.length > 1 && points[0][0] === points.at(-1)[0] && points[0][1] === points.at(-1)[1]
      ? points.slice(0, -1)
      : points.slice();
    const n = pts.length;
    const sign = closed ? -Math.sign(signedArea(pts)) : 1; // para anel anti-horário, "fora" é a direita

    const edgeCount = closed ? n : n - 1;
    const lines = [];
    for (let i = 0; i < edgeCount; i++) {
      const a = pts[i];
      const b = pts[(i + 1) % n];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const len = Math.hypot(dx, dy);
      const nx = (-dy / len) * sign;
      const ny = (dx / len) * sign;
      lines.push({ p: [a[0] + d * nx, a[1] + d * ny], u: [dx, dy] });
    }

    // Interseção de duas retas deslocadas; se paralelas, usa o ponto deslocado da primeira
    const intersect = (r1, r2) => {
      const cross = (a, b) => a[0] * b[1] - a[1] * b[0];
      const denom = cross(r1.u, r2.u);
      if (Math.abs(denom) < 1e-9 * Math.hypot(...r1.u) * Math.hypot(...r2.u)) return r2.p;
      const w = [r2.p[0] - r1.p[0], r2.p[1] - r1.p[1]];
      const t = cross(w, r2.u) / denom;
      return [r1.p[0] + t * r1.u[0], r1.p[1] + t * r1.u[1]];
    };

    if (closed) {
      return lines.map((line, i) => intersect(lines[(i - 1 + n) % n], line));
    }
    const result = [lines[0].p];
    for (let i = 1; i < lines.length; i++) result.push(intersect(lines[i - 1], lines[i]));
    const last = lines.at(-1);
    result.push([last.p[0] + last.u[0], last.p[1] + last.u[1]]);
    return result;
  }

  return {
    utmZoneFromLon,
    offsetPlanar,
    signedArea,
    toUtmPoint,
    fromUtmPoint,
    utmEpsg,
    summarize,
    findOverlaps,
    looksGeographic,
    gridAzimuth,
    dms,
    bearingLabel,
    ringSegments,
    validate,
    interiorAngles,
  };
})();
