/* Polyclaw Dashboard – app.js
 * Two-phase workflow:  Scan → Select → Monitor
 */

let ws = null;
let chart = null;
let equitySeries = null;
let reconnectTimer = null;
let simState = {};
let scanCandidates = [];       // current scan results
let selectedTokenIds = new Set(); // user-selected markets
let watchedMarkets = {};       // token_id → candidate dict (for display)

/* ── Formatting helpers ────────────────────── */

function fmt(n, decimals = 2) {
    if (n == null) return '—';
    return Number(n).toFixed(decimals);
}

function fmtUSD(n) {
    if (n == null) return '—';
    return '$' + fmt(n);
}

function fmtTime(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtDate(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    return d.toLocaleDateString() + ' ' + fmtTime(ts);
}

function truncate(s, n) {
    if (!s) return '';
    return s.length > n ? s.slice(0, n) + '…' : s;
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

/* ── Init ──────────────────────────────────── */

function initApp(wsUrl, initialState) {
    simState = initialState || {};
    connectWS(wsUrl);

    // If a sim is already running, jump to monitor phase
    if (simState.status === 'running' || simState.status === 'paused') {
        showPhase('monitor');
        renderMonitorState(simState);
    }
}

/* ── Phase switching ───────────────────────── */

function showPhase(phase) {
    document.getElementById('phase-scan').style.display =
        phase === 'scan' ? 'block' : 'none';
    document.getElementById('phase-monitor').style.display =
        phase === 'monitor' ? 'block' : 'none';

    if (phase === 'monitor') {
        // Lazy-init chart when monitor first shown
        if (!chart) initChart();
    }
}

function backToScan() {
    if (simState.status === 'running') {
        if (!confirm('Simulation is running. Stop it and go back to scan?')) return;
        stopSim();
    }
    showPhase('scan');
}

/* ── WebSocket ─────────────────────────────── */

function connectWS(url) {
    if (ws && ws.readyState <= 1) return;
    ws = new WebSocket(url);

    ws.onopen = () => {
        console.log('[WS] connected');
        clearTimeout(reconnectTimer);
    };

    ws.onclose = () => {
        console.log('[WS] disconnected');
        reconnectTimer = setTimeout(() => connectWS(url), 3000);
    };

    ws.onerror = (e) => {
        console.error('[WS] error', e);
    };

    ws.onmessage = (msg) => {
        try {
            const evt = JSON.parse(msg.data);
            handleEvent(evt);
        } catch (e) {
            console.warn('[WS] bad message', e);
        }
    };
}

/* ── Event Router ──────────────────────────── */

function handleEvent(evt) {
    const type = evt.type;
    const data = evt.data || {};
    const ts = evt.timestamp;

    switch (type) {
        case 'sim_status':
            simState.status = data.status;
            renderStatusBadge(data.status);
            if (data.status === 'running') {
                setText('sim-run-id', data.run_id || '');
                setText('sim-strategy', data.strategy || '');
            }
            break;
        case 'tick':
            simState.tick = data.tick;
            setText('sim-tick', data.tick);
            break;
        case 'events_scanned':
            // Update watched list prices if we have new data
            break;
        case 'signal_emitted':
            addTradeEntry(ts, 'signal', data);
            break;
        case 'risk_verdict':
            if (!data.approved) {
                addTradeEntry(ts, 'rejected', data);
            }
            break;
        case 'trade_executed':
            addTradeEntry(ts, 'trade', data);
            updateTradeCount();
            break;
        case 'position_updated':
            renderPositions(data.positions || []);
            updatePosCount(data.positions);
            break;
        case 'snapshot':
            renderSnapshot(data);
            addEquityPoint(ts, data.balance);
            break;
        case 'price_update':
            updateWatchedPrice(data);
            break;
        case 'error':
            addTradeEntry(ts, 'error', data);
            break;
        default:
            break;
    }
}

/* ══════════════════════════════════════════════
   PHASE 1 — SCAN
   ══════════════════════════════════════════════ */

async function doScan() {
    const btn = document.getElementById('btn-scan');
    const strategy = document.getElementById('scan-strategy').value;
    btn.disabled = true;
    btn.textContent = '⏳ Scanning…';

    document.getElementById('scan-status').innerHTML =
        '<p><span aria-busy="true">Scanning live markets…</span></p>';
    document.getElementById('scan-results').style.display = 'none';

    try {
        const resp = await fetch(`/api/scan?strategy=${strategy}`, { method: 'POST' });
        const j = await resp.json();

        if (j.error) {
            document.getElementById('scan-status').innerHTML =
                `<p style="color:var(--pico-del-color);">Error: ${j.error}</p>`;
            return;
        }

        scanCandidates = j.candidates || [];
        selectedTokenIds.clear();

        if (scanCandidates.length === 0) {
            document.getElementById('scan-status').innerHTML =
                '<p>No candidates found matching strategy filters. Try a different strategy.</p>';
            document.getElementById('scan-results').style.display = 'none';
            document.getElementById('start-monitoring-bar').style.display = 'none';
            return;
        }

        // Select all by default
        scanCandidates.forEach(c => selectedTokenIds.add(c.token_id));

        renderCandidatesTable(scanCandidates);
        document.getElementById('scan-status').style.display = 'none';
        document.getElementById('scan-results').style.display = 'block';
        document.getElementById('start-monitoring-bar').style.display = 'block';
        updateSelectionCount();
    } catch (e) {
        document.getElementById('scan-status').innerHTML =
            `<p style="color:var(--pico-del-color);">Scan failed: ${e}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '🔍 Scan Markets';
    }
}

function renderCandidatesTable(candidates) {
    const tbody = document.getElementById('candidates-tbody');
    tbody.innerHTML = '';
    setText('scan-count', candidates.length);

    candidates.forEach(c => {
        const tr = document.createElement('tr');
        const checked = selectedTokenIds.has(c.token_id) ? 'checked' : '';
        const ttrHrs = c.time_to_resolution_hrs;
        let ttrStr = '—';
        if (ttrHrs != null) {
            ttrStr = ttrHrs > 48 ? `${(ttrHrs / 24).toFixed(1)}d` : `${ttrHrs.toFixed(0)}h`;
        }
        const vol24h = c.volume_24hr || 0;
        const volStr = vol24h >= 1000 ? `$${(vol24h / 1000).toFixed(1)}k` : `$${vol24h.toFixed(0)}`;

        tr.innerHTML = `
            <td><input type="checkbox" ${checked}
                 data-token="${c.token_id}"
                 onchange="onCandidateToggle(this)"></td>
            <td><span class="badge ${c.score > 50 ? 'running' : c.score > 25 ? 'paused' : 'idle'}">${c.score}</span></td>
            <td>
                <div style="font-weight:600;">${truncate(c.event_title, 45)}
                    ${c.polymarket_url ? `<a href="${c.polymarket_url}" target="_blank" rel="noopener" title="View on Polymarket" style="margin-left:0.3rem;font-size:0.7rem;text-decoration:none;">🔗</a>` : ''}
                </div>
                <div style="color:var(--pico-muted-color);font-size:0.75rem;">${truncate(c.question, 55)}</div>
            </td>
            <td>${fmtUSD(c.midpoint)}</td>
            <td>${fmt(c.spread, 4)}</td>
            <td>${volStr}</td>
            <td>${ttrStr}</td>
            <td style="font-size:0.75rem;color:var(--pico-muted-color);">${truncate(c.reasoning, 40)}</td>
        `;
        tbody.appendChild(tr);
    });
}

function onCandidateToggle(checkbox) {
    const tokenId = checkbox.dataset.token;
    if (checkbox.checked) {
        selectedTokenIds.add(tokenId);
    } else {
        selectedTokenIds.delete(tokenId);
    }
    updateSelectionCount();
}

function toggleSelectAll(checkbox) {
    const allChecked = checkbox ?
        checkbox.checked :
        selectedTokenIds.size < scanCandidates.length;

    const boxes = document.querySelectorAll('#candidates-tbody input[type="checkbox"]');
    boxes.forEach(cb => {
        cb.checked = allChecked;
        const token = cb.dataset.token;
        if (allChecked) selectedTokenIds.add(token);
        else selectedTokenIds.delete(token);
    });

    const selectAllCb = document.getElementById('select-all-cb');
    if (selectAllCb) selectAllCb.checked = allChecked;

    updateSelectionCount();
}

function updateSelectionCount() {
    setText('scan-selected-count', selectedTokenIds.size);
    const btn = document.getElementById('btn-start-monitoring');
    if (btn) {
        btn.disabled = selectedTokenIds.size === 0;
        btn.textContent = `▶ Start Monitoring ${selectedTokenIds.size} Market${selectedTokenIds.size !== 1 ? 's' : ''}`;
    }
}

/* ══════════════════════════════════════════════
   PHASE 2 — MONITOR
   ══════════════════════════════════════════════ */

async function startMonitoring() {
    if (selectedTokenIds.size === 0) {
        alert('Select at least one market to monitor.');
        return;
    }

    const btn = document.getElementById('btn-start-monitoring');
    btn.disabled = true;
    btn.textContent = '⏳ Starting…';

    try {
        // 1. Set watchlist
        await fetch('/api/sim/watchlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token_ids: Array.from(selectedTokenIds) }),
        });

        // 2. Build watched markets map
        watchedMarkets = {};
        scanCandidates.forEach(c => {
            if (selectedTokenIds.has(c.token_id)) {
                watchedMarkets[c.token_id] = { ...c };
            }
        });

        // 3. Start simulation
        const strategy = document.getElementById('scan-strategy').value;
        const resp = await fetch(`/api/sim/start?strategy=${strategy}`, { method: 'POST' });
        const j = await resp.json();

        if (j.error) {
            alert(j.error);
            btn.disabled = false;
            btn.textContent = '▶ Start Monitoring Selected Markets';
            return;
        }

        // 4. Switch to monitor phase
        showPhase('monitor');
        setText('sim-run-id', j.run_id || '');
        setText('sim-strategy', strategy);
        setText('watchlist-count', selectedTokenIds.size);
        renderStatusBadge('running');
        renderWatchedList();
    } catch (e) {
        alert('Failed to start: ' + e);
        btn.disabled = false;
        btn.textContent = '▶ Start Monitoring Selected Markets';
    }
}

async function pauseSim() {
    try {
        await fetch('/api/sim/pause', { method: 'POST' });
    } catch (e) { alert(e); }
}

async function stopSim() {
    try {
        await fetch('/api/sim/stop', { method: 'POST' });
    } catch (e) { alert(e); }
}

function renderMonitorState(state) {
    renderStatusBadge(state.status || 'idle');
    if (state.balance != null) {
        setText('stat-balance', fmtUSD(state.balance));
    }
    setText('sim-tick', state.tick_count || 0);
    setText('watchlist-count', (state.watchlist || []).length);
    if (state.run) {
        setText('sim-run-id', state.run.run_id || '');
        setText('sim-strategy', state.run.strategy || '');
    }
}

function renderStatusBadge(status) {
    const el = document.getElementById('sim-status-badge');
    if (!el) return;
    el.textContent = status.toUpperCase();
    el.className = 'badge ' + (status || 'idle');

    const btnPause = document.getElementById('btn-pause');
    const btnStop = document.getElementById('btn-stop');
    if (btnPause) btnPause.disabled = (status !== 'running');
    if (btnStop) btnStop.disabled = (status !== 'running' && status !== 'paused');
}

function renderSnapshot(data) {
    setText('stat-balance', fmtUSD(data.balance));
    const pnl = data.unrealized_pnl || 0;
    const el = document.getElementById('stat-unrealized');
    if (el) {
        el.textContent = (pnl >= 0 ? '+' : '') + fmtUSD(pnl);
        el.className = 'stat-value ' + (pnl >= 0 ? 'positive' : 'negative');
    }
}

function renderWatchedList() {
    const list = document.getElementById('watched-list');
    if (!list) return;
    list.innerHTML = '';
    setText('watched-count', Object.keys(watchedMarkets).length);

    Object.values(watchedMarkets).forEach(m => {
        const item = document.createElement('div');
        item.className = 'event-item';
        item.id = `watched-${m.token_id}`;
        item.innerHTML = `
            <div class="event-title">${truncate(m.event_title, 50)}</div>
            <div class="event-meta">
                ${truncate(m.question, 55)} ·
                Price: <strong class="watched-price">${fmtUSD(m.midpoint)}</strong> ·
                Spread: ${fmt(m.spread, 4)}
            </div>
        `;
        list.appendChild(item);
    });
}

function updateWatchedPrice(data) {
    // Update price in the watched list
    if (watchedMarkets[data.token_id]) {
        watchedMarkets[data.token_id].midpoint = data.midpoint;
        const el = document.querySelector(`#watched-${CSS.escape(data.token_id)} .watched-price`);
        if (el) el.textContent = fmtUSD(data.midpoint);
    }
}

function renderPositions(positions) {
    const body = document.getElementById('positions-tbody');
    if (!body) return;
    body.innerHTML = '';
    if (!positions || positions.length === 0) {
        body.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--pico-muted-color)">No open positions</td></tr>';
        return;
    }
    positions.forEach(p => {
        const pnl = p.unrealized_pnl || 0;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${truncate(p.market || p.condition_id || '—', 30)}</td>
            <td>${p.side || '—'}</td>
            <td>${fmtUSD(p.avg_price || p.entry_price)}</td>
            <td>${fmtUSD(p.current_price)}</td>
            <td>${fmt(p.size)}</td>
            <td class="${pnlClass}">${(pnl >= 0 ? '+' : '') + fmtUSD(pnl)}</td>
        `;
        body.appendChild(tr);
    });
}

let tradeCounter = 0;

function addTradeEntry(ts, kind, data) {
    const feed = document.getElementById('trade-feed');
    if (!feed) return;
    // Clear placeholder
    const placeholder = feed.querySelector('p');
    if (placeholder) placeholder.remove();

    const entry = document.createElement('div');
    entry.className = 'trade-entry';
    let detail = '';
    let sideClass = '';

    switch (kind) {
        case 'trade':
            sideClass = (data.side === 'BUY' || data.side === 'buy') ? 'buy' : 'sell';
            detail = `<span class="${sideClass}">${data.side}</span> ${fmt(data.size)} @ ${fmtUSD(data.price)} — ${truncate(data.market_title || '', 30)}`;
            tradeCounter++;
            break;
        case 'signal':
            sideClass = (data.side === 'BUY' || data.side === 'buy') ? 'buy' : 'sell';
            detail = `Signal: <span class="${sideClass}">${data.side}</span> ${truncate(data.market_title || '', 30)} conf=${fmt(data.confidence)}`;
            break;
        case 'rejected':
            detail = `<span class="rejected">Rejected:</span> ${data.reason || '—'}`;
            break;
        case 'error':
            detail = `<span class="error">Error:</span> ${data.message || data.error || JSON.stringify(data)}`;
            break;
    }

    entry.innerHTML = `<span class="time">${fmtTime(ts)}</span><span>${detail}</span>`;
    feed.prepend(entry);

    setText('trade-feed-count', feed.querySelectorAll('.trade-entry').length);

    // Max 100 entries
    while (feed.children.length > 100) feed.removeChild(feed.lastChild);
}

function updateTradeCount() {
    setText('stat-trades', tradeCounter);
}

function updatePosCount(positions) {
    setText('stat-positions', positions ? positions.length : 0);
}

/* ── Equity Chart (Lightweight Charts) ────── */

function initChart() {
    const container = document.getElementById('equity-chart');
    if (!container || typeof LightweightCharts === 'undefined') return;

    chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 260,
        layout: {
            background: { color: 'transparent' },
            textColor: '#999',
        },
        grid: {
            vertLines: { color: 'rgba(255,255,255,0.05)' },
            horzLines: { color: 'rgba(255,255,255,0.05)' },
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
        },
        rightPriceScale: {
            borderColor: 'rgba(255,255,255,0.1)',
        },
    });

    equitySeries = chart.addLineSeries({
        color: '#0d6efd',
        lineWidth: 2,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    const ro = new ResizeObserver(() => {
        chart.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);
}

function addEquityPoint(ts, balance) {
    if (!equitySeries || balance == null) return;
    const time = Math.floor(new Date(ts).getTime() / 1000);
    equitySeries.update({ time, value: balance });
}

/* ── Runs page helpers ─────────────────────── */

async function loadRuns() {
    const body = document.getElementById('runs-body');
    if (!body) return;
    try {
        const resp = await fetch('/api/sim/runs');
        const runs = await resp.json();
        body.innerHTML = '';
        if (!runs || runs.length === 0) {
            body.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--pico-muted-color)">No simulation runs yet</td></tr>';
            return;
        }
        runs.forEach(r => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${r.run_id || '—'}</td>
                <td>${r.strategy || '—'}</td>
                <td>${fmtDate(r.started_at)}</td>
                <td><span class="badge ${r.status}">${r.status}</span></td>
                <td>${r.notes || '—'}</td>
            `;
            body.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load runs', e);
    }
}
