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
  __VIS_NETWORK_SCRIPT__
  <style>
    :root {
      --bg: #0f172a;
      --bg-panel: rgba(30, 41, 59, 0.88);
      --bg-card: rgba(15, 23, 42, 0.75);
      --border: rgba(148, 163, 184, 0.18);
      --border-focus: rgba(99, 102, 241, 0.6);
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
      --bg-panel: rgba(255, 255, 255, 0.92);
      --bg-card: rgba(241, 245, 249, 0.9);
      --border: rgba(100, 116, 139, 0.22);
      --border-focus: rgba(99, 102, 241, 0.6);
      --text: #0f172a;
      --text-muted: #64748b;
      --accent: #4f46e5;
      --accent-hover: #4338ca;
      --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.08), 0 8px 10px -6px rgba(0, 0, 0, 0.04);
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
      width: 100vw;
      height: 100vh;
      position: fixed;
      top: 0;
      left: 0;
      z-index: 1;
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
      background: linear-gradient(135deg, #a5b4fc, #6366f1, #38bdf8);
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
      gap: 6px;
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
      padding: 6px 30px 6px 10px;
      width: 200px;
      outline: none;
      transition: all 0.2s;
    }

    .search-input:focus {
      border-color: var(--border-focus);
      width: 260px;
      box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.25);
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

    .search-results {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-top: 6px;
      max-height: 280px;
      overflow-y: auto;
      box-shadow: var(--shadow);
      display: none;
      z-index: 30;
    }

    .search-item {
      padding: 8px 12px;
      font-size: 12px;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
    }

    .search-item:last-child {
      border-bottom: none;
    }

    .search-item:hover {
      background: rgba(99, 102, 241, 0.15);
    }

    /* Buttons */
    .btn-toggle {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text-muted);
      font-family: inherit;
      font-size: 12px;
      font-weight: 500;
      padding: 6px 9px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.2s;
      user-select: none;
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
      padding: 6px 14px;
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
      width: 390px;
      background: var(--bg-panel);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      z-index: 20;
      transform: translateX(430px);
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
      padding: 3px 8px;
      border-radius: 4px;
    }

    .drawer-title {
      font-size: 15px;
      font-weight: 600;
      line-height: 1.35;
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

    /* Entity Cards in Drawer */
    .entity-stack {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .entity-card {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 10px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 12px;
    }

    .entity-card.primary {
      border-color: rgba(99, 102, 241, 0.45);
      background: rgba(99, 102, 241, 0.1);
    }

    .entity-card.secondary {
      border-color: rgba(245, 158, 11, 0.4);
      background: rgba(245, 158, 11, 0.08);
    }

    .entity-role-badge {
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: 2px 6px;
      border-radius: 4px;
    }

    .role-primary {
      background: var(--accent);
      color: #ffffff;
    }

    .role-secondary {
      background: rgba(245, 158, 11, 0.25);
      color: #f59e0b;
      border: 1px solid rgba(245, 158, 11, 0.4);
    }

    .role-org {
      background: rgba(139, 92, 246, 0.25);
      color: #a78bfa;
      border: 1px solid rgba(139, 92, 246, 0.4);
    }

    .role-other {
      background: rgba(100, 116, 139, 0.2);
      color: var(--text-muted);
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
      transition: border-color 0.2s, background 0.2s;
    }

    .connected-item:hover {
      border-color: var(--accent);
      background: rgba(99, 102, 241, 0.1);
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
      margin-top: 6px;
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
        <input type="text" class="search-input" id="search-input" placeholder="Search files, entities...">
        <span class="search-shortcut">/</span>
        <div class="search-results" id="search-results"></div>
      </div>

      <button class="btn-toggle active" id="btn-group-folder" title="Group documents by folder hubs">
        <span>📁</span> Folders
      </button>

      <button class="btn-toggle" id="btn-layout-tree" title="Toggle Hierarchical Tree view (Left-to-Right)">
        <span>🌳</span> Tree
      </button>

      <button class="btn-toggle" id="btn-labels" title="Toggle node labels">
        <span>🏷️</span> Labels
      </button>

      <button class="btn-toggle" id="btn-focus" title="Dim unconnected nodes">
        <span>🎯</span> Focus
      </button>

      <button class="btn-toggle" id="btn-orphans" title="Show or hide 0-degree disconnected nodes">
        <span>👻</span> Orphans
      </button>

      <button class="btn-toggle" id="btn-mentions" title="Show all entity mentions">
        <span>👥</span> Mentions
      </button>

      <button class="btn-toggle" id="btn-top25" title="Focus on busiest hubs">
        <span>🌐</span> <span id="top25-text">Top Hubs</span>
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

    // Semantic domain color palette
    const VISUAL_COLORS = {
      education: '#6366f1',   // Indigo (Certificates, Memos)
      projects: '#06b6d4',    // Cyan (Straive project overviews)
      identity: '#f59e0b',    // Amber (Aadhaar, Voter ID, Passport)
      finance: '#10b981',     // Emerald (Invoices, Tax, Bank)
      awards: '#a855f7',      // Purple (Training, Awards, Slides)
      folder: '#3b82f6',      // Blue Hub (Folder Cluster)
      entity: '#ec4899',      // Pink Diamond (Institutions, Orgs)
      tech_stack: '#64748b',  // Slate (Technologies & Tools)
      repo: '#8b5cf6',        // Violet (Code Repositories)
      generic: '#0ea5e9'      // Sky Blue (Other Documents)
    };

    // Self names to suppress as central star-hubs
    const SELF_NORMS = new Set([
      'botte saketh', 'saketh botte', 'mr botte saketh', 'mr saketh botte',
      'saketh botte b saketh', 'saketh botte, b. saketh',
      'sri/srimathi/kumari botte saketh', 'sri srimathi kumari botte saketh',
      'saketh'
    ]);

    let network = null;
    let allNodes = [];
    let allEdges = [];
    let activeNodes = null;
    let activeEdges = null;

    let groupByFolderEnabled = true;
    let hierarchicalEnabled = false;
    let showOrphansEnabled = true;
    let allLabelsEnabled = true;
    let focusModeEnabled = false;
    let showCodeEnabled = false;
    let showAllMentions = false;
    let top25Active = false;
    let selectedNodeId = null;
    let hiddenGroups = new Set();
    let currentFilteredEdges = [];

    // Derive semantic visual group based on folder path, entity kind, and category
    function deriveVisualGroup(n) {
      if (n.kind === 'folder') return 'folder';
      if (n.kind === 'repo') return 'repo';
      if (n.kind === 'entity') {
        const kind = (n.facts && n.facts.Kind) ? n.facts.Kind : 'entity';
        return kind === 'product' ? 'tech_stack' : 'entity';
      }
      const p = (n.rel_path || '').toLowerCase();
      if (p.includes('educational_certificates')) return 'education';
      if (p.includes('straive_documents')) return 'projects';
      if (p.includes('personal_documents')) return 'identity';
      if (p.includes('invoices')) return 'finance';
      if (p.includes('training_and_achievement')) return 'awards';

      const cat = (n.category || n.group || '').toLowerCase();
      if (cat.includes('education') || cat.includes('certificate')) return 'education';
      if (cat.includes('project') || cat.includes('tech')) return 'projects';
      if (cat.includes('identity') || cat.includes('passport') || cat.includes('birth')) return 'identity';
      if (cat.includes('finance') || cat.includes('invoice')) return 'finance';
      return 'generic';
    }

    function isSelfEntity(node) {
      if (node.kind !== 'entity') return false;
      const lbl = (node.label || '').toLowerCase().trim();
      return SELF_NORMS.has(lbl) || Boolean(node.is_self);
    }

    window.onerror = function(msg, url, lineNo, columnNo, error) {
      console.error('FileGraph Error:', msg, error);
      const badge = document.getElementById('stats-badge');
      if (badge) {
        badge.style.color = '#ef4444';
        badge.textContent = 'Error: ' + msg;
      }
    };

    function init() {
      try {
        const visLib = window.vis || (typeof vis !== 'undefined' ? vis : null);
        if (!visLib || !visLib.DataSet || !visLib.Network) {
          console.error('vis-network library failed to load or missing DataSet/Network');
          const badge = document.getElementById('stats-badge');
          if (badge) {
            badge.style.color = '#ef4444';
            badge.textContent = 'Error: vis-network failed to load. Check internet or local vis-network.min.js';
          }
          return;
        }
        activeNodes = new visLib.DataSet();
        activeEdges = new visLib.DataSet();
        allNodes = GRAPH_DATA.nodes || [];
        allEdges = GRAPH_DATA.edges || [];

        buildLegend();
        setupEventListeners();
        renderNetwork();
      } catch (err) {
        console.error('Initialization error:', err);
        const badge = document.getElementById('stats-badge');
        if (badge) {
          badge.style.color = '#ef4444';
          badge.textContent = 'Init Error: ' + err.message;
        }
      }
    }

    function buildLegend() {
      const legendContainer = document.getElementById('legend-chips');
      legendContainer.innerHTML = '';

      // Count occurrences by derived visual group
      const groupCounts = {};
      allNodes.forEach(n => {
        const vg = deriveVisualGroup(n);
        groupCounts[vg] = (groupCounts[vg] || 0) + 1;
      });

      if (groupByFolderEnabled) {
        groupCounts['folder'] = 5;
      }

      Object.keys(groupCounts).sort().forEach(group => {
        const color = VISUAL_COLORS[group] || '#64748b';
        const count = groupCounts[group];

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

    function applyFilters() {
      // 1. Filter out noisy edges (spurious Today: chains and grade-sheet loops)
      const validEdges = allEdges.filter(e => {
        if (e.edge_type === 'imports' && !showCodeEnabled) return false;
        if (e.edge_type === 'mentioned_in' && !showAllMentions) return false;
        if (e.edge_type === 'contains_file') return false;
        if (e.edge_type === 'replies_to' && (
          (e.evidence && e.evidence.includes('Today:')) ||
          ['R20', 'SEMESTER GRADE SHEET', 'SEE+CIE'].includes(e.evidence)
        )) return false;
        return true;
      });

      // 2. Build virtual Folder Hubs if enabled
      const folderNodes = [];
      const folderEdges = [];
      if (groupByFolderEnabled) {
        const folders = new Map();
        allNodes.forEach(n => {
          if (n.kind !== 'doc' || !n.rel_path) return;
          const parts = n.rel_path.split('/').filter(Boolean);
          let topFolder = parts[0] || 'Documents';
          if (parts.length > 1 && ['saketh', 'saibabu', 'rama', 'adithya'].includes(parts[0].toLowerCase())) {
            topFolder = parts[1];
          }
          if (!folders.has(topFolder)) {
            const folderId = `folder:${topFolder}`;
            const folderName = topFolder.replace(/_/g, ' ');
            folders.set(topFolder, folderId);
            folderNodes.push({
              id: folderId,
              label: folderName,
              subtitle: 'Folder Hub',
              group: 'folder',
              kind: 'folder',
              summary: `Folder cluster for files in ${folderName}`,
              vGroup: 'folder'
            });
          }
          folderEdges.push({
            id: `fe-${folders.get(topFolder)}-${n.id}`,
            from: folders.get(topFolder),
            to: n.id,
            edge_type: 'in_folder',
            arrows: 'to',
            dashes: true
          });
        });
      }

      // 3. Compute initial visible degree from active edges ONLY
      const candidateEdges = [...validEdges, ...folderEdges];
      const visibleDegree = new Map();
      candidateEdges.forEach(e => {
        visibleDegree.set(e.from, (visibleDegree.get(e.from) || 0) + 1);
        visibleDegree.set(e.to, (visibleDegree.get(e.to) || 0) + 1);
      });

      // 4. Filter nodes (hide self-entities, hidden groups, and 0-degree orphans)
      const combinedNodes = [
        ...allNodes.map(n => ({ ...n, vGroup: deriveVisualGroup(n) })),
        ...folderNodes
      ];

      // Top 25 filter if active
      let allowedHubNodeIds = null;
      if (top25Active) {
        const candidateHubs = combinedNodes.filter(n => (n.kind === 'entity' || n.kind === 'folder' || n.kind === 'repo') && !hiddenGroups.has(n.vGroup));
        const hubDegrees = candidateHubs.map(h => ({
          id: h.id,
          degree: visibleDegree.get(h.id) || 0
        }));
        hubDegrees.sort((a, b) => b.degree - a.degree);
        const topHubIds = new Set(hubDegrees.slice(0, 25).map(h => h.id));
        allowedHubNodeIds = new Set(topHubIds);
        candidateEdges.forEach(e => {
          if (topHubIds.has(e.from)) allowedHubNodeIds.add(e.to);
          if (topHubIds.has(e.to)) allowedHubNodeIds.add(e.from);
        });
      }

      const filteredNodes = combinedNodes.filter(n => {
        if (hiddenGroups.has(n.vGroup)) return false;
        if (isSelfEntity(n) && !showAllMentions) return false;
        if (top25Active && allowedHubNodeIds && !allowedHubNodeIds.has(n.id)) return false;
        const deg = visibleDegree.get(n.id) || 0;
        if (deg === 0 && !showOrphansEnabled) return false;
        return true;
      });

      const validNodeIds = new Set(filteredNodes.map(n => n.id));
      const finalEdges = candidateEdges.filter(e => validNodeIds.has(e.from) && validNodeIds.has(e.to));

      currentFilteredEdges = finalEdges;

      // 5. Recalculate true visible degree after node pruning
      const finalDegree = new Map();
      finalEdges.forEach(e => {
        finalDegree.set(e.from, (finalDegree.get(e.from) || 0) + 1);
        finalDegree.set(e.to, (finalDegree.get(e.to) || 0) + 1);
      });

      // 6. Format nodes for vis-network
      const visNodes = filteredNodes.map(n => {
        const deg = finalDegree.get(n.id) || 0;
        const isFolder = n.kind === 'folder';
        const isEntity = n.kind === 'entity';
        const isRepo = n.kind === 'repo';
        const isHub = isFolder || deg >= 3;

        const rawLabel = n.label || '';
        const displayLabel = (!rawLabel || rawLabel.toLowerCase() === 'unknown')
          ? (n.rel_path ? n.rel_path.split('/').pop() : 'Document')
          : rawLabel;

        const color = VISUAL_COLORS[n.vGroup] || '#64748b';

        return {
          id: n.id,
          label: (allLabelsEnabled || isHub || selectedNodeId === n.id) ? displayLabel : '',
          fullLabel: displayLabel,
          title: displayLabel + (n.subtitle ? ' — ' + n.subtitle : '') + (n.rel_path ? ' — ' + n.rel_path : ''),
          shape: isFolder ? 'box' : (isEntity ? 'diamond' : (isRepo ? 'box' : 'dot')),
          size: isFolder ? 24 : (isEntity ? Math.min(26, Math.max(12, 12 + Math.sqrt(deg) * 3)) : 15),
          color: {
            background: color,
            border: isFolder ? '#2563eb' : color,
            highlight: { background: '#ffffff', border: color },
            hover: { background: '#ffffff', border: color }
          },
          font: {
            color: document.body.classList.contains('light') ? '#0f172a' : '#f8fafc',
            face: 'Inter',
            size: isFolder ? 13 : 11,
            bold: isFolder
          },
          raw: n
        };
      });

      const visEdges = finalEdges.map((e, idx) => ({
        id: e.id || `edge-${e.from}-${e.to}-${e.edge_type}-${idx}`,
        from: e.from,
        to: e.to,
        label: allLabelsEnabled && e.edge_type !== 'in_folder' ? e.edge_type : '',
        title: `${e.edge_type}${e.evidence ? ': ' + e.evidence : ''}`,
        arrows: e.arrows || 'to',
        dashes: e.edge_type === 'in_folder' || Boolean(e.dashes),
        color: {
          color: e.edge_type === 'in_folder'
            ? (document.body.classList.contains('light') ? 'rgba(59, 130, 246, 0.35)' : 'rgba(59, 130, 246, 0.3)')
            : (document.body.classList.contains('light') ? 'rgba(100, 116, 139, 0.45)' : 'rgba(148, 163, 184, 0.45)'),
          highlight: '#6366f1',
          hover: '#a5b4fc'
        },
        smooth: e.edge_type === 'in_folder' ? false : { type: 'curvedCW', roundness: 0.15 },
        raw: e
      }));

      // Safely deduplicate nodes and edges before adding to DataSets to prevent crashes
      activeNodes.clear();
      const seenNodeIds = new Set();
      const uniqueVisNodes = visNodes.filter(n => {
        if (seenNodeIds.has(n.id)) return false;
        seenNodeIds.add(n.id);
        return true;
      });
      activeNodes.add(uniqueVisNodes);

      activeEdges.clear();
      const seenEdgeIds = new Set();
      const uniqueVisEdges = visEdges.filter(e => {
        if (seenEdgeIds.has(e.id)) return false;
        seenEdgeIds.add(e.id);
        return true;
      });
      activeEdges.add(uniqueVisEdges);

      document.getElementById('stats-badge').textContent =
        `${uniqueVisNodes.length} nodes · ${uniqueVisEdges.length} edges`;

      if (network) {
        network.setData({ nodes: activeNodes, edges: activeEdges });
      }
    }

    function setNetworkLayout(hierarchical) {
      hierarchicalEnabled = hierarchical;
      const btnTree = document.getElementById('btn-layout-tree');
      if (btnTree) btnTree.classList.toggle('active', hierarchicalEnabled);

      if (hierarchicalEnabled) {
        network.setOptions({
          layout: {
            hierarchical: {
              enabled: true,
              direction: 'LR',
              sortMethod: 'directed',
              levelSeparation: 260,
              nodeSpacing: 100,
              treeSpacing: 120
            }
          },
          physics: {
            hierarchicalRepulsion: {
              centralGravity: 0.0,
              springLength: 120,
              springConstant: 0.01,
              nodeDistance: 130,
              damping: 0.09
            }
          }
        });
      } else {
        network.setOptions({
          layout: {
            hierarchical: {
              enabled: false
            }
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
          }
        });
      }
      network.fit({ animation: { duration: 600, easingFunction: 'easeInOutQuad' } });
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
          stabilization: false
        },
        interaction: {
          hover: true,
          tooltipDelay: 150,
          hideEdgesOnDrag: true
        }
      };

      const visLib = window.vis || (typeof vis !== 'undefined' ? vis : null);
      const NetworkClass = (visLib && visLib.Network) || vis.Network;
      network = new NetworkClass(container, { nodes: activeNodes, edges: activeEdges }, options);
      setTimeout(() => {
        if (network) {
          network.setSize('100vw', '100vh');
          network.redraw();
          network.fit();
        }
      }, 100);

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
          const showAll = zoom >= 1.5;
          activeNodes.forEach(n => {
            const deg = currentFilteredEdges.filter(e => e.from === n.id || e.to === n.id).length;
            const shouldShow = showAll || deg >= 3 || (selectedNodeId === n.id);
            if ((n.label !== '') !== shouldShow) {
              activeNodes.update({ id: n.id, label: shouldShow ? n.fullLabel : '' });
            }
          });
        }
      });
    }

    function selectNode(nodeId) {
      selectedNodeId = nodeId;
      const visNode = activeNodes.get(nodeId);
      const rawNode = visNode ? visNode.raw : allNodes.find(n => n.id === nodeId);
      if (!rawNode) return;

      openDrawer(rawNode);

      if (focusModeEnabled) {
        const connectedNodeIds = new Set(network.getConnectedNodes(nodeId));
        connectedNodeIds.add(nodeId);

        activeNodes.forEach(n => {
          const isConnected = connectedNodeIds.has(n.id);
          const color = VISUAL_COLORS[n.raw ? n.raw.vGroup : 'generic'] || '#64748b';
          activeNodes.update({
            id: n.id,
            color: isConnected ? { background: color, border: color } : { background: 'rgba(100, 116, 139, 0.15)', border: 'rgba(100, 116, 139, 0.15)' },
            font: { color: isConnected ? (document.body.classList.contains('light') ? '#0f172a' : '#f8fafc') : 'rgba(148, 163, 184, 0.2)' }
          });
        });
      }
    }

    function deselectNode() {
      selectedNodeId = null;
      closeDrawer();

      if (focusModeEnabled) {
        activeNodes.forEach(n => {
          const color = VISUAL_COLORS[n.raw ? n.raw.vGroup : 'generic'] || '#64748b';
          activeNodes.update({
            id: n.id,
            color: { background: color, border: color },
            font: { color: document.body.classList.contains('light') ? '#0f172a' : '#f8fafc' }
          });
        });
      }
    }

    function openDrawer(node) {
      const drawer = document.getElementById('drawer');
      const badgesContainer = document.getElementById('drawer-badges');
      const titleElem = document.getElementById('drawer-title');
      const body = document.getElementById('drawer-body');

      const vGroup = node.vGroup || deriveVisualGroup(node);
      const groupColor = VISUAL_COLORS[vGroup] || '#64748b';

      badgesContainer.innerHTML = `
        <span class="badge" style="background:${groupColor}; color:#ffffff">${vGroup}</span>
        ${node.category ? `<span class="badge" style="background:rgba(255,255,255,0.1); color:var(--text)">${escapeHtml(node.category)}</span>` : ''}
        ${node.status ? `<span class="badge" style="background:rgba(99,102,241,0.2); color:#a5b4fc">${escapeHtml(node.status)}</span>` : ''}
      `;

      titleElem.textContent = node.label || 'Document';

      let html = '';

      // Summary
      if (node.summary) {
        html += `
          <div>
            <div class="section-title">Summary</div>
            <div class="summary-text">${escapeHtml(node.summary)}</div>
          </div>
        `;
      }

      // Entities Section (Primary & Secondary Entities)
      if (node.entities && node.entities.length > 0) {
        html += `<div><div class="section-title">Entities & Parties</div><div class="entity-stack">`;
        node.entities.forEach(ent => {
          const isPrimary = ent.role === 'primary' || ent.role === 'holder' || ent.role === 'student';
          const isSecondary = ent.role === 'secondary' || ent.role === 'parent' || ent.role === 'father' || ent.role === 'mother';
          const isOrg = ent.kind === 'organization' || ent.role === 'issuer';

          const cardClass = isPrimary ? 'primary' : (isSecondary ? 'secondary' : '');
          const badgeClass = isPrimary ? 'role-primary' : (isSecondary ? 'role-secondary' : (isOrg ? 'role-org' : 'role-other'));
          const roleDisplay = isPrimary ? 'Primary' : (isSecondary ? (ent.role === 'parent' ? 'Secondary (Parent)' : 'Secondary') : (ent.role || ent.kind));

          html += `
            <div class="entity-card ${cardClass}">
              <span><strong>${escapeHtml(ent.name)}</strong></span>
              <span class="entity-role-badge ${badgeClass}">${escapeHtml(roleDisplay)}</span>
            </div>
          `;
        });
        html += `</div></div>`;
      } else if (node.main_entity) {
        html += `
          <div>
            <div class="section-title">Main Entity</div>
            <div class="entity-card primary">
              <span><strong>${escapeHtml(node.main_entity)}</strong></span>
              <span class="entity-role-badge role-primary">Primary</span>
            </div>
          </div>
        `;
      }

      // Details Table
      html += `<div><div class="section-title">Details</div><table class="meta-table">`;
      if (node.main_date) html += `<tr><td>Date</td><td>${escapeHtml(node.main_date)}</td></tr>`;
      if (node.rel_path) html += `<tr><td>Relative Path</td><td><code>${escapeHtml(node.rel_path)}</code></td></tr>`;
      if (node.confidence !== null && node.confidence !== undefined) html += `<tr><td>Confidence</td><td>${Math.round(node.confidence * 100)}%</td></tr>`;
      html += `</table></div>`;

      // Facts Table
      if (node.facts && Object.keys(node.facts).length > 0) {
        html += `<div><div class="section-title">Key Facts</div><table class="meta-table">`;
        for (const [k, v] of Object.entries(node.facts)) {
          html += `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`;
        }
        html += `</table></div>`;
      }

      // Connected Nodes
      const connectedEdges = currentFilteredEdges.filter(e => e.from === node.id || e.to === node.id);
      if (connectedEdges.length > 0) {
        html += `<div><div class="section-title">Connected Items (${connectedEdges.length})</div><ul class="connected-list" id="drawer-connected-list">`;
        connectedEdges.forEach(e => {
          const targetId = e.from === node.id ? e.to : e.from;
          const targetNode = allNodes.find(n => n.id === targetId) || (targetId.toString().startsWith('folder:') ? { label: targetId.replace('folder:', '').replace(/_/g, ' ') } : null);
          if (targetNode) {
            html += `
              <li class="connected-item" data-target-id="${targetId}">
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
            <span style="font-size:10px; color:var(--text-muted)">${m.group || m.kind}</span>
          </div>
        `).join('');
        searchResults.style.display = 'block';
      });

      searchResults.addEventListener('click', e => {
        const item = e.target.closest('.search-item');
        if (item) {
          const id = Number(item.dataset.id) || item.dataset.id;
          searchResults.style.display = 'none';
          searchInput.value = '';
          network.focus(id, { scale: 1.2, animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
          selectNode(id);
        }
      });

      // Folder Hubs toggle
      const btnGroupFolder = document.getElementById('btn-group-folder');
      if (btnGroupFolder) {
        btnGroupFolder.classList.toggle('active', groupByFolderEnabled);
        btnGroupFolder.addEventListener('click', () => {
          groupByFolderEnabled = !groupByFolderEnabled;
          btnGroupFolder.classList.toggle('active', groupByFolderEnabled);
          applyFilters();
        });
      }

      // Hierarchical Tree toggle
      const btnLayoutTree = document.getElementById('btn-layout-tree');
      if (btnLayoutTree) {
        btnLayoutTree.addEventListener('click', () => {
          setNetworkLayout(!hierarchicalEnabled);
        });
      }

      // Labels toggle
      const btnLabels = document.getElementById('btn-labels');
      if (btnLabels) {
        btnLabels.classList.toggle('active', allLabelsEnabled);
        btnLabels.addEventListener('click', () => {
          allLabelsEnabled = !allLabelsEnabled;
          btnLabels.classList.toggle('active', allLabelsEnabled);
          applyFilters();
        });
      }

      // Focus mode
      const btnFocus = document.getElementById('btn-focus');
      if (btnFocus) {
        btnFocus.classList.toggle('active', focusModeEnabled);
        btnFocus.addEventListener('click', () => {
          focusModeEnabled = !focusModeEnabled;
          btnFocus.classList.toggle('active', focusModeEnabled);
          if (selectedNodeId) selectNode(selectedNodeId);
        });
      }

      // Orphans toggle
      const btnOrphans = document.getElementById('btn-orphans');
      if (btnOrphans) {
        btnOrphans.classList.toggle('active', showOrphansEnabled);
        btnOrphans.addEventListener('click', () => {
          showOrphansEnabled = !showOrphansEnabled;
          btnOrphans.classList.toggle('active', showOrphansEnabled);
          applyFilters();
        });
      }

      // Mentions toggle
      const btnMentions = document.getElementById('btn-mentions');
      btnMentions.addEventListener('click', () => {
        showAllMentions = !showAllMentions;
        btnMentions.classList.toggle('active', showAllMentions);
        applyFilters();
      });

      // Top Hubs toggle
      const btnTop25 = document.getElementById('btn-top25');
      if (btnTop25) {
        btnTop25.addEventListener('click', () => {
          top25Active = !top25Active;
          btnTop25.classList.toggle('active', top25Active);
          document.getElementById('top25-text').textContent = top25Active ? 'Top Hubs' : 'All Nodes';
          applyFilters();
        });
      }

      // Fit
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

      // Drawer close
      document.getElementById('btn-close-drawer').addEventListener('click', deselectNode);

      // Event delegation for drawer connected list
      const drawerBody = document.getElementById('drawer-body');
      drawerBody.addEventListener('click', e => {
        const item = e.target.closest('.connected-item');
        if (item && item.dataset.targetId) {
          const targetId = Number(item.dataset.targetId) || item.dataset.targetId;
          network.focus(targetId, { scale: 1.2, animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
          selectNode(targetId);
        }
      });
    }

    if (document.readyState === 'loading') {
      window.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
  </script>
</body>
</html>
"""
