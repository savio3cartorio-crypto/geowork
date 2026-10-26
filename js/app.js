/* GeoWork: clientes, serviços, mapa, barra CAD e os quatro pilares. Usa Geo, Cad, Exporter e Memorial. */
(() => {
  const STORAGE_KEY = 'geowork:v1';
  const RL_PERCENT = 20; // referência de Reserva Legal; varia por bioma e região, confirmar com o RT
  const SNAP_PX = 12;
  // Mesmo padrão do servidor (backend/app/schemas.py): UF, código IBGE de 7 dígitos e 32 hexadecimais
  const PADRAO_CAR = /^[A-Z]{2}-\d{7}-[0-9A-F]{32}$/;

  const LAYERS = {
    perimetro: { label: 'Perímetro oficial do imóvel', color: '#38bdf8', closed: true, dxf: 'PERIMETRO' },
    rl: { label: 'Reserva Legal (CAR)', color: '#22c55e', closed: true, dxf: 'RESERVA_LEGAL' },
    app: { label: 'APP', color: '#60a5fa', closed: true, dxf: 'APP' },
    hidro: { label: "Curso d'água (linha)", color: '#0ea5e9', closed: false, dxf: 'HIDROGRAFIA' },
    edificacao: { label: 'Edificação / benfeitoria', color: '#f97316', closed: true, dxf: 'EDIFICACAO' },
  };

  const TIPOS_DIVISA = {
    PESSOA: 'Pessoa',
    FAIXA_DOMINIO: 'Faixa de domínio (DER/DNIT)',
    CORREGO_RIO: 'Córrego ou rio',
    ESTRADA: 'Estrada',
    OUTRO: 'Outro',
  };

  // Endpoints WMS herdados do protótipo original. Confirmar antes de usar em produção.
  const WMS = {
    sigef: { url: 'https://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=certificada_sigef_particular_sp', layers: 'certificada_sigef_particular_sp' },
    sicar: { url: 'https://geoserver.car.gov.br/geoserver/sicar/wms', layers: 'sicar:sicar_imoveis_sp' },
    igc: { url: 'http://datageo.ambiente.sp.gov.br/geoserver/datageo/ows', layers: 'datageo:LimiteMunicipal' },
    hidro: { url: 'http://datageo.ambiente.sp.gov.br/geoserver/datageo/ows', layers: 'datageo:Hidrografia' },
  };

  const el = id => document.getElementById(id);
  const fmt = (value, digits = 2) =>
    value.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const pad2 = n => String(n).padStart(2, '0');
  const uid = () => 'id' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const slug = text =>
    (text || 'imovel').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-zA-Z0-9]+/g, '_').toLowerCase();
  const escapeHtml = text =>
    String(text).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  // ---------- Estado persistido (por navegador) ----------
  function newService(clientId, name, rev = 0) {
    return {
      id: uid(),
      clientId,
      name,
      rev,
      mf: null,
      layers: { perimetro: [], rl: [], app: [], hidro: [], edificacao: [] },
      meta: { codes: [], vertex: [], segs: [] },
      docs: { matricula: '', incra: '', car: '' },
    };
  }

  function defaultState() {
    const clients = [
      { id: 'c1', name: 'Fazenda Santa Bárbara', doc: '123.456.789-00' },
      { id: 'c2', name: 'Sítio São José — Retificação', doc: 'Mat. 45.120' },
      { id: 'c3', name: 'Chácara Bela Vista — Usucapião', doc: '' },
    ];
    const srv1 = { ...newService('c1', 'Georreferenciamento SIGEF + CAR', 0), id: 'srv1' };
    const srv2 = { ...newService('c1', 'Desmembramento Gleba B', 1), id: 'srv2' };
    return { clients, services: [srv1, srv2], activeClientId: 'c1', activeServiceId: 'srv1' };
  }

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      if (saved && Array.isArray(saved.services)) return saved;
    } catch (err) {
      // dados corrompidos: volta ao exemplo
    }
    return defaultState();
  }

  function save() {
    if (remote.mode === 'api') return; // no modo remoto, a fonte da verdade é o servidor
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (err) {
      // armazenamento bloqueado: o painel segue funcionando sem persistir
    }
  }

  let state = load();

  // Estado de sessão, não salvo: camada ativa, rascunho em desenho e modo de base
  const ui = { layer: 'perimetro', draft: null, snap: false, mapMode: 'sat' };

  const svc = () => state.services.find(s => String(s.id) === String(state.activeServiceId));
  const zoneOf = lng => Geo.utmZoneFromLon(lng);

  // ---------- Modo remoto: backend FastAPI + PostGIS (mesma origem). Sem servidor, segue no navegador ----------
  const remote = { mode: 'local', falhaPendente: false };
  let syncQueue = Promise.resolve();
  const flushSync = () => syncQueue;

  async function api(path, options = {}) {
    const res = await fetch(path, {
      method: options.method || 'GET',
      headers: options.body ? { 'Content-Type': 'application/json' } : {},
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (res.status === 401) {
      // Sessão encerrada ou expirada: volta para o login sem mostrar erro de sincronização
      window.location.replace('/login');
      const err = new Error('Sessão encerrada');
      err.sessaoEncerrada = true;
      throw err;
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail;
      } catch (err) {
        // resposta sem JSON: fica com o texto do status
      }
      // Erros de validação do FastAPI vêm como lista de {loc, msg}; mostra só as mensagens
      if (Array.isArray(detail)) {
        detail = detail.map(d => String(d.msg || '').replace(/^Value error, /, '')).join('; ');
      }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  const emptyLayers = () => ({ perimetro: [], rl: [], app: [], hidro: [], edificacao: [] });
  const emptyMeta = () => ({ codes: [], vertex: [], segs: [] });
  const toNumber = v => {
    const n = parseFloat(String(v ?? '').replace(',', '.'));
    return Number.isFinite(n) ? n : null;
  };

  // Cabeçalho do serviço, sem camadas: a lista usa isso; o detalhe é carregado ao selecionar
  function summaryOf(x) {
    return {
      id: x.id,
      clientId: x.cliente_id,
      name: x.nome_imovel,
      rev: x.revisao,
      mf: null,
      remote: true,
      layers: emptyLayers(),
      meta: emptyMeta(),
      docs: { matricula: '', incra: '', car: '' },
    };
  }

  // Converte o detalhe do servidor para o formato que a interface usa
  function fromRemote(d) {
    const s = {
      ...summaryOf(d),
      mf: d.modulo_fiscal_ha ? Number(d.modulo_fiscal_ha) : null,
      docs: { matricula: d.matricula || '', incra: d.codigo_sncr || '', car: d.codigo_car || '' },
    };
    // Curvas de nível têm rota própria (mostrarCurvasSalvas): não entram nas camadas de desenho
    d.camadas.filter(c => c.categoria !== 'curvas').forEach(c => {
      const coords = c.geometry.type === 'Polygon' ? c.geometry.coordinates[0] : c.geometry.coordinates;
      s.layers[c.categoria].push(coords);
    });
    d.vertices.forEach(v => {
      s.meta.codes[v.indice] = v.codigo;
      s.meta.vertex[v.indice] = {
        metodo: v.metodo || '',
        altitude: v.altitude ?? '',
        sigmaX: v.sigma_x ?? '',
        sigmaY: v.sigma_y ?? '',
        sigmaZ: v.sigma_z ?? '',
      };
    });
    d.confrontantes.forEach(c => {
      s.meta.segs[c.indice] = {
        nome: c.nome,
        doc: c.cpf_cnpj || '',
        matricula: c.matricula || '',
        incra: c.codigo_incra_sncr || '',
        tipo: c.tipo_divisa,
      };
    });
    return s;
  }

  // Envia ao servidor o estado completo de um serviço: perímetro, camadas e dados cadastrais
  async function pushService(s) {
    const ring = s.layers.perimetro[0];
    if (ring) {
      const open = ring.slice(0, -1);
      await api(`/servicos/${s.id}/perimetro`, {
        method: 'PUT',
        body: {
          ring,
          vertices: open.map((_, i) => {
            const v = s.meta.vertex[i] || {};
            return {
              codigo: codeOf(s, i),
              metodo: v.metodo || null,
              altitude: toNumber(v.altitude),
              sigma_x: toNumber(v.sigmaX),
              sigma_y: toNumber(v.sigmaY),
              sigma_z: toNumber(v.sigmaZ),
            };
          }),
          confrontantes: open.flatMap((_, i) => {
            const c = s.meta.segs[i];
            if (!c || !c.nome) return [];
            return [
              {
                indice: i,
                nome: c.nome,
                tipo_divisa: c.tipo || 'PESSOA',
                cpf_cnpj: c.doc || null,
                matricula: c.matricula || null,
                codigo_incra_sncr: c.incra || null,
              },
            ];
          }),
        },
      });
    } else {
      await api(`/servicos/${s.id}/perimetro`, { method: 'DELETE' });
    }
    for (const cat of ['rl', 'app', 'hidro', 'edificacao']) {
      await api(`/servicos/${s.id}/camadas/${cat}`, { method: 'PUT', body: { features: s.layers[cat] } });
    }
    await api(`/servicos/${s.id}`, {
      method: 'PUT',
      body: {
        matricula: s.docs.matricula || null,
        codigo_sncr: s.docs.incra || null,
        codigo_car: s.docs.car || null,
        modulo_fiscal_ha: s.mf || null,
      },
    });
  }

  // Copia do serviço ativo entra numa fila, em ordem; a interface não espera o servidor
  function scheduleSync() {
    const s = svc();
    if (remote.mode !== 'api' || !s || !s.remote) return;
    const snapshot = JSON.parse(JSON.stringify(s));
    syncQueue = syncQueue
      .then(() => pushService(snapshot))
      .then(() => {
        remote.falhaPendente = false;
        msg('Salvo no servidor.');
      })
      .catch(err => {
        if (err.sessaoEncerrada) return;
        remote.falhaPendente = true;
        msg(`Falha ao salvar no servidor: ${err.message}. A última alteração ainda não foi gravada.`, true);
      });
  }

  async function loadRemoteServicos() {
    const [clientes, servicos] = await Promise.all([api('/clientes'), api('/servicos')]);
    state.clients = clientes.map(c => ({
      id: c.id,
      name: c.nome,
      doc: c.cpf_cnpj,
      contato: { email: c.email, telefone: c.telefone },
      endereco: enderecoDe(c),
    }));
    state.services = servicos.map(summaryOf);
  }

  async function loadServico(id) {
    await flushSync();
    const detail = await api(`/servicos/${id}`);
    state.services = state.services.map(x => (String(x.id) === String(id) ? fromRemote(detail) : x));
    mostrarCurvasSalvas(detail.camadas);
  }

  // Anexos PDF do serviço (ex.: demonstrativo do CAR); só existem no modo remoto
  let arquivos = [];

  function renderArquivos() {
    const input = el('arquivoInput');
    const lista = el('arquivoList');
    const s = svc();
    const ativo = remote.mode === 'api' && s && s.remote;
    input.disabled = !ativo;
    lista.replaceChildren();
    if (!ativo) {
      const li = document.createElement('li');
      li.className = 'text-slate-500';
      li.textContent = 'Anexos disponíveis com o servidor.';
      lista.append(li);
      return;
    }
    arquivos.forEach(a => {
      const li = document.createElement('li');
      const link = document.createElement('a');
      link.href = `/arquivos/${a.id}`;
      link.target = '_blank';
      link.rel = 'noopener';
      link.className = 'text-sky-400 hover:underline';
      link.textContent = a.nome_arquivo;
      li.append(link, document.createTextNode(` · ${a.categoria}`));
      lista.append(li);
    });
  }

  async function loadArquivos() {
    const s = svc();
    arquivos = [];
    try {
      if (remote.mode === 'api' && s && s.remote) arquivos = await api(`/servicos/${s.id}/arquivos`);
    } catch (err) {
      msg(`Erro ao carregar anexos: ${err.message}`, true);
    }
    renderArquivos();
  }

  async function fileToBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }

  // Troca o serviço ativo; no modo remoto carrega o detalhe antes de mostrar
  async function selectService(id) {
    state.activeServiceId = id ?? null;
    ui.draft = null;
    if (remote.mode !== 'api') {
      removerCurvas(); // no modo local as curvas não são salvas: não ficam de um serviço para outro
      return commitChanges();
    }
    if (id != null) await loadServico(id);
    else removerCurvas();
    await loadArquivos();
    refresh();
  }

  async function selectClient(id) {
    state.activeClientId = id;
    const first = state.services.find(x => String(x.clientId) === String(id));
    await selectService(first ? first.id : null);
  }

  async function novaRevisaoRemote(s) {
    try {
      await flushSync();
      if (!s) {
        const name = prompt('Nome do serviço:');
        if (!name) return;
        const criado = await api('/servicos', {
          method: 'POST',
          body: { cliente_id: Number(state.activeClientId), nome_imovel: name.trim() },
        });
        await loadRemoteServicos();
        await selectService(criado.id);
        return msg(`Serviço "${criado.nome_imovel}" criado no servidor.`);
      }
      const nova = await api(`/servicos/${s.id}/revisoes`, { method: 'POST' });
      await loadRemoteServicos();
      await selectService(nova.id);
      msg(`Rev ${pad2(nova.revisao)} criada no servidor a partir de Rev ${pad2(s.rev)}.`);
    } catch (err) {
      msg(`Falha ao criar serviço ou revisão: ${err.message}`, true);
    }
  }

  // OFFSET no servidor (ST_Buffer / ST_OffsetCurve). Antes, envia o estado atual para o índice bater.
  async function offsetRemote(s, dist) {
    try {
      await flushSync();
      const indice = s.layers[ui.layer].length - 1;
      await api(`/servicos/${s.id}/offset`, {
        method: 'POST',
        body: { categoria: ui.layer, indice, distancia_m: dist },
      });
      await loadServico(s.id);
      msg(`Paralela de ${fmt(dist, 2)} m criada no servidor (PostGIS).`);
    } catch (err) {
      msg(`OFFSET recusado pelo servidor: ${err.message}`, true);
    }
    refresh();
  }

  // ---------- Mapa ----------
  const map = L.map('map', { center: [-23.045, -45.525], zoom: 15 });
  const mapGroup = L.layerGroup().addTo(map);

  const baseLayers = {
    sat: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19,
      attribution: 'Imagens &copy; Esri',
    }),
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap',
    }),
  };

  const wmsLayers = Object.fromEntries(
    Object.entries(WMS).map(([key, cfg]) => [
      key,
      L.tileLayer.wms(cfg.url, { layers: cfg.layers, format: 'image/png', transparent: true, opacity: 0.7 }),
    ])
  );

  function setBaseMode(mode) {
    Object.values(baseLayers).forEach(layer => map.removeLayer(layer));
    if (mode !== 'cad') baseLayers[mode].addTo(map);
    el('map').classList.toggle('cad-mode', mode === 'cad');
    ui.mapMode = mode;
    document.querySelectorAll('[data-mode]').forEach(btn => {
      const on = btn.dataset.mode === mode;
      btn.classList.toggle('bg-sky-600', on);
      btn.classList.toggle('text-white', on);
      btn.classList.toggle('bg-slate-800', !on);
      btn.classList.toggle('text-slate-300', !on);
    });
  }

  // ---------- Mensagens ----------
  function msg(text, isError = false) {
    const box = el('status');
    box.textContent = text;
    box.className = `text-xs px-4 py-1 ${isError ? 'text-rose-400' : 'text-emerald-400'}`;
  }

  // ---------- Geometria ----------
  const closedPolygon = ring => ({
    type: 'Feature',
    properties: {},
    geometry: { type: 'Polygon', coordinates: [ring] },
  });

  const closeRing = ring => {
    const first = ring[0];
    const last = ring[ring.length - 1];
    return first[0] === last[0] && first[1] === last[1] ? ring : [...ring, first];
  };

  const ringAreaHa = ring => Geo.summarize(closedPolygon(ring)).areaUtmHa;

  // Contexto do perímetro oficial: null se ainda não existe
  function perimeterCtx() {
    const s = svc();
    const ring = s && s.layers.perimetro[0];
    if (!ring) return null;
    const feature = closedPolygon(ring);
    const summary = Geo.summarize(feature);
    return {
      ring,
      open: ring.slice(0, -1),
      feature,
      summary,
      segments: Geo.ringSegments(summary.utmPolygons[0][0]),
    };
  }

  const perimeterM = pm => pm.segments.reduce((total, seg) => total + seg.distanceM, 0);

  function codeOf(s, i) {
    return s.meta.codes[i] || Memorial.vertexCode(Memorial.codePrefix(s.name), i);
  }

  function allVertices() {
    const s = svc();
    if (!s) return [];
    const points = [];
    Object.entries(s.layers).forEach(([cat, items]) =>
      items.forEach(item => points.push(...(LAYERS[cat].closed ? item.slice(0, -1) : item)))
    );
    if (ui.draft) points.push(...ui.draft.points);
    return points;
  }

  // Com SNAP ligado, o clique "gruda" no vértice existente mais próximo (dentro de SNAP_PX pixels)
  function snapTarget(latlng) {
    const fallback = [latlng.lng, latlng.lat];
    if (!ui.snap) return fallback;
    const click = map.latLngToContainerPoint(latlng);
    let best = null;
    let bestDist = SNAP_PX;
    allVertices().forEach(pt => {
      const d = click.distanceTo(map.latLngToContainerPoint([pt[1], pt[0]]));
      if (d < bestDist) {
        bestDist = d;
        best = pt;
      }
    });
    return best || fallback;
  }

  // ---------- Rascunho e comandos ----------
  function addDraftPoint(pt) {
    if (!ui.draft) ui.draft = { layer: ui.layer, points: [] };
    ui.draft.points.push(pt);
  }

  function lastPoint() {
    if (ui.draft && ui.draft.points.length) return ui.draft.points.at(-1);
    const s = svc();
    const items = s ? s.layers[ui.layer] : [];
    const last = items.at(-1);
    if (!last) return null;
    return LAYERS[ui.layer].closed ? last.at(-2) : last.at(-1);
  }

  function addPolar(dist, az) {
    const last = lastPoint();
    if (!last) return msg('Lance o primeiro ponto no mapa antes de usar distância e azimute.', true);
    const zone = zoneOf(last[0]);
    const [E, N] = Geo.toUtmPoint(last[0], last[1], zone);
    const [x, y] = Cad.polarPoint([E, N], dist, az);
    addDraftPoint(Geo.fromUtmPoint(x, y, zone));
    msg(`Vértice a ${fmt(dist, 2)} m, azimute ${Geo.dms(az)}.`);
  }

  function distanciaUltimoTrecho() {
    const pts = ui.draft && ui.draft.points.length >= 2 ? ui.draft.points : null;
    if (!pts) return msg('DIST precisa de pelo menos dois pontos no rascunho.', true);
    const [a, b] = pts.slice(-2);
    const zone = zoneOf(a[0]);
    const seg = Geo.ringSegments([Geo.toUtmPoint(a[0], a[1], zone), Geo.toUtmPoint(b[0], b[1], zone)])[0];
    msg(`Distância ${fmt(seg.distanceM, 2)} m · azimute ${Geo.dms(seg.azimuth)} · ${seg.bearing}`);
  }

  function commitDraft() {
    const s = svc();
    const d = ui.draft;
    if (!s || !d || !d.points.length) return msg('Não há desenho em andamento.', true);
    const info = LAYERS[d.layer];
    if (info.closed) {
      if (d.points.length < 3) return msg('Polígono precisa de pelo menos 3 pontos.', true);
      const ring = closeRing(d.points);
      if (d.layer === 'perimetro') {
        s.layers.perimetro = [ring];
        s.meta = { codes: [], vertex: [], segs: [] }; // novo perímetro: códigos e confrontantes recomeçam
      } else {
        s.layers[d.layer].push(ring);
      }
    } else {
      if (d.points.length < 2) return msg('Linha precisa de pelo menos 2 pontos.', true);
      s.layers[d.layer].push([...d.points]);
    }
    ui.draft = null;
    msg(`${info.label}: desenho fechado.`);
    commitChanges();
  }

  function undo() {
    if (ui.draft && ui.draft.points.length) {
      ui.draft.points.pop();
      if (!ui.draft.points.length) ui.draft = null;
      msg('Último ponto removido.');
    } else {
      msg('Nada para desfazer.', true);
    }
    refresh();
  }

  function clearActive() {
    const s = svc();
    if (!s) return;
    if (ui.draft && ui.draft.layer === ui.layer) ui.draft = null;
    s.layers[ui.layer] = [];
    if (ui.layer === 'perimetro') s.meta = { codes: [], vertex: [], segs: [] };
    msg(`Camada "${LAYERS[ui.layer].label}" limpa.`);
    commitChanges();
  }

  function executeCad() {
    const input = el('cadInput');
    const cmd = Cad.parse(input.value);
    input.value = '';
    if (!cmd) return;
    if (cmd.type === 'error') return msg(cmd.message, true);
    if (cmd.type === 'offset') {
      offsetActive(cmd.dist);
      return refresh();
    }
    if (cmd.type === 'polar') {
      addPolar(cmd.dist, cmd.az);
      return refresh();
    }

    switch (cmd.name) {
      case 'LIMPAR':
        return clearActive();
      case 'PL':
        ui.draft = ui.draft || { layer: ui.layer, points: [] };
        msg('Modo polilinha: clique no mapa ou digite distância e azimute.');
        break;
      case 'FECHAR':
        return commitDraft();
      case 'DESFAZER':
        return undo();
      case 'DIST':
        distanciaUltimoTrecho();
        break;
      case 'SNAP':
        ui.snap = !ui.snap;
        msg(`SNAP ${ui.snap ? 'ligado' : 'desligado'}.`);
        break;
      default:
        msg(`${cmd.name} ainda não está implementado.`, true);
    }
    refresh();
  }

  // OFFSET: paralela da última feição da camada ativa, em UTM, mantendo a original
  function offsetActive(dist) {
    const s = svc();
    if (!s) return msg('Crie ou selecione um serviço antes do OFFSET.', true);
    if (ui.layer === 'perimetro') {
      return msg('OFFSET não cria paralela do perímetro. Escolha RL, APP, curso d\'água ou edificação.', true);
    }
    const info = LAYERS[ui.layer];
    const item = s.layers[ui.layer].at(-1);
    if (!item) return msg(`A camada "${info.label}" está vazia.`, true);
    if (dist === 0) return msg('A distância do OFFSET não pode ser zero.', true);
    if (remote.mode === 'api') return offsetRemote(s, dist);

    const lngMedio = item.reduce((t, p) => t + p[0], 0) / item.length;
    const zone = zoneOf(lngMedio);
    const utm = item.map(([lng, lat]) => Geo.toUtmPoint(lng, lat, zone));
    const offsetPts = Geo.offsetPlanar(utm, dist, info.closed);
    if (offsetPts.some(([x, y]) => !Number.isFinite(x) || !Number.isFinite(y))) {
      return msg('OFFSET falhou: há arestas de comprimento zero no desenho.', true);
    }

    const geo = offsetPts.map(([E, N]) => Geo.fromUtmPoint(E, N, zone));
    if (info.closed) {
      const ring = closeRing(geo);
      const issues = Geo.validate(closedPolygon(ring));
      if (issues.length) {
        return msg(`OFFSET de ${fmt(dist, 2)} m gerou ${issues.join('; ')}. Use uma distância menor.`, true);
      }
      s.layers[ui.layer].push(ring);
    } else {
      s.layers[ui.layer].push(geo);
    }
    msg(`Paralela de ${fmt(dist, 2)} m criada em "${info.label}".`);
    commitChanges();
  }

  // ---------- Renderização ----------
  function renderMap() {
    mapGroup.clearLayers();
    const s = svc();
    if (!s) return;

    Object.entries(s.layers).forEach(([cat, items]) => {
      const info = LAYERS[cat];
      items.forEach(item => {
        const latlngs = item.map(([lng, lat]) => [lat, lng]);
        if (info.closed) {
          L.polygon(latlngs, { color: info.color, weight: 2, fillOpacity: 0.2 }).addTo(mapGroup);
        } else {
          L.polyline(latlngs, { color: info.color, weight: 3 }).addTo(mapGroup);
        }
      });
    });

    if (ui.draft && ui.draft.points.length) {
      const color = LAYERS[ui.draft.layer].color;
      const latlngs = ui.draft.points.map(([lng, lat]) => [lat, lng]);
      if (latlngs.length > 1) L.polyline(latlngs, { color, weight: 2, dashArray: '6 4' }).addTo(mapGroup);
      latlngs.forEach(ll =>
        L.circleMarker(ll, { radius: 4, color, fillColor: '#ffffff', fillOpacity: 1, weight: 2 }).addTo(mapGroup)
      );
    }

    const pm = perimeterCtx();
    if (pm) {
      pm.open.forEach(([lng, lat], i) =>
        L.circleMarker([lat, lng], { radius: 5, color: '#0f172a', weight: 2, fillColor: '#facc15', fillOpacity: 1 })
          .bindTooltip(codeOf(s, i), { permanent: true, direction: 'top', offset: [0, -6], className: 'vertex-label' })
          .addTo(mapGroup)
      );
    }
  }

  function renderSelectors() {
    const clientSel = el('clientSelect');
    clientSel.textContent = '';
    state.clients.forEach(c =>
      clientSel.append(opt(c.id, `${c.name} (${c.doc || 'sem documento'})`, String(c.id) === String(state.activeClientId)))
    );

    const srvSel = el('serviceSelect');
    srvSel.textContent = '';
    const list = state.services.filter(x => String(x.clientId) === String(state.activeClientId));
    if (!list.length) srvSel.append(opt('', '(nenhum serviço: clique em +Rev para criar)', true, true));
    list.forEach(x =>
      srvSel.append(opt(x.id, `${x.name} [Rev ${pad2(x.rev)}]`, String(x.id) === String(state.activeServiceId)))
    );
  }

  function opt(value, text, selected = false, disabled = false) {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = text;
    o.selected = selected;
    o.disabled = disabled;
    return o;
  }

  // Campos de endereço do cliente, com texto vazio onde não há valor
  function enderecoDe(c) {
    return {
      cep: c.cep || '',
      logradouro: c.logradouro || '',
      numero: c.numero || '',
      complemento: c.complemento || '',
      bairro: c.bairro || '',
      municipio: c.municipio || '',
      uf: c.uf || '',
    };
  }

  // Endereço do cliente selecionado. Só edita com o servidor; no modo local fica desativado
  function renderEndereco() {
    const c = state.clients.find(x => String(x.id) === String(state.activeClientId));
    const ativo = remote.mode === 'api' && !!c;
    document.querySelectorAll('[data-end]').forEach(input => {
      input.value = ativo ? c.endereco[input.dataset.end] : '';
      input.disabled = !ativo;
    });
    el('btnSalvarEndereco').disabled = !ativo;
    el('enderecoStatus').textContent = remote.mode === 'api' ? '' : 'Disponível com o servidor.';
  }

  function docsToInputs() {
    const s = svc();
    document.querySelectorAll('[data-doc]').forEach(input => {
      input.value = s ? s.docs[input.dataset.doc] || '' : '';
      input.disabled = !s;
    });
    el('mfInput').value = s && s.mf ? s.mf : '';
  }

  function renderDesenho() {
    const s = svc();
    const pm = perimeterCtx();
    el('activeLayerSelect').value = ui.layer;

    const totalHa = pm ? pm.summary.areaUtmHa : 0;
    const rlExigida = (totalHa * RL_PERCENT) / 100;
    const rlHa = s ? s.layers.rl.reduce((t, r) => t + ringAreaHa(r), 0) : 0;
    const appHa = s ? s.layers.app.reduce((t, r) => t + ringAreaHa(r), 0) : 0;
    const mf = s && s.mf;

    el('carTotalHa').textContent = `${fmt(totalHa, 4)} ha`;
    el('carRlExig').textContent = `${fmt(rlExigida, 4)} ha (${RL_PERCENT}% de referência)`;
    el('carRlDesenhada').textContent = `${fmt(rlHa, 4)} ha`;
    el('carApp').textContent = `${fmt(appHa, 4)} ha`;
    el('carMf').textContent = mf && totalHa ? `${fmt(totalHa / mf, 2)} MF` : 'informe o módulo fiscal';
    el('carStatus').textContent = !pm
      ? 'Sem perímetro.'
      : rlHa >= rlExigida
        ? 'Reserva Legal desenhada atende ao percentual de referência.'
        : `Faltam ${fmt(rlExigida - rlHa, 4)} ha de Reserva Legal.`;

    const list = el('layerList');
    list.textContent = '';
    Object.entries(LAYERS).forEach(([cat, info]) => {
      const items = s ? s.layers[cat] : [];
      const li = document.createElement('li');
      li.className = 'flex justify-between text-xs py-1 border-b border-slate-800';
      const name = document.createElement('span');
      name.textContent = info.label;
      const count = document.createElement('span');
      count.className = 'font-mono text-slate-400';
      count.textContent = info.closed ? `${items.length} polígono(s)` : `${items.length} linha(s)`;
      li.append(name, count);
      list.append(li);
    });
  }

  function field(label, value, onChange, wide = false) {
    const wrap = document.createElement('label');
    wrap.className = `text-[10px] text-slate-400 ${wide ? 'col-span-2' : ''}`;
    wrap.textContent = label;
    const input = document.createElement('input');
    input.className = 'mt-0.5 w-full bg-slate-900 border border-slate-700 rounded px-1.5 py-1 text-[11px] text-slate-100';
    input.value = value || '';
    input.addEventListener('change', () => onChange(input.value.trim()));
    wrap.append(input);
    return wrap;
  }

  function selectField(label, value, options, onChange) {
    const wrap = document.createElement('label');
    wrap.className = 'text-[10px] text-slate-400 col-span-2';
    wrap.textContent = label;
    const select = document.createElement('select');
    select.className = 'mt-0.5 w-full bg-slate-900 border border-slate-700 rounded px-1 py-1 text-[11px] text-slate-100';
    Object.entries(options).forEach(([v, text]) => select.append(opt(v, text, v === value)));
    select.addEventListener('change', () => onChange(select.value));
    wrap.append(select);
    return wrap;
  }

  function patchMeta(s, key, i, patch) {
    s.meta[key][i] = { ...(s.meta[key][i] || {}), ...patch };
    commitChanges();
  }

  function vertexCard(s, pm, i, lng, lat) {
    const [E, N] = Geo.toUtmPoint(lng, lat, pm.summary.zone);
    const seg = pm.segments[i];
    const meta = s.meta.vertex[i] || {};
    const segMeta = s.meta.segs[i] || {};
    const next = (i + 1) % pm.open.length;

    const card = document.createElement('div');
    card.className = 'bg-slate-800/70 border border-slate-700 rounded p-2 space-y-2';

    const head = document.createElement('div');
    head.className = 'flex items-center justify-between gap-2';
    const code = document.createElement('input');
    code.className = 'bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 font-mono font-bold text-amber-300 w-36 text-[11px]';
    code.value = codeOf(s, i);
    code.addEventListener('change', () => {
      s.meta.codes[i] = code.value.trim() || null;
      commitChanges();
    });
    const coords = document.createElement('span');
    coords.className = 'font-mono text-[10px] text-slate-400';
    coords.textContent = `E ${fmt(E, 2)} · N ${fmt(N, 2)}`;
    head.append(code, coords);

    const grid = document.createElement('div');
    grid.className = 'grid grid-cols-3 gap-1';
    grid.append(
      field('Método', meta.metodo, v => patchMeta(s, 'vertex', i, { metodo: v })),
      field('Altitude (m)', meta.altitude, v => patchMeta(s, 'vertex', i, { altitude: v })),
      field('Sigma X', meta.sigmaX, v => patchMeta(s, 'vertex', i, { sigmaX: v })),
      field('Sigma Y', meta.sigmaY, v => patchMeta(s, 'vertex', i, { sigmaY: v })),
      field('Sigma Z', meta.sigmaZ, v => patchMeta(s, 'vertex', i, { sigmaZ: v }))
    );

    const segLine = document.createElement('div');
    segLine.className = 'text-[11px] text-sky-300 font-mono';
    segLine.textContent = `→ ${codeOf(s, next)}: ${seg.bearing} · ${Geo.dms(seg.azimuth)} · ${fmt(seg.distanceM, 2)} m`;

    const conf = document.createElement('div');
    conf.className = 'grid grid-cols-2 gap-1 border-t border-slate-700 pt-2';
    const title = document.createElement('div');
    title.className = 'col-span-2 text-[10px] font-bold uppercase text-slate-400';
    title.textContent = 'Confrontante deste trecho';
    conf.append(
      title,
      field('Nome', segMeta.nome, v => patchMeta(s, 'segs', i, { nome: v }), true),
      field('CPF/CNPJ', segMeta.doc, v => patchMeta(s, 'segs', i, { doc: v })),
      field('Matrícula', segMeta.matricula, v => patchMeta(s, 'segs', i, { matricula: v })),
      field('Código INCRA/SNCR', segMeta.incra, v => patchMeta(s, 'segs', i, { incra: v }), true),
      selectField('Tipo de divisa', segMeta.tipo || 'PESSOA', TIPOS_DIVISA, v => patchMeta(s, 'segs', i, { tipo: v }))
    );

    card.append(head, grid, segLine, conf);
    return card;
  }

  function renderValidator(s, pm) {
    const list = el('validatorList');
    list.textContent = '';
    const checks = [];
    const issues = Geo.validate(pm.feature);
    checks.push(
      pm.open.length >= 3
        ? ['ok', `${pm.open.length} vértices`]
        : ['erro', `Apenas ${pm.open.length} vértice(s); mínimo de 3`]
    );
    checks.push(issues.length ? ['erro', issues.join('; ')] : ['ok', 'Sem auto-interseção e anel fechado']);
    const semConf = pm.segments.filter((_, i) => !(s.meta.segs[i] && s.meta.segs[i].nome)).length;
    checks.push(
      semConf ? ['aviso', `${semConf} trecho(s) sem confrontante informado`] : ['ok', 'Todos os trechos têm confrontante']
    );
    if (pm.open.length >= 3) {
      // Ângulos internos no plano UTM: espigões (quase 0°) e reentrâncias muito fechadas (quase 360°)
      const angulos = Geo.interiorAngles(pm.summary.utmPolygons[0][0]);
      const extremos = angulos.map((a, i) => ({ a, i })).filter(({ a }) => a < 5 || a > 355);
      checks.push(
        extremos.length
          ? ['aviso', `Ângulos extremos (< 5° ou > 355°): ${extremos.map(({ a, i }) => `${codeOf(s, i)} ${a.toFixed(1)}°`).join(', ')}`]
          : ['ok', `Ângulos internos entre ${Math.min(...angulos).toFixed(1)}° e ${Math.max(...angulos).toFixed(1)}°`]
      );
    }

    const marks = { ok: '✓ ', aviso: '⚠ ', erro: '✗ ' };
    const colors = { ok: 'text-emerald-400', aviso: 'text-amber-300', erro: 'text-rose-400' };
    checks.forEach(([level, text]) => {
      const li = document.createElement('li');
      li.className = colors[level];
      li.textContent = marks[level] + text;
      list.append(li);
    });
    const note = document.createElement('li');
    note.className = 'text-[11px] text-slate-500';
    note.textContent = 'Sobreposição com parcelas do SIGEF depende da camada oficial, ainda não integrada.';
    list.append(note);
  }

  function renderLimites() {
    const s = svc();
    const pm = perimeterCtx();
    const list = el('verticesList');
    list.textContent = '';

    if (!pm) {
      ['statAreaHa', 'statAreaM2', 'statPerim', 'statVertices'].forEach(id => (el(id).textContent = '0'));
      list.textContent = 'Nenhum perímetro. Use "Carregar exemplo", importe um arquivo ou desenhe na camada Perímetro.';
      el('validatorList').textContent = '';
      return;
    }

    el('statAreaHa').textContent = fmt(pm.summary.areaUtmHa, 4);
    el('statAreaM2').textContent = fmt(pm.summary.areaUtmHa * 10000, 2);
    el('statPerim').textContent = fmt(perimeterM(pm), 2);
    el('statVertices').textContent = String(pm.open.length);

    pm.open.forEach(([lng, lat], i) => list.append(vertexCard(s, pm, i, lng, lat)));
    renderValidator(s, pm);
  }

  function updateZoneLabel() {
    const pm = perimeterCtx();
    const zone = pm ? pm.summary.zone : zoneOf(map.getCenter().lng);
    el('zoneLabel').textContent = `UTM ${zone}S (EPSG:${Geo.utmEpsg(zone)})`;
  }

  function refresh() {
    renderSelectors();
    renderMap();
    renderDesenho();
    renderLimites();
    docsToInputs();
    renderEndereco();
    updateZoneLabel();
  }

  function commitChanges() {
    save();
    refresh();
    scheduleSync();
  }

  function fitPerimeter() {
    const pm = perimeterCtx();
    if (pm) map.fitBounds(L.latLngBounds(pm.open.map(([lng, lat]) => [lat, lng])), { padding: [40, 40] });
  }

  // ---------- Pilar 1: DADOS (importação) ----------
  function collectFeatures(data) {
    if (Array.isArray(data)) return data.flatMap(collectFeatures);
    if (data.type === 'FeatureCollection') return data.features;
    if (data.type === 'Feature') return [data];
    return [];
  }

  function importGeo(data, fileName) {
    const s = svc();
    if (!s) throw new Error('crie um serviço antes de importar');
    const cat = ui.layer;
    const info = LAYERS[cat];
    const wanted = info.closed ? ['Polygon', 'MultiPolygon'] : ['LineString', 'MultiLineString'];
    const usable = collectFeatures(data).filter(f => f.geometry && wanted.includes(f.geometry.type));
    if (!usable.length) throw new Error(`nenhuma geometria compatível com "${info.label}" (${wanted.join(' ou ')})`);

    const rings = [];
    usable.forEach(({ geometry: g }) => {
      if (g.type === 'Polygon') rings.push(g.coordinates[0]);
      else if (g.type === 'MultiPolygon') g.coordinates.forEach(p => rings.push(p[0]));
      else if (g.type === 'LineString') rings.push(g.coordinates);
      else g.coordinates.forEach(line => rings.push(line));
    });
    if (rings.some(r => r.some(([x, y]) => Math.abs(x) > 180 || Math.abs(y) > 90))) {
      throw new Error('coordenadas fora de graus (parecem UTM). Reprojete para SIRGAS 2000 geográfico (EPSG:4674).');
    }

    if (cat === 'perimetro') {
      s.layers.perimetro = [closeRing(rings[0])];
      s.meta = { codes: [], vertex: [], segs: [] };
      if (rings.length > 1) msg(`Só o primeiro polígono de "${fileName}" foi usado no perímetro.`);
    } else {
      s.layers[cat].push(...rings.map(r => (info.closed ? closeRing(r) : r)));
      msg(`${rings.length} feição(ões) importada(s) de "${fileName}" para ${info.label}.`);
    }
    commitChanges();
    if (cat === 'perimetro') fitPerimeter();
  }

  function importCsv(text) {
    if (!svc()) throw new Error('crie um serviço antes de importar');
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (lines.length < 2) throw new Error('CSV sem pontos');

    const sep = lines[0].includes(';') ? ';' : ',';
    const header = lines[0].split(sep).map(h => h.trim().toLowerCase());
    const col = names => header.findIndex(h => names.includes(h));
    const iLat = col(['lat', 'latitude']);
    const iLon = col(['lon', 'lng', 'long', 'longitude']);
    const iE = col(['e', 'este', 'x', 'easting']);
    const iN = col(['n', 'norte', 'y', 'northing']);
    const useLatLon = iLat >= 0 && iLon >= 0;
    if (!useLatLon && !(iE >= 0 && iN >= 0)) throw new Error('CSV precisa de colunas lat/lon ou E/N');

    const num = v => parseFloat(String(v).trim().replace(',', '.'));
    const zone = zoneOf(map.getCenter().lng);
    const points = lines
      .slice(1)
      .map(line => {
        const c = line.split(sep);
        return useLatLon ? [num(c[iLon]), num(c[iLat])] : Geo.fromUtmPoint(num(c[iE]), num(c[iN]), zone);
      })
      .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
    if (!points.length) throw new Error('nenhum ponto válido');

    ui.draft = { layer: ui.layer, points: [...(ui.draft && ui.draft.layer === ui.layer ? ui.draft.points : []), ...points] };
    msg(
      `${points.length} ponto(s) no rascunho. ${useLatLon ? '' : `Coordenadas E/N lidas na zona ${zone}S (centro do mapa). `}Clique em FECHAR para confirmar.`
    );
    refresh();
  }

  // ---------- Curvas de nível (não grava no servidor) ----------
  let curvasLayer = null;
  let curvasAtuais = null;

  function removerCurvas() {
    if (curvasLayer) map.removeLayer(curvasLayer);
    curvasLayer = null;
    curvasAtuais = null;
    el('cotaResumo').textContent = '';
  }

  function mostrarCurvas(geojson, { intervalo, minZ, maxZ, sufixo = '', ajustar = true }) {
    removerCurvas();
    curvasAtuais = geojson;
    curvasLayer = L.geoJSON(geojson, {
      style: { color: '#f59e0b', weight: 1 },
      onEachFeature: (f, layer) => layer.bindTooltip(`${f.properties.cota} m`, { sticky: true }),
    }).addTo(map);
    el('cotaResumo').textContent =
      `${geojson.features.length} curva(s) de ${intervalo} m, cotas de ${minZ.toFixed(2)} a ${maxZ.toFixed(2)} m${sufixo}.`;
    if (ajustar && geojson.features.length) map.fitBounds(curvasLayer.getBounds());
  }

  // Mostra as curvas salvas no serviço carregado (categoria 'curvas' do detalhe)
  function mostrarCurvasSalvas(camadas) {
    const feicoes = camadas.filter(c => c.categoria === 'curvas');
    if (!feicoes.length) return removerCurvas();
    const cotas = feicoes.map(c => c.propriedades.cota);
    mostrarCurvas(
      {
        type: 'FeatureCollection',
        features: feicoes.map(c => ({ type: 'Feature', properties: { cota: c.propriedades.cota }, geometry: c.geometry })),
      },
      {
        intervalo: feicoes[0].propriedades.intervalo,
        minZ: Math.min(...cotas),
        maxZ: Math.max(...cotas),
        sufixo: ' (salvas no serviço)',
        ajustar: false,
      }
    );
  }

  // Grava as curvas no serviço ativo (modo remoto). Retorna false quando não há serviço no servidor
  async function salvarCurvas(res) {
    const s = svc();
    if (remote.mode !== 'api' || !s || !s.remote) return false;
    await api(`/servicos/${s.id}/curvas`, {
      method: 'PUT',
      body: {
        intervalo: res.intervalo,
        feicoes: res.geojson.features.map(f => ({ cota: f.properties.cota, partes: f.geometry.coordinates })),
      },
    });
    return true;
  }

  async function gerarCurvas(file) {
    const intervalo = parseFloat(String(el('cotaIntervalo').value).replace(',', '.'));
    try {
      const pontos = Contours.parseCsv(await file.text(), zoneOf(map.getCenter().lng));
      const res = Contours.build(pontos, intervalo);
      mostrarCurvas(res.geojson, {
        intervalo: res.intervalo,
        minZ: res.minZ,
        maxZ: res.maxZ,
        sufixo: ` a partir de ${res.pontos} ponto(s)`,
      });
      const salvo = await salvarCurvas(res);
      msg(salvo ? 'Curvas de nível geradas e salvas no serviço.' : 'Curvas de nível geradas.');
    } catch (err) {
      msg(`Erro nas curvas: ${err.message}`, true);
    }
  }

  async function importFile(file) {
    const name = file.name.toLowerCase();
    try {
      if (name.endsWith('.csv')) {
        importCsv(await file.text());
        return;
      }
      let data;
      if (name.endsWith('.zip')) data = await shp(await file.arrayBuffer());
      else if (name.endsWith('.kml')) data = toGeoJSON.kml(new DOMParser().parseFromString(await file.text(), 'text/xml'));
      else data = JSON.parse(await file.text());
      importGeo(data, file.name);
    } catch (err) {
      msg(`Erro ao importar: ${err.message}`, true);
    } finally {
      el('fileImport').value = '';
    }
  }

  // ---------- Pilar 2: DESENHOS (APP e quadro CAR) ----------
  function gerarApp() {
    const s = svc();
    if (!s) return;
    if (!s.layers.hidro.length) {
      return msg('Desenhe o curso d\'água na camada "Curso d\'água" antes de gerar a APP.', true);
    }
    const metros = el('bufferDist').value;
    s.layers.hidro.forEach(linha => {
      const buf = turf.buffer(turf.lineString(linha), parseFloat(metros) / 1000, { units: 'kilometers' });
      const poligonos = buf.geometry.type === 'Polygon' ? [buf.geometry.coordinates] : buf.geometry.coordinates;
      poligonos.forEach(p => s.layers.app.push(p[0]));
    });
    msg(`APP gerada com faixa de ${metros} m. Faixa fixa simplificada: a largura legal depende do curso e da nascente.`);
    commitChanges();
  }

  // ---------- Pilar 4: ENTREGAS ----------
  function download(name, content, mime) {
    const blob = new Blob([content], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  function entregaContext() {
    const s = svc();
    const pm = perimeterCtx();
    if (!s || !pm) {
      msg('Desenhe ou carregue um perímetro primeiro.', true);
      return null;
    }
    return { s, pm };
  }

  let memFormat = 'corrido';

  function gerarMemorial(format) {
    const ctx = entregaContext();
    if (!ctx) return;
    const { s, pm } = ctx;
    memFormat = format;
    el('output').value = Memorial.build(s.name, pm.summary, {
      format,
      codes: pm.open.map((_, i) => codeOf(s, i)),
      confrontantes: pm.segments.map((_, i) => (s.meta.segs[i] && s.meta.segs[i].nome) || '[a informar]'),
    });
    msg(`Memorial ${format === 'corrido' ? 'em texto corrido' : 'tabular'} gerado.`);
  }

  // Gera o .docx no servidor a partir do texto do memorial exibido na área de saída
  async function baixarMemorialDocx() {
    const texto = el('output').value.trim();
    if (!texto) return msg('Gere o memorial antes de baixar o .docx.', true);
    if (remote.mode !== 'api') return msg('O download do .docx precisa do servidor.', true);
    const [titulo, ...resto] = texto.split('\n');
    try {
      const res = await fetch('/memorial.docx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          titulo: titulo.trim().slice(0, 200) || 'Memorial descritivo',
          formato: memFormat,
          linhas: resto.length ? resto : [''],
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `memorial-${memFormat}.docx`;
      link.click();
      URL.revokeObjectURL(url);
      msg('Memorial .docx baixado.');
    } catch (err) {
      msg(`Erro ao gerar o .docx: ${err.message}`, true);
    }
  }

  // Base vetorial do serviço salvo: uma camada por categoria, em SIRGAS 2000 (GeoPackage)
  async function baixarGpkg() {
    const s = svc();
    if (remote.mode !== 'api' || !s || !s.remote) return msg('A base .gpkg precisa do servidor e de um serviço salvo.', true);
    try {
      await flushSync(); // grava antes as alterações pendentes para a base sair atualizada
      const res = await fetch(`/servicos/${s.id}/exportar.gpkg`);
      if (!res.ok) {
        const corpo = await res.json().catch(() => ({}));
        throw new Error(typeof corpo.detail === 'string' ? corpo.detail : `HTTP ${res.status}`);
      }
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `servico-${s.id}.gpkg`;
      link.click();
      URL.revokeObjectURL(url);
      msg('Base .gpkg baixada.');
    } catch (err) {
      msg(`Erro ao gerar o .gpkg: ${err.message}`, true);
    }
  }

  function gerarSigef() {
    const ctx = entregaContext();
    if (!ctx) return;
    const { s, pm } = ctx;
    const vertices = pm.open.map(([lng, lat], i) => {
      const [E, N] = Geo.toUtmPoint(lng, lat, pm.summary.zone);
      const seg = pm.segments[i];
      const meta = s.meta.vertex[i] || {};
      return {
        codigo: codeOf(s, i),
        E,
        N,
        altitude: meta.altitude || '',
        sigma: [meta.sigmaX, meta.sigmaY, meta.sigmaZ].filter(Boolean).join(' / '),
        metodo: meta.metodo || '',
        azimute: seg ? Geo.dms(seg.azimuth) : '',
        distancia: seg ? seg.distanceM : '',
      };
    });
    el('output').value = Exporter.sigefCsv({
      identificacao: {
        'Nome do imóvel': s.name,
        Matrícula: s.docs.matricula,
        'Código INCRA/SNCR': s.docs.incra,
        'Recibo CAR': s.docs.car,
        Sistema: `SIRGAS 2000 / UTM zona ${pm.summary.zone}S (EPSG:${pm.summary.epsg})`,
        'Área (ha)': fmt(pm.summary.areaUtmHa, 4),
        'Perímetro (m)': fmt(perimeterM(pm), 2),
      },
      vertices,
    });
    msg('Planilha SIGEF gerada em CSV (não é ODS).');
  }

  function gerarDxf() {
    const ctx = entregaContext();
    if (!ctx) return;
    const { s, pm } = ctx;
    const zone = pm.summary.zone;
    // Todas as feições saem na zona do perímetro
    const features = [];
    Object.entries(s.layers).forEach(([cat, items]) =>
      items.forEach(item => {
        const pts = item.map(([lng, lat]) => Geo.toUtmPoint(lng, lat, zone));
        const closed = LAYERS[cat].closed;
        features.push({ layer: LAYERS[cat].dxf, points: closed ? pts.slice(0, -1) : pts, closed });
      })
    );
    download(`${slug(s.name)}_utm${zone}s.dxf`, Exporter.toDxf(features), 'application/dxf');
  }

  function gerarGeojson() {
    const s = svc();
    if (!s) return msg('Nenhum serviço ativo.', true);
    const features = [];
    Object.entries(s.layers).forEach(([cat, items]) =>
      items.forEach(item =>
        features.push({
          type: 'Feature',
          properties: { camada: cat, descricao: LAYERS[cat].label, servico: s.name },
          geometry: LAYERS[cat].closed
            ? { type: 'Polygon', coordinates: [item] }
            : { type: 'LineString', coordinates: item },
        })
      )
    );
    if (!features.length) return msg('Nada para exportar.', true);
    download(`${slug(s.name)}.geojson`, JSON.stringify({ type: 'FeatureCollection', features }), 'application/geo+json');
  }

  function gerarOrcamento() {
    const s = svc();
    const pm = perimeterCtx();
    const client = state.clients.find(c => c.id === (s && s.clientId));
    if (!s || !pm) return msg('Desenhe ou carregue um perímetro para gerar o croqui.', true);

    const w = window.open('', '_blank');
    if (!w) return msg('O navegador bloqueou a janela. Permita pop-ups para gerar o croqui.', true);
    const linhas = [
      ['Cliente', `${client ? client.name : '-'} (${client ? client.doc : '-'})`],
      ['Serviço', `${s.name} · Rev ${pad2(s.rev)}`],
      ['Área', `${fmt(pm.summary.areaUtmHa, 4)} ha (${fmt(pm.summary.areaUtmHa * 10000, 2)} m²)`],
      ['Perímetro', `${fmt(perimeterM(pm), 2)} m`],
      ['Vértices', String(pm.open.length)],
      ['Sistema', `SIRGAS 2000 / UTM zona ${pm.summary.zone}S`],
    ];
    const rows = linhas.map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`).join('');
    w.document.write(`<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
      <title>Croqui de orçamento</title>
      <style>body{font-family:sans-serif;margin:32px;color:#111}table{border-collapse:collapse;width:100%}
      th,td{border-bottom:1px solid #ddd;padding:8px;text-align:left;font-size:14px}th{width:30%}</style></head>
      <body><h1>Croqui de orçamento</h1><table>${rows}</table>
      <p>Valor: a definir conforme tabela de honorários. Documento preliminar, sem validade cartorial.</p></body></html>`);
    w.document.close();
    w.print();
  }

  // ---------- Topbar: clientes, serviços e revisões ----------
  function novaRevisao() {
    const s = svc();
    if (remote.mode === 'api') return novaRevisaoRemote(s);
    if (!s) {
      const name = prompt('Nome do serviço:');
      if (!name) return;
      const created = newService(state.activeClientId, name.trim(), 0);
      state.services.push(created);
      state.activeServiceId = created.id;
      ui.draft = null;
      return commitChanges();
    }
    const rev = Math.max(...state.services.filter(x => x.clientId === s.clientId && x.name === s.name).map(x => x.rev)) + 1;
    const copia = { ...JSON.parse(JSON.stringify(s)), id: uid(), rev };
    state.services.push(copia);
    state.activeServiceId = copia.id;
    ui.draft = null;
    msg(`Rev ${pad2(rev)} criada a partir de Rev ${pad2(s.rev)}.`);
    commitChanges();
  }

  // Busca: coordenadas decimais no mapa; depois os serviços da base (CAR, matrícula, nome, cliente);
  // o que sobrar vai ao Nominatim (OSM).
  async function buscarNaBase(text) {
    const achados = await api(`/buscar?q=${encodeURIComponent(text)}`);
    if (!achados.length) return false;
    const primeiro = achados[0];
    state.activeClientId = primeiro.cliente_id;
    await selectService(primeiro.id);
    msg(achados.length > 1
      ? `${achados.length} resultados; mostrando "${primeiro.nome_imovel}".`
      : `Encontrado: ${primeiro.nome_imovel}.`);
    return true;
  }

  async function goToLocation() {
    const text = el('searchInput').value.trim();
    if (!text) return;
    const ll = Cad.parseLatLng(text);
    if (ll) return map.setView([ll.lat, ll.lng], 16);
    if (remote.mode === 'api' && text.length >= 2) {
      try {
        if (await buscarNaBase(text)) return;
      } catch (err) {
        msg(`Falha na busca na base: ${err.message}`, true);
        return;
      }
    }
    try {
      const url = `https://nominatim.openstreetmap.org/search?format=json&countrycodes=br&limit=1&q=${encodeURIComponent(text)}`;
      const res = await fetch(url, { headers: { 'Accept-Language': 'pt-BR' } });
      const data = await res.json();
      if (!data.length) return msg('Local não encontrado. Use coordenadas decimais: -23.02, -45.55.', true);
      map.setView([parseFloat(data[0].lat), parseFloat(data[0].lon)], 14);
    } catch (err) {
      msg('Falha na busca. Verifique a conexão.', true);
    }
  }

  function loadDemo() {
    const s = svc();
    if (!s) return;
    s.layers.perimetro = [
      [
        [-45.53, -23.04],
        [-45.52, -23.04],
        [-45.518, -23.032],
        [-45.529, -23.03],
        [-45.53, -23.04],
      ],
    ];
    s.layers.hidro = [
      [
        [-45.525, -23.042],
        [-45.523, -23.034],
        [-45.5215, -23.029],
      ],
    ];
    s.meta = { codes: [], vertex: [], segs: [] };
    ui.draft = null;
    msg('Exemplo fictício carregado (Taubaté-SP).');
    commitChanges();
    fitPerimeter();
  }

  function switchPillar(name) {
    document.querySelectorAll('.pillar').forEach(p => p.classList.toggle('hidden', p.id !== `pillar-${name}`));
    document.querySelectorAll('[data-pillar]').forEach(btn => {
      const on = btn.dataset.pillar === name;
      btn.classList.toggle('text-sky-400', on);
      btn.classList.toggle('border-sky-500', on);
      btn.classList.toggle('bg-slate-900', on);
      btn.classList.toggle('text-slate-400', !on);
      btn.classList.toggle('border-transparent', !on);
    });
  }

  // ---------- Eventos ----------
  map.on('click', e => {
    if (!svc()) return msg('Crie ou selecione um serviço antes de desenhar.', true);
    addDraftPoint(snapTarget(e.latlng));
    refresh();
  });

  map.on('mousemove', e => {
    const zone = zoneOf(e.latlng.lng);
    const [E, N] = Geo.toUtmPoint(e.latlng.lng, e.latlng.lat, zone);
    el('cursorCoords').textContent =
      `E ${fmt(E, 2)} m · N ${fmt(N, 2)} m · UTM ${zone}S · Lat ${e.latlng.lat.toFixed(6)} · Lng ${e.latlng.lng.toFixed(6)}`;
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && ui.draft) {
      ui.draft = null;
      msg('Desenho cancelado.');
      refresh();
    }
  });

  el('clientSelect').addEventListener('change', e => selectClient(e.target.value));

  el('serviceSelect').addEventListener('change', e => {
    if (e.target.value) selectService(e.target.value);
  });

  el('btnRev').addEventListener('click', novaRevisao);
  el('btnSearch').addEventListener('click', goToLocation);
  el('searchInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') goToLocation();
  });
  el('btnQuote').addEventListener('click', gerarOrcamento);

  document.querySelectorAll('[data-pillar]').forEach(btn =>
    btn.addEventListener('click', () => switchPillar(btn.dataset.pillar))
  );
  document.querySelectorAll('[data-mode]').forEach(btn =>
    btn.addEventListener('click', () => setBaseMode(btn.dataset.mode))
  );
  document.querySelectorAll('[data-wms]').forEach(box =>
    box.addEventListener('change', () => {
      const layer = wmsLayers[box.dataset.wms];
      if (box.checked) layer.addTo(map);
      else map.removeLayer(layer);
    })
  );

  // Pilar 1
  el('fileImport').addEventListener('change', e => {
    const file = e.target.files[0];
    if (file) importFile(file);
  });
  el('cotaInput').addEventListener('change', async e => {
    const file = e.target.files[0];
    try {
      if (file) await gerarCurvas(file);
    } finally {
      e.target.value = '';
    }
  });
  el('btnCurvasLimpar').addEventListener('click', async () => {
    const s = svc();
    removerCurvas();
    if (remote.mode !== 'api' || !s || !s.remote) return;
    try {
      await api(`/servicos/${s.id}/curvas`, { method: 'DELETE' });
      msg('Curvas removidas do serviço.');
    } catch (err) {
      msg(`Erro ao remover as curvas: ${err.message}`, true);
    }
  });
  el('btnCurvasBaixar').addEventListener('click', () => {
    if (!curvasAtuais) return msg('Gere as curvas antes de baixar.', true);
    const url = URL.createObjectURL(new Blob([JSON.stringify(curvasAtuais)], { type: 'application/geo+json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'curvas-de-nivel.geojson';
    link.click();
    URL.revokeObjectURL(url);
  });
  el('arquivoInput').addEventListener('change', async e => {
    const file = e.target.files[0];
    const s = svc();
    try {
      if (!file || !s || !s.remote || remote.mode !== 'api') return;
      await api(`/servicos/${s.id}/arquivos`, {
        method: 'POST',
        body: { nome_arquivo: file.name, categoria: 'recibo_car', conteudo_base64: await fileToBase64(file) },
      });
      await loadArquivos();
      msg('Demonstrativo anexado.');
    } catch (err) {
      msg(`Erro ao anexar: ${err.message}`, true);
    } finally {
      e.target.value = '';
    }
  });
  document.querySelectorAll('[data-doc]').forEach(input =>
    input.addEventListener('change', () => {
      const s = svc();
      if (!s) return;
      let valor = input.value.trim();
      if (input.dataset.doc === 'car' && valor) {
        valor = valor.toUpperCase();
        if (!PADRAO_CAR.test(valor)) {
          input.value = s.docs.car || '';
          return msg('Código CAR fora do formato UF-CódigoIBGE-HASH (ex.: SP-3538709-E4A558DDFC5649D0A3524EB344ECC52D).', true);
        }
      }
      s.docs[input.dataset.doc] = valor;
      input.value = valor;
      commitChanges();
    })
  );

  // Pilar 2
  el('activeLayerSelect').addEventListener('change', e => {
    ui.layer = e.target.value;
    refresh();
  });
  el('mfInput').addEventListener('change', e => {
    const s = svc();
    if (!s) return;
    s.mf = parseFloat(e.target.value.replace(',', '.')) || null;
    commitChanges();
  });
  el('btnGerarApp').addEventListener('click', gerarApp);
  el('btnFechar').addEventListener('click', commitDraft);
  el('btnDesfazer').addEventListener('click', undo);
  el('btnLimparCamada').addEventListener('click', clearActive);

  // Pilar 3
  el('btnExemplo').addEventListener('click', loadDemo);
  el('btnLimparPerimetro').addEventListener('click', () => {
    const s = svc();
    if (!s || !confirm('Remover o perímetro e seus vértices?')) return;
    s.layers.perimetro = [];
    s.meta = { codes: [], vertex: [], segs: [] };
    msg('Perímetro removido.');
    commitChanges();
  });

  // Pilar 4
  el('btnMemNotarial').addEventListener('click', () => gerarMemorial('corrido'));
  el('btnMemTabular').addEventListener('click', () => gerarMemorial('tabular'));
  el('btnMemDocx').addEventListener('click', baixarMemorialDocx);
  el('btnGpkg').addEventListener('click', baixarGpkg);
  el('btnSalvarEndereco').addEventListener('click', async () => {
    const c = state.clients.find(x => String(x.id) === String(state.activeClientId));
    if (remote.mode !== 'api' || !c) return;
    const campos = {};
    document.querySelectorAll('[data-end]').forEach(input => {
      campos[input.dataset.end] = input.value.trim();
    });
    try {
      const salvo = await api(`/clientes/${c.id}`, {
        method: 'PUT',
        body: { nome: c.name, cpf_cnpj: c.doc, email: c.contato.email, telefone: c.contato.telefone, ...campos },
      });
      c.endereco = enderecoDe(salvo);
      renderEndereco();
      el('enderecoStatus').textContent = 'Endereço salvo.';
      msg('Endereço do cliente salvo.');
    } catch (err) {
      el('enderecoStatus').textContent = '';
      msg(`Erro no endereço: ${err.message}`, true);
    }
  });
  el('btnSigef').addEventListener('click', gerarSigef);
  el('btnDxf').addEventListener('click', gerarDxf);
  el('btnGeojson').addEventListener('click', gerarGeojson);
  el('btnCopy').addEventListener('click', () =>
    navigator.clipboard
      .writeText(el('output').value)
      .then(() => msg('Texto copiado.'))
      .catch(() => msg('Não foi possível copiar.', true))
  );

  el('cadForm').addEventListener('submit', e => {
    e.preventDefault();
    executeCad();
  });

  function bloquearSemServidor() {
    const aviso = document.createElement('div');
    aviso.setAttribute('role', 'alert');
    aviso.className = 'fixed inset-0 z-[2000] flex items-center justify-center bg-slate-950/90 p-4';
    aviso.innerHTML = `
      <div class="max-w-sm bg-slate-900 border border-rose-500/40 rounded-xl p-6 text-center space-y-3">
        <p class="font-semibold text-rose-300">Não foi possível carregar os dados do servidor.</p>
        <p class="text-xs text-slate-400">Nada foi alterado. Verifique a conexão e tente de novo.</p>
        <button type="button" class="bg-sky-600 hover:bg-sky-500 text-white text-sm font-semibold px-4 py-2 rounded">Tentar de novo</button>
      </div>`;
    aviso.querySelector('button').addEventListener('click', () => window.location.reload());
    document.body.appendChild(aviso);
  }

  // Alteração que falhou ao gravar: o navegador pede confirmação antes de fechar ou recarregar a página
  window.addEventListener('beforeunload', ev => {
    if (!remote.falhaPendente) return;
    ev.preventDefault();
    ev.returnValue = '';
  });

  el('btnSair').addEventListener('click', async () => {
    try {
      await fetch('/auth/logout', { method: 'POST' });
    } finally {
      window.location.replace('/login');
    }
  });

  // ---------- Inicialização ----------
  // Tenta o servidor; se não houver, a interface continua no navegador
  async function init() {
    try {
      await api('/health');
      remote.mode = 'api';
      await loadRemoteServicos();
      state.activeClientId = state.clients.length ? state.clients[0].id : null;
      const first = state.services.find(x => String(x.clientId) === String(state.activeClientId));
      state.activeServiceId = first ? first.id : null;
      if (first) await loadServico(first.id);
      msg('Conectado ao servidor (PostGIS).');
    } catch (err) {
      if (err.sessaoEncerrada) return;
      console.error('Falha ao carregar do servidor:', err);
      if (location.protocol !== 'file:') {
        // Página servida pela API: sem os dados do servidor, não abre o modo local (gravaria só no navegador)
        bloquearSemServidor();
        return;
      }
      remote.mode = 'local';
      state = load();
      msg('Modo local: servidor não encontrado. Os dados ficam neste navegador.');
    }
    await loadArquivos();
    setBaseMode('sat');
    switchPillar('limites');
    refresh();
    fitPerimeter();
  }

  init();
})();
