/*
 * Barra de comando estilo AutoCAD. Funções puras: interpretam o texto digitado
 * e calculam pontos. A ação sobre o mapa fica em app.js.
 */
const Cad = (() => {
  const COMMANDS = ['LIMPAR', 'PL', 'DIST', 'DESFAZER', 'FECHAR', 'SNAP', 'FILLET'];

  // "120.50 85.25" = distância em metros e azimute de grade em graus (horário, a partir do norte)
  function parse(input) {
    const text = input.trim().toUpperCase();
    if (!text) return null;
    if (COMMANDS.includes(text)) return { type: 'command', name: text };

    // "OFFSET 10": paralela de 10 m; negativo contrai (polígono) ou vai à direita (linha)
    const offset = text.match(/^OFFSET\s+(-?\d+(?:[.,]\d+)?)$/);
    if (offset) return { type: 'offset', dist: parseFloat(offset[1].replace(',', '.')) };

    const match = text.match(/^(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)$/);
    if (!match) return { type: 'error', message: `Comando não reconhecido: "${input.trim()}"` };

    const dist = parseFloat(match[1].replace(',', '.'));
    const az = parseFloat(match[2].replace(',', '.'));
    if (az >= 360) return { type: 'error', message: 'Azimute deve estar entre 0 e 359,99 graus.' };
    return { type: 'polar', dist, az };
  }

  // Ponto a partir de [E, N], distância e azimute de grade
  function polarPoint([E, N], dist, az) {
    const rad = (az * Math.PI) / 180;
    return [E + dist * Math.sin(rad), N + dist * Math.cos(rad)];
  }

  // Coordenadas decimais: "-23.02, -45.55" (lat, lng)
  function parseLatLng(text) {
    const match = text.trim().match(/^(-?\d+(?:\.\d+)?)[\s,;]+(-?\d+(?:\.\d+)?)$/);
    if (!match) return null;
    const lat = parseFloat(match[1]);
    const lng = parseFloat(match[2]);
    if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
    return { lat, lng };
  }

  return { parse, polarPoint, parseLatLng };
})();
