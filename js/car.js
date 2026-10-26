/*
 * Regras do CAR sobre as camadas do serviço: quadro de áreas e pré-análise antes da inscrição.
 * Base: Lei 12.651/2012 e os critérios do Módulo de Análise do SICAR-SP (manual v3, 2016).
 * Recortes e áreas em UTM SIRGAS 2000 (zona do perímetro), em coordenadas planas, como a área do perímetro.
 * Usa Geo e turf. Não toca no DOM: devolve números e mensagens para a interface mostrar.
 */
const Car = (() => {
  const TOLERANCIA_M2 = 1; // sobras abaixo de 1 m² são ruído de desenho ou arredondamento
  // Lascas ao longo de divisas comuns: um vértice no meio de uma divisa reta em graus não cai exatamente
  // sobre o segmento reto em UTM. Sobra com largura média (área / perímetro) abaixo disso é ignorada.
  const LARGURA_LASCA_M = 0.05;
  const TOLERANCIA_VEGETACAO_M2 = 100; // vegetação fora de APP abaixo disso não muda a análise da RL
  const LIMITE_PEQUENA_MF = 4; // pequena propriedade ou posse rural: até 4 módulos fiscais (art. 3º, V)
  // Diferença aceita entre área desenhada e da matrícula. O SICAR-SP usa um percentual por classe de tamanho;
  // este é o valor adotado pelo GeoWork até haver a tabela oficial.
  const TOLERANCIA_AREA_DOC_PCT = 5;
  const RL_PERCENTUAL_PADRAO = 20; // fora da Amazônia Legal (art. 12, II)

  const COBERTURA = ['vegetacao_nativa', 'area_consolidada', 'pousio'];
  // A RL vinculada à compensação de outro imóvel serve ao outro imóvel: não conta para o mínimo deste
  const RL_DO_IMOVEL = ['rl', 'rl_averbada', 'rl_aprovada'];
  const RL_TODAS = [...RL_DO_IMOVEL, 'rl_compensacao'];
  const POLIGONOS = [...COBERTURA, 'servidao', 'app', 'uso_restrito', ...RL_TODAS, 'edificacao'];

  const fecharAnel = ring => {
    const [a, b] = [ring[0], ring[ring.length - 1]];
    return a[0] === b[0] && a[1] === b[1] ? ring : [...ring, a];
  };
  const poligonoUtm = (ring, zone) =>
    turf.polygon([fecharAnel(ring).map(([lng, lat]) => Geo.toUtmPoint(lng, lat, zone))]);

  // Área plana (m²) de Polygon ou MultiPolygon: anel externo menos os buracos
  function areaM2(feature) {
    if (!feature) return 0;
    const g = feature.geometry;
    const poligonos = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    return poligonos.reduce(
      (total, aneis) =>
        total + aneis.reduce((soma, anel, i) => soma + (i === 0 ? 1 : -1) * Math.abs(Geo.signedArea(anel)), 0),
      0
    );
  }

  function perimetroM(feature) {
    if (!feature) return 0;
    const g = feature.geometry;
    const poligonos = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    let total = 0;
    poligonos.forEach(aneis =>
      aneis.forEach(anel => {
        for (let i = 1; i < anel.length; i++) total += Math.hypot(anel[i][0] - anel[i - 1][0], anel[i][1] - anel[i - 1][1]);
      })
    );
    return total;
  }

  // Sobra ou sobreposição que importa: acima de 1 m² e mais larga que uma lasca de divisa
  function areaRelevanteM2(feature) {
    const m2 = areaM2(feature);
    if (m2 <= TOLERANCIA_M2) return 0;
    return m2 / perimetroM(feature) < LARGURA_LASCA_M ? 0 : m2;
  }

  const uniao = poligonos => poligonos.reduce((acc, p) => (acc ? turf.union(acc, p) : p), null);
  const intersecao = (a, b) => (a && b ? turf.intersect(a, b) : null);
  const diferenca = (a, b) => (!a ? null : !b ? a : turf.difference(a, b));
  const ha = m2 => m2 / 10000;
  // Números das mensagens no formato brasileiro (vírgula decimal)
  const num = (v, casas) => v.toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });

  /*
   * entrada: { camadas: {categoria: [anéis [lng, lat]]}, rotulos: {categoria: texto},
   *            mfHa, rlPercentual, areaDocumentalHa }
   * Devolve { quadro, checks }. checks: [nível, texto], com nível 'ok', 'info', 'aviso' ou 'erro'.
   */
  function analisar({ camadas, rotulos, mfHa, rlPercentual, areaDocumentalHa }) {
    const checks = [];
    const nome = cat => rotulos[cat] || cat;
    const perimetro = camadas.perimetro && camadas.perimetro[0];
    if (!perimetro) {
      return { quadro: null, checks: [['aviso', 'Desenhe ou importe o perímetro para calcular o quadro de áreas.']] };
    }

    const lngMedio = perimetro.reduce((t, p) => t + p[0], 0) / perimetro.length;
    const zone = Geo.utmZoneFromLon(lngMedio);
    const imovel = poligonoUtm(perimetro, zone);
    const areaImovelM2 = areaM2(imovel);

    // União de cada camada, a parte dentro do imóvel e a sobra fora dele
    const dentro = {};
    POLIGONOS.forEach(cat => {
      const aneis = (camadas[cat] || []).filter(r => r.length >= 3);
      if (!aneis.length) return;
      let geom;
      try {
        geom = uniao(aneis.map(r => poligonoUtm(r, zone)));
      } catch (err) {
        checks.push(['erro', `${nome(cat)}: geometria inválida (auto-interseção?). Corrija para calcular as áreas.`]);
        return;
      }
      dentro[cat] = intersecao(geom, imovel);
      const foraM2 = areaRelevanteM2(diferenca(geom, imovel));
      if (foraM2) {
        checks.push(['erro', `${nome(cat)} ultrapassa o perímetro em ${num(foraM2, 1)} m².`]);
      }
    });
    const haDe = cat => ha(areaM2(dentro[cat]));

    const rlGeom = uniao(RL_DO_IMOVEL.map(cat => dentro[cat]).filter(Boolean));
    const servidaoHa = haDe('servidao');
    const areaImovelHa = ha(areaImovelM2);
    const areaLiquidaHa = areaImovelHa - servidaoHa;
    const percentual = rlPercentual || RL_PERCENTUAL_PADRAO;
    const rlTotalHa = ha(areaM2(rlGeom));
    const rlMinimaHa = (areaLiquidaHa * percentual) / 100;
    const modulos = mfHa ? areaImovelHa / mfHa : null;

    const classes = {};
    POLIGONOS.forEach(cat => (classes[cat] = haDe(cat)));
    const quadro = { zone, areaImovelHa, servidaoHa, areaLiquidaHa, classes, rlTotalHa, rlMinimaHa, percentual, modulos, mfHa };

    // Sede ou ponto de referência: obrigatório na etapa Geo do CAR
    const sede = camadas.sede && camadas.sede[0] && camadas.sede[0][0];
    if (!sede) {
      checks.push(['aviso', 'Marque a sede ou ponto de referência do imóvel (obrigatório no CAR).']);
    } else if (!turf.booleanPointInPolygon(turf.point(Geo.toUtmPoint(sede[0], sede[1], zone)), imovel)) {
      checks.push(['erro', 'A sede ou ponto de referência está fora do perímetro.']);
    } else {
      checks.push(['ok', 'Sede ou ponto de referência dentro do imóvel.']);
    }

    if (!COBERTURA.some(cat => dentro[cat])) {
      checks.push(['aviso', 'Nenhuma cobertura do solo desenhada (vegetação nativa, área consolidada ou pousio).']);
    }

    // Classes que não podem ocupar o mesmo lugar
    const pares = [
      ['vegetacao_nativa', 'area_consolidada'],
      ['vegetacao_nativa', 'pousio'],
      ['area_consolidada', 'pousio'],
      ...RL_TODAS.flatMap((a, i) => RL_TODAS.slice(i + 1).map(b => [a, b])),
      ...RL_TODAS.map(rl => [rl, 'servidao']),
    ];
    pares.forEach(([a, b]) => {
      const m2 = areaRelevanteM2(intersecao(dentro[a], dentro[b]));
      if (m2) checks.push(['aviso', `${nome(a)} e ${nome(b)} se sobrepõem em ${num(m2, 1)} m².`]);
    });

    // Reserva Legal mínima (art. 12) e regime da pequena propriedade (art. 67)
    const rlTexto = `RL de ${num(rlTotalHa, 4)} ha; mínimo de ${num(rlMinimaHa, 4)} ha (${percentual}% da área líquida)`;
    if (rlTotalHa + 0.0001 >= rlMinimaHa) {
      checks.push(['ok', `${rlTexto}: atende.`]);
    } else if (!modulos) {
      checks.push(['aviso', `${rlTexto}. Informe o módulo fiscal do município para saber se o imóvel tem até 4 MF.`]);
    } else if (modulos > LIMITE_PEQUENA_MF) {
      checks.push([
        'aviso',
        `${rlTexto}, em imóvel com ${num(modulos, 2)} MF. É critério de prioridade na análise do SICAR-SP: ` +
          'prever recomposição, regeneração ou compensação.',
      ]);
    } else {
      checks.push([
        'info',
        `${rlTexto}, em imóvel com ${num(modulos, 2)} MF (até 4 MF): a RL pode ser a vegetação nativa existente ` +
          'em 22/07/2008 (art. 67 da Lei 12.651/2012).',
      ]);
    }

    // APP computada na RL com vegetação nativa sobrando fora da APP (art. 15)
    const rlEmAppM2 = areaRelevanteM2(intersecao(rlGeom, dentro.app));
    if (rlEmAppM2) {
      const vegetacaoLivreM2 = areaRelevanteM2(diferenca(diferenca(dentro.vegetacao_nativa, dentro.app), rlGeom));
      checks.push(
        vegetacaoLivreM2 > TOLERANCIA_VEGETACAO_M2
          ? [
              'aviso',
              `A RL inclui ${num(ha(rlEmAppM2), 4)} ha de APP, mas há ${num(ha(vegetacaoLivreM2), 4)} ha de ` +
                'vegetação nativa fora de APP sem RL. É critério de prioridade no SICAR-SP (art. 15).',
            ]
          : ['info', `A RL inclui ${num(ha(rlEmAppM2), 4)} ha de APP (cômputo do art. 15).`]
      );
    }

    if ((camadas.hidro || []).length && !dentro.app) {
      checks.push(['aviso', "Há curso d'água desenhado e nenhuma APP declarada."]);
    }

    // Área desenhada x área da matrícula: diferença grande também é critério de prioridade no SICAR-SP
    if (areaDocumentalHa) {
      const difPct = ((areaImovelHa - areaDocumentalHa) / areaDocumentalHa) * 100;
      const texto = `Área desenhada ${num(areaImovelHa, 4)} ha × matrícula ${num(areaDocumentalHa, 4)} ha ` +
        `(${difPct >= 0 ? '+' : ''}${num(difPct, 2)}%)`;
      checks.push(Math.abs(difPct) > TOLERANCIA_AREA_DOC_PCT
        ? ['aviso', `${texto}: acima de ${TOLERANCIA_AREA_DOC_PCT}%. Confira o desenho ou a documentação.`]
        : ['ok', `${texto}.`]);
    } else {
      checks.push(['info', 'Informe a área da matrícula para comparar com a área desenhada.']);
    }

    return { quadro, checks };
  }

  // Faixa marginal de APP de curso d'água natural (Lei 12.651/2012, art. 4º, I), medida da borda do leito
  function faixaAppM(larguraM) {
    if (!(larguraM > 0)) return null;
    if (larguraM < 10) return 30;
    if (larguraM < 50) return 50;
    if (larguraM < 200) return 100;
    if (larguraM < 600) return 200;
    return 500;
  }

  // Anel de APP ao redor de cada linha de água: metade da largura do leito mais a faixa legal
  function appDeCursos(linhas, larguraM) {
    const raioKm = (larguraM / 2 + faixaAppM(larguraM)) / 1000;
    return linhas
      .filter(linha => linha.length >= 2)
      .flatMap(linha => {
        const buf = turf.buffer(turf.lineString(linha), raioKm, { units: 'kilometers', steps: 16 });
        return buf.geometry.type === 'Polygon' ? [buf.geometry.coordinates[0]] : buf.geometry.coordinates.map(p => p[0]);
      });
  }

  return { analisar, areaM2, COBERTURA, RL_DO_IMOVEL, RL_TODAS, faixaAppM, appDeCursos };
})();
