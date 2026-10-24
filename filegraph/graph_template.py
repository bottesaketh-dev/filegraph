"""HTML/CSS/JS template for interactive graph visualization (Section 19.2)."""

from __future__ import annotations

GRAPH_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FileGraph — Knowledge Graph</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <script type="text/javascript" src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
  <style>
    :root {
      --bg: #0f172a;
      --bg-panel: rgba(30, 41, 59, 0.85);
      --bg-card: rgba(15, 23, 42, 0.7);
      --border: rgba(148, 163, 184, 0.15);
      --border-focus: rgba(99, 102, 241, 0.5);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5);
      --radius: 12px;
      --font: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    body.light {
      --bg: #f8fafc;
      --bg-panel: rgba(255, 255, 255, 0.9);
      --bg-card: rgba(241, 245, 249, 0.85);
      --border: rgba(100, 116, 139, 0.2);
      --border-focus: rgba(99, 102, 241, 0.6);
      --text: #0f172a;
      --text-muted: #64748b;
      --accent: #4f46e5;
      --accent-hover: #4338ca;
      --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.05);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      overflow: hidden;
      height: 100vh;
      width: 100vw;
      transition: background 0.3s ease, color 0.3s ease;
    }

    #network {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
    }

    /* Header / Top Control Bar */
    .topbar {
      position: absolute;
      top: 16px;
      left: 16px;
      right: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      pointer-events: none;
      z-index: 10;
    }

    .topbar > * {
      pointer-events: auto;
    }

    .brand-box {
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--bg-panel);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      padding: 8px 16px;
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    .brand-title {
      font-weight: 700;
      font-size: 16px;
      letter-spacing: -0.02em;
      background: linear-gradient(135deg, #a5b4fc, #6366f1, #c084fc);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .stats-badge {
      font-size: 11px;
      color: var(--text-muted);
      border-left: 1px solid var(--border);
      padding-left: 10px;
    }

    /* Controls box */
    .controls-box {
      display: flex;
      align-items: center;
      gap: 8px;
      background: var(--bg-panel);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      flex-wrap: wrap;
    }

    /* Search Input */
    .search-wrapper {
      position: relative;
      display: flex;
      align-items: center;
    }

    .search-input {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text);
      font-family: inherit;
      font-size: 13px;
      padding: 6px 32px 6px 10px;
      width: 220px;
      outline: none;
      transition: all 0.2s;
    }

    .search-input:focus {
      border-color: var(--border-focus);
      width: 280px;
      box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
    }

    .search-shortcut {
      position: absolute;
      right: 8px;
      font-size: 10px;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 4px;
      padding: 2px 5px;
      color: var(--text-muted);
      pointer-events: none;
    }

    /* Dropdown for search results */
    .search-results {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-top: 4px;
      max-height: 280px;
      overflow-y: auto;
      display: none;
      z-index: 100;
      box-shadow: var(--shadow);
    }

    .search-item {
      padding: 8px 12px;
      font-size: 12px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border);
    }

    .search-item:hover, .search-item.active {
      background: var(--border-focus);
      color: #fff;
    }

    /* Toggle Buttons */
    .btn-toggle {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text-muted);
      font-family: inherit;
      font-size: 12px;
      font-weight: 500;
      padding: 6px 10px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
    }

    .btn-toggle:hover {
      color: var(--text);
      border-color: var(--text-muted);
    }

    .btn-toggle.active {
      background: var(--accent);
      color: #ffffff;
      border-color: var(--accent);
    }

    /* Legend / Filter Bar (Bottom) */
    .legend-bar {
      position: absolute;
      bottom: 16px;
      left: 16px;
      right: 16px;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 8px;
      pointer-events: none;
      z-index: 10;
      flex-wrap: wrap;
    }

    .legend-container {
      background: var(--bg-panel);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      padding: 8px 16px;
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      pointer-events: auto;
    }

    .legend-chip {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 500;
      padding: 4px 10px;
      border-radius: 20px;
      border: 1px solid transparent;
      cursor: pointer;
      transition: all 0.2s;
      user-select: none;
      opacity: 1;
    }

    .legend-chip.muted {
      opacity: 0.35;
      text-decoration: line-through;
    }

    .legend-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }

    /* Detail Drawer (Right Side) */
    .drawer {
      position: absolute;
      top: 16px;
      bottom: 16px;
      right: 16px;
      width: 380px;
      background: var(--bg-panel);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      z-index: 20;
      transform: translateX(420px);
      transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
      overflow: hidden;
    }

    .drawer.open {
      transform: translateX(0);
    }

    .drawer-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }

    .drawer-badges {
      display: flex;
      gap: 6px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }

    .badge {
      font-size: 10px;
      text-transform: uppercase;
      font-weight: 700;
      letter-spacing: 0.05em;
      padding: 2px 8px;
      border-radius: 4px;
    }

    .drawer-title {
      font-size: 16px;
      font-weight: 600;
      line-height: 1.3;
      word-break: break-word;
    }

    .btn-close {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 20px;
      line-height: 1;
      padding: 4px;
      border-radius: 4px;
    }

    .btn-close:hover {
      color: var(--text);
    }

    .drawer-body {
      padding: 16px 20px;
      overflow-y: auto;
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .section-title {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 6px;
    }

    .summary-text {
      font-size: 13px;
      line-height: 1.5;
      color: var(--text);
    }

    .meta-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }

    .meta-table td {
      padding: 6px 0;
      border-bottom: 1px solid var(--border);
    }

    .meta-table td:first-child {
      color: var(--text-muted);
      width: 35%;
    }

    .connected-list {
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .connected-item {
      padding: 8px 10px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: background 0.2s;
    }

    .connected-item:hover {
      border-color: var(--accent);
    }

    .connected-rel {
      font-size: 10px;
      color: var(--text-muted);
      text-transform: uppercase;
    }

    .btn-open-file {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      background: var(--accent);
      color: #ffffff;
      padding: 8px 14px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 12px;
      font-weight: 600;
      margin-top: 10px;
      transition: background 0.2s;
    }

    .btn-open-file:hover {
      background: var(--accent-hover);
    }

    /* Mobile Responsive */
    @media (max-width: 820px) {
      .topbar { flex-direction: column; align-items: stretch; }
      .brand-box { justify-content: space-between; }
      .controls-box { justify-content: space-between; }
      .search-input { width: 100%; }
      .drawer { left: 16px; width: auto; transform: translateY(105%); }
      .drawer.open { transform: translateY(0); }
    }

    @media (prefers-reduced-motion: reduce) {
      * { transition: none !important; animation: none !important; }
    }
  </style>
</head>
<body>
  <div id="network"></div>

  <!-- Top bar -->
  <div class="topbar">
    <div class="brand-box">
      <div class="brand-title">FileGraph</div>
      <div class="stats-badge" id="stats-badge">0 nodes · 0 edges</div>
    </div>

    <div class="controls-box">
      <div class="search-wrapper">
        <input type="text" class="search-input" id="search-input" placeholder="Search files, entities, repos...">
        <span class="search-shortcut">/</span>
        <div class="search-results" id="search-results"></div>
      </div>

      <button class="btn-toggle" id="btn-labels" title="Toggle node labels">
        <span>🏷️</span> Labels
      </button>

      <button class="btn-toggle" id="btn-focus" title="Dim unconnected nodes">
        <span>🎯</span> Focus
      </button>

      <button class="btn-toggle" id="btn-code" title="Toggle code imports">
        <span>💻</span> Code
      </button>

      <button class="btn-toggle" id="btn-mentions" title="Show all entity mentions">
        <span>👥</span> All Mentions
      </button>

      <button class="btn-toggle" id="btn-fit" title="Fit to view">
        <span>⛶</span> Fit
      </button>

      <button class="btn-toggle" id="btn-theme" title="Toggle light/dark mode">
        <span id="theme-icon">☀️</span>
      </button>
    </div>
  </div>

  <!-- Legend chips at bottom -->
  <div class="legend-bar">
    <div class="legend-container" id="legend-chips">
      <!-- Injected dynamically -->
    </div>
  </div>

  <!-- Slide-in detail drawer -->
  <div class="drawer" id="drawer">
    <div class="drawer-header">
      <div>
        <div class="drawer-badges" id="drawer-badges"></div>
        <div class="drawer-title" id="drawer-title"></div>
      </div>
      <button class="btn-close" id="btn-close-drawer">✕</button>
    </div>
    <div class="drawer-body" id="drawer-body">
      <!-- Injected dynamically -->
    </div>
  </div>

  <script>
    const GRAPH_DATA = __GRAPH_DATA__;

    const CARD_COLORS = {
      identity: '#f59e0b',
      finance: '#10b981',
      legal: '#8b5cf6',
      generic: '#0ea5e9',
      code: '#06b6d4',
      entity: '#ec4899',
      repo: '#6366f1',
      missing: '#ef4444',
      other: '#64748b'
    };

    let network = null;
    let allNodes = [];
    let allEdges = [];
    let activeNodes = new vis.DataSet();
    let activeEdges = new vis.DataSet();
    let hiddenGroups = new Set();
    let allLabelsEnabled = false;
    let focusModeEnabled = false;
    let showCodeEnabled = false;
    let showAllMentions = false;
    let selectedNodeId = null;

    function init() {
      allNodes = GRAPH_DATA.nodes || [];
      allEdges = GRAPH_DATA.edges || [];

      document.getElementById('stats-badge').textContent =
        `${allNodes.length} nodes · ${allEdges.length} edges`;

      buildLegend();
      setupEventListeners();
      renderNetwork();
    }

    function buildLegend() {
      const legendContainer = document.getElementById('legend-chips');
      legendContainer.innerHTML = '';

      const presentGroups = new Set(allNodes.map(n => n.group));

      presentGroups.forEach(group => {
        const color = CARD_COLORS[group] || '#64748b';
        const count = allNodes.filter(n => n.group === group).length;

        const chip = document.createElement('div');
        chip.className = 'legend-chip';
        chip.style.borderColor = color;
        chip.style.color = 'var(--text)';
        chip.innerHTML = `<span class="legend-dot" style="background:${color}"></span>${group} (${count})`;

        chip.addEventListener('click', () => {
          if (hiddenGroups.has(group)) {
            hiddenGroups.delete(group);
            chip.classList.remove('muted');
          } else {
            hiddenGroups.add(group);
            chip.classList.add('muted');
          }
          applyFilters();
        });

        legendContainer.appendChild(chip);
      });
    }

    function getNodeDegree(nodeId) {
      return allEdges.filter(e => e.from === nodeId || e.to === nodeId).length;
    }

    function applyFilters() {
      const filteredNodes = allNodes.filter(n => !hiddenGroups.has(n.group));
      const validNodeIds = new Set(filteredNodes.map(n => n.id));

      const filteredEdges = allEdges.filter(e => {
        if (!validNodeIds.has(e.from) || !validNodeIds.has(e.to)) return false;
        if (e.edge_type === 'imports' && !showCodeEnabled) return false;
        if (e.edge_type === 'mentioned_in' && !showAllMentions) return false;
        if (e.edge_type === 'contains_file') return false; // hidden by default per spec
        return true;
      });

      // Prepare nodes with dynamic labels and sizes
      const visNodes = filteredNodes.map(n => {
        const degree = getNodeDegree(n.id);
        const isHub = degree >= 3;
        const showLabel = allLabelsEnabled || isHub || (selectedNodeId === n.id);

        let size = 16;
        if (n.kind === 'entity') {
          size = Math.min(26, Math.max(12, 12 + Math.sqrt(degree) * 3));
        } else if (n.kind === 'repo') {
          size = 22;
        }

        const color = CARD_COLORS[n.group] || '#64748b';

        return {
          id: n.id,
          label: showLabel ? n.label : '',
          fullLabel: n.label,
          title: n.label + (n.subtitle ? ' — ' + n.subtitle : ''),
          shape: n.kind === 'entity' ? 'diamond' : (n.kind === 'repo' ? 'box' : 'dot'),
          size: size,
          color: {
            background: color,
            border: color,
            highlight: { background: '#ffffff', border: color },
            hover: { background: '#ffffff', border: color }
          },
          font: { color: document.body.classList.contains('light') ? '#0f172a' : '#f8fafc', face: 'Inter', size: 12 },
          raw: n
        };
      });

      const visEdges = filteredEdges.map(e => ({
        id: e.id || `${e.from}-${e.to}-${e.edge_type}`,
        from: e.from,
        to: e.to,
        label: allLabelsEnabled ? e.edge_type : '',
        title: `${e.edge_type}${e.evidence ? ': ' + e.evidence : ''}`,
        arrows: e.arrows || 'to',
        color: { color: 'rgba(148, 163, 184, 0.4)', highlight: '#6366f1', hover: '#a5b4fc' },
        smooth: { type: 'curvedCW', roundness: 0.15 },
        raw: e
      }));

      activeNodes.clear();
      activeNodes.add(visNodes);
      activeEdges.clear();
      activeEdges.add(visEdges);

      if (network) {
        network.setData({ nodes: activeNodes, edges: activeEdges });
      }
    }

    function renderNetwork() {
      const container = document.getElementById('network');
      applyFilters();

      const options = {
        nodes: {
          borderWidth: 1.5,
          shadow: true
        },
        edges: {
          width: 1.2,
          selectionWidth: 2.5,
          hoverWidth: 1.8
        },
        physics: {
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            springLength: 140,
            gravitationalConstant: -70,
            avoidOverlap: 0.9,
            centralGravity: 0.008,
            damping: 0.4
          },
          stabilization: { iterations: 120, updateInterval: 25 }
        },
        interaction: {
          hover: true,
          tooltipDelay: 150,
          hideEdgesOnDrag: true
        }
      };

      network = new vis.Network(container, { nodes: activeNodes, edges: activeEdges }, options);

      network.on('click', params => {
        if (params.nodes.length > 0) {
          selectNode(params.nodes[0]);
        } else {
          deselectNode();
        }
      });

      network.on('zoom', params => {
        if (!allLabelsEnabled) {
          const zoom = network.getScale();
          const showAll = zoom >= 1.6;
          activeNodes.forEach(n => {
            const shouldShow = showAll || getNodeDegree(n.id) >= 3 || (selectedNodeId === n.id);
            if ((n.label !== '') !== shouldShow) {
              activeNodes.update({ id: n.id, label: shouldShow ? n.fullLabel : '' });
            }
          });
        }
      });
    }

    function selectNode(nodeId) {
      selectedNodeId = nodeId;
      const node = allNodes.find(n => n.id === nodeId);
      if (!node) return;

      openDrawer(node);

      if (focusModeEnabled) {
        applyFocus(nodeId);
      }
    }

    function deselectNode() {
      selectedNodeId = null;
      closeDrawer();
      if (focusModeEnabled) {
        resetFocus();
      }
    }

    function applyFocus(nodeId) {
      const connectedNodeIds = new Set(network.getConnectedNodes(nodeId));
      connectedNodeIds.add(nodeId);

      const updates = [];
      activeNodes.forEach(n => {
        const isConn = connectedNodeIds.has(n.id);
        updates.push({
          id: n.id,
          opacity: isConn ? 1 : 0.15
        });
      });
      activeNodes.update(updates);
    }

    function resetFocus() {
      const updates = [];
      activeNodes.forEach(n => {
        updates.push({ id: n.id, opacity: 1 });
      });
      activeNodes.update(updates);
    }

    function openDrawer(node) {
      const drawer = document.getElementById('drawer');
      const badges = document.getElementById('drawer-badges');
      const title = document.getElementById('drawer-title');
      const body = document.getElementById('drawer-body');

      const color = CARD_COLORS[node.group] || '#64748b';
      badges.innerHTML = `
        <span class="badge" style="background:${color}22; color:${color}; border:1px solid ${color}66">${node.kind}: ${node.group}</span>
        ${node.status ? `<span class="badge" style="background:rgba(255,255,255,0.1); color:var(--text)">${node.status}</span>` : ''}
        ${node.confidence ? `<span class="badge" style="background:rgba(99,102,241,0.2); color:#a5b4fc">${node.confidence}</span>` : ''}
      `;

      title.textContent = node.label;

      let html = '';

      if (node.summary) {
        html += `
          <div>
            <div class="section-title">Summary</div>
            <div class="summary-text">${escapeHtml(node.summary)}</div>
          </div>
        `;
      }

      // Meta Table
      html += `<div><div class="section-title">Details</div><table class="meta-table">`;
      if (node.main_date) html += `<tr><td>Date</td><td>${escapeHtml(node.main_date)}</td></tr>`;
      if (node.main_entity) html += `<tr><td>Main Entity</td><td>${escapeHtml(node.main_entity)}</td></tr>`;
      if (node.rel_path) html += `<tr><td>Relative Path</td><td><code>${escapeHtml(node.rel_path)}</code></td></tr>`;
      html += `</table></div>`;

      // Facts
      if (node.facts && Object.keys(node.facts).length > 0) {
        html += `<div><div class="section-title">Key Facts</div><table class="meta-table">`;
        for (const [k, v] of Object.entries(node.facts)) {
          html += `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`;
        }
        html += `</table></div>`;
      }

      // Connected Nodes
      const connectedEdges = allEdges.filter(e => e.from === node.id || e.to === node.id);
      if (connectedEdges.length > 0) {
        html += `<div><div class="section-title">Connected Items (${connectedEdges.length})</div><ul class="connected-list">`;
        connectedEdges.forEach(e => {
          const targetId = e.from === node.id ? e.to : e.from;
          const targetNode = allNodes.find(n => n.id === targetId);
          if (targetNode) {
            html += `
              <li class="connected-item" onclick="selectNode(${targetId}); network.focus(${targetId}, {scale:1.2, animation:true})">
                <span>${escapeHtml(targetNode.label)}</span>
                <span class="connected-rel">${escapeHtml(e.edge_type)}</span>
              </li>
            `;
          }
        });
        html += `</ul></div>`;
      }

      // Open Original File button
      if (node.file_url) {
        html += `
          <a class="btn-open-file" href="${node.file_url}" target="_blank" rel="noopener">
            Open original file ↗
          </a>
        `;
      }

      body.innerHTML = html;
      drawer.classList.add('open');
    }

    function closeDrawer() {
      document.getElementById('drawer').classList.remove('open');
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function setupEventListeners() {
      // Search
      const searchInput = document.getElementById('search-input');
      const searchResults = document.getElementById('search-results');

      window.addEventListener('keydown', e => {
        if (e.key === '/' && document.activeElement !== searchInput) {
          e.preventDefault();
          searchInput.focus();
        } else if (e.key === 'Escape') {
          deselectNode();
          searchResults.style.display = 'none';
        }
      });

      searchInput.addEventListener('input', () => {
        const q = searchInput.value.trim().toLowerCase();
        if (!q) {
          searchResults.style.display = 'none';
          return;
        }

        const matches = allNodes.filter(n =>
          (n.label && n.label.toLowerCase().includes(q)) ||
          (n.subtitle && n.subtitle.toLowerCase().includes(q)) ||
          (n.rel_path && n.rel_path.toLowerCase().includes(q))
        ).slice(0, 8);

        if (matches.length === 0) {
          searchResults.style.display = 'none';
          return;
        }

        searchResults.innerHTML = matches.map(m => `
          <div class="search-item" data-id="${m.id}">
            <span>${escapeHtml(m.label)}</span>
            <span style="font-size:10px; color:var(--text-muted)">${m.group}</span>
          </div>
        `).join('');
        searchResults.style.display = 'block';
      });

      searchResults.addEventListener('click', e => {
        const item = e.target.closest('.search-item');
        if (item) {
          const id = Number(item.dataset.id);
          searchResults.style.display = 'none';
          searchInput.value = '';
          selectNode(id);
          network.focus(id, { scale: 1.4, animation: true });
        }
      });

      document.addEventListener('click', e => {
        if (!e.target.closest('.search-wrapper')) {
          searchResults.style.display = 'none';
        }
      });

      // Toggles
      const btnLabels = document.getElementById('btn-labels');
      btnLabels.addEventListener('click', () => {
        allLabelsEnabled = !allLabelsEnabled;
        btnLabels.classList.toggle('active', allLabelsEnabled);
        applyFilters();
      });

      const btnFocus = document.getElementById('btn-focus');
      btnFocus.addEventListener('click', () => {
        focusModeEnabled = !focusModeEnabled;
        btnFocus.classList.toggle('active', focusModeEnabled);
        if (focusModeEnabled && selectedNodeId) {
          applyFocus(selectedNodeId);
        } else {
          resetFocus();
        }
      });

      const btnCode = document.getElementById('btn-code');
      btnCode.addEventListener('click', () => {
        showCodeEnabled = !showCodeEnabled;
        btnCode.classList.toggle('active', showCodeEnabled);
        applyFilters();
      });

      const btnMentions = document.getElementById('btn-mentions');
      btnMentions.addEventListener('click', () => {
        showAllMentions = !showAllMentions;
        btnMentions.classList.toggle('active', showAllMentions);
        applyFilters();
      });

      document.getElementById('btn-fit').addEventListener('click', () => {
        network.fit({ animation: { duration: 600, easingFunction: 'easeInOutQuad' } });
      });

      // Theme toggle
      const btnTheme = document.getElementById('btn-theme');
      btnTheme.addEventListener('click', () => {
        document.body.classList.toggle('light');
        const isLight = document.body.classList.contains('light');
        document.getElementById('theme-icon').textContent = isLight ? '🌙' : '☀️';
        applyFilters();
      });

      document.getElementById('btn-close-drawer').addEventListener('click', deselectNode);
    }

    window.addEventListener('DOMContentLoaded', init);
  </script>
</body>
</html>
"""
