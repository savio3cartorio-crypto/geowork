/*
 * Exportadores de texto: DXF (R12 mínimo) e planilha CSV para conferência do SIGEF.
 * Não geram .ODS nem .DOCX; isso fica para um backend (ver docs/ARQUITETURA.md).
 */
const Exporter = (() => {
  const csvCell = value => {
    const text = String(value);
    return /[";\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };

  // Números com vírgula decimal, para abrir corretamente no Excel em português
  const dec = (value, digits) =>
    typeof value === 'number' ? value.toFixed(digits).replace('.', ',') : value;

  // features: [{ layer, points: [[E, N], ...], closed }]. Todas na mesma zona UTM.
  function toDxf(features) {
    const out = ['0', 'SECTION', '2', 'ENTITIES'];
    features.forEach(({ layer, points, closed }) => {
      out.push('0', 'LWPOLYLINE', '8', layer, '90', String(points.length), '70', closed ? '1' : '0');
      points.forEach(([x, y]) => out.push('10', x.toFixed(3), '20', y.toFixed(3)));
    });
    out.push('0', 'ENDSEC', '0', 'EOF');
    return out.join('\n') + '\n';
  }

  // Duas seções: identificação do imóvel e vértices do perímetro
  function sigefCsv({ identificacao, vertices }) {
    const rows = [['identificacao'], ['campo', 'valor']];
    Object.entries(identificacao).forEach(([campo, valor]) => rows.push([campo, valor]));
    rows.push([], ['perimetro_1'], ['codigo', 'E', 'N', 'altitude', 'sigma', 'metodo', 'azimute', 'distancia_m']);
    vertices.forEach(v =>
      rows.push([v.codigo, dec(v.E, 3), dec(v.N, 3), v.altitude, v.sigma, v.metodo, v.azimute, dec(v.distancia, 2)])
    );
    return rows.map(row => row.map(csvCell).join(';')).join('\n');
  }

  return { toDxf, sigefCsv };
})();
