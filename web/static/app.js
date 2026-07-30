/* Option Income Lab — Client-side JS */

// ── Clickable table rows ──
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.clickable-row[data-href]').forEach(function(row) {
        row.addEventListener('click', function(e) {
            if (e.target.closest('.btn-trigger-row')) return;
            window.location.href = this.dataset.href;
        });
    });

    // ── Run Now trigger buttons ──
    document.querySelectorAll('.btn-trigger[data-agent]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var agentType = btn.dataset.agent;
            var origText = btn.textContent;
            btn.textContent = '⏳ Running…';
            btn.classList.add('running');
            btn.disabled = true;

            fetch('/api/trigger/' + agentType, { method: 'POST' })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.status === 'triggered') {
                        btn.textContent = '✓ Triggered';
                        btn.classList.remove('running');
                        btn.classList.add('done');
                    } else {
                        btn.textContent = '✗ Error';
                        btn.classList.remove('running');
                        btn.classList.add('error');
                    }
                })
                .catch(function() {
                    btn.textContent = '✗ Error';
                    btn.classList.remove('running');
                    btn.classList.add('error');
                })
                .finally(function() {
                    setTimeout(function() {
                        btn.textContent = origText;
                        btn.disabled = false;
                        btn.classList.remove('running', 'done', 'error');
                    }, 3000);
                });
        });
    });

    // ── Row-level trigger buttons ──
    document.querySelectorAll('.btn-trigger-row[data-agent][data-symbol]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var agentType = btn.dataset.agent;
            var symbol = btn.dataset.symbol;
            var origText = btn.textContent;
            btn.textContent = '⏳';
            btn.classList.add('running');
            btn.disabled = true;

            fetch('/api/trigger/' + agentType, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: symbol })
            })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.status === 'triggered') {
                        btn.textContent = '✓';
                        btn.classList.remove('running');
                        btn.classList.add('done');
                    } else {
                        btn.textContent = '✗';
                        btn.classList.remove('running');
                        btn.classList.add('error');
                    }
                })
                .catch(function() {
                    btn.textContent = '✗';
                    btn.classList.remove('running');
                    btn.classList.add('error');
                })
                .finally(function() {
                    setTimeout(function() {
                        btn.textContent = origText;
                        btn.disabled = false;
                        btn.classList.remove('running', 'done', 'error');
                    }, 3000);
                });
        });
    });

    // ── Run Full Analysis button ──
    var runFullBtn = document.getElementById('run-full-analysis');
    if (runFullBtn) {
        var _fullAnalysisOrigText = runFullBtn.textContent;

        runFullBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            runFullBtn.disabled = true;
            runFullBtn.textContent = '⏳ Triggering…';

            fetch('/api/trigger-all', { method: 'POST' })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.status === 'started') {
                        runFullBtn.textContent = '✓ Triggered';
                        runFullBtn.classList.add('done');
                    } else {
                        runFullBtn.textContent = '✗ ' + (data.error || 'Error');
                        runFullBtn.classList.add('error');
                    }
                })
                .catch(function() {
                    runFullBtn.textContent = '✗ Network error';
                    runFullBtn.classList.add('error');
                })
                .finally(function() {
                    setTimeout(function() {
                        runFullBtn.textContent = _fullAnalysisOrigText;
                        runFullBtn.disabled = false;
                        runFullBtn.classList.remove('done', 'error');
                    }, 3000);
                });
        });
    }
    // ── Hamburger menu toggle ──
    var hamburger = document.querySelector('.hamburger');
    var topnav = document.querySelector('.topnav');
    if (hamburger && topnav) {
        hamburger.addEventListener('click', function() {
            topnav.classList.toggle('nav-open');
        });
    }

    // ── Settings dropdown tap handler for touch devices ──
    var dropdownTrigger = document.querySelector('.nav-dropdown-trigger');
    var dropdown = document.querySelector('.nav-dropdown');
    if (dropdownTrigger && dropdown) {
        dropdownTrigger.addEventListener('click', function(e) {
            e.stopPropagation();
            dropdown.classList.toggle('dropdown-open');
        });
        document.addEventListener('click', function() {
            dropdown.classList.remove('dropdown-open');
        });
    }
});

/* ── Filtering ─────────────────────────────────────────────────── */

function cutoffDate(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d;
}

function applyDashboardFilters() {
    const activePill = document.querySelector('#activity-time-filter .pill.active');
    const days = activePill ? parseInt(activePill.dataset.range, 10) : 7;
    const symbolSelect = document.getElementById('activity-symbol-filter');
    const selectedSymbol = symbolSelect ? symbolSelect.value : '';
    const agentSelect = document.getElementById('activity-agent-filter');
    const selectedAgent = agentSelect ? agentSelect.value : '';
    const confSelect = document.getElementById('activity-confidence-filter');
    const selectedConf = confSelect ? confSelect.value : '';
    const decisionSelect = document.getElementById('activity-decision-filter');
    const selectedDecision = decisionSelect ? decisionSelect.value : '';
    const cutoff = cutoffDate(days);
    
    document.querySelectorAll('.activity-feed .activity-item').forEach(item => {
        const ts = new Date(item.dataset.timestamp);
        const sym = item.dataset.symbol || '';
        const agent = (item.dataset.agentType || '').trim();
        const conf = (item.dataset.confidence || '').trim();
        const decision = (item.dataset.decision || '').trim();
        const hasAlpha = item.dataset.hasAlpha || '';
        const timeOk = ts >= cutoff;
        const symOk = !selectedSymbol || sym === selectedSymbol;
        const agentOk = !selectedAgent || agent === selectedAgent.trim();
        const confOk = !selectedConf || conf === selectedConf;
        let decisionOk = true;
        if (selectedDecision === 'alpha') {
            decisionOk = hasAlpha === 'yes';
        } else if (selectedDecision === 'sell') {
            decisionOk = decision === 'sell';
        } else if (selectedDecision === 'wait') {
            decisionOk = decision === 'wait';
        }
        item.style.display = (timeOk && symOk && agentOk && confOk && decisionOk) ? '' : 'none';
    });
}

function applyTableFilter(pillContainerId, tableSelector, agentFilterId, confFilterId) {
    const activePill = document.querySelector('#' + pillContainerId + ' .pill.active');
    const days = activePill ? parseInt(activePill.dataset.range, 10) : 7;
    const cutoff = cutoffDate(days);
    const agentSelect = agentFilterId ? document.getElementById(agentFilterId) : null;
    const selectedAgent = agentSelect ? agentSelect.value : '';
    const confSelect = confFilterId ? document.getElementById(confFilterId) : null;
    const selectedConf = confSelect ? confSelect.value : '';
    let visible = 0;
    
    document.querySelectorAll(tableSelector + ' tbody tr').forEach(row => {
        if (row.classList.contains('pos-detail-row')) return;
        const ts = new Date(row.dataset.timestamp);
        const agent = (row.dataset.agentType || '').trim();
        const conf = (row.dataset.confidence || '').trim();
        const timeOk = ts >= cutoff;
        const agentOk = !selectedAgent || agent === selectedAgent.trim();
        const confOk = !selectedConf || conf === selectedConf;
        const show = timeOk && agentOk && confOk;
        row.style.display = show ? '' : 'none';
        if (show) visible++;
    });
    
    const header = document.querySelector('#' + pillContainerId)?.closest('.card-header');
    const badge = header?.querySelector('.card-badge');
    if (badge) badge.textContent = visible;
}

document.querySelectorAll('.filter-pills').forEach(container => {
    container.querySelectorAll('.pill').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            if (container.id === 'activity-time-filter') {
                applyDashboardFilters();
            } else if (container.id === 'sym-activity-time-filter') {
                applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-activity-agent-filter', 'sym-activity-confidence-filter');
            }
        });
    });
});

const symFilter = document.getElementById('activity-symbol-filter');
if (symFilter) {
    const symbols = new Set();
    document.querySelectorAll('.activity-feed .activity-item').forEach(item => {
        if (item.dataset.symbol) symbols.add(item.dataset.symbol);
    });
    Array.from(symbols).sort().forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        symFilter.appendChild(opt);
    });
    symFilter.addEventListener('change', applyDashboardFilters);
}

// Agent type filter on dashboard (options rendered server-side)
const dashAgentFilter = document.getElementById('activity-agent-filter');
if (dashAgentFilter) {
    dashAgentFilter.addEventListener('change', applyDashboardFilters);
}

// Agent type filter on symbol detail (options rendered server-side)
const symAgentFilter = document.getElementById('sym-activity-agent-filter');
if (symAgentFilter) {
    symAgentFilter.addEventListener('change', function() {
        applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-activity-agent-filter', 'sym-activity-confidence-filter');
    });
}

// Confidence filter on dashboard
const dashConfFilter = document.getElementById('activity-confidence-filter');
if (dashConfFilter) {
    dashConfFilter.addEventListener('change', applyDashboardFilters);
}

// Decision filter on dashboard
const dashDecisionFilter = document.getElementById('activity-decision-filter');
if (dashDecisionFilter) {
    dashDecisionFilter.addEventListener('change', applyDashboardFilters);
}

// Confidence filter on symbol detail
const symConfFilter = document.getElementById('sym-activity-confidence-filter');
if (symConfFilter) {
    symConfFilter.addEventListener('change', function() {
        applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-activity-agent-filter', 'sym-activity-confidence-filter');
    });
}

if (document.getElementById('activity-time-filter')) {
    applyDashboardFilters();
}
if (document.getElementById('sym-activity-time-filter')) {
    applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-activity-agent-filter', 'sym-activity-confidence-filter');
}

// ── Review panel toggle (Supervisor + Alpha) ──
function togglePanel(panelType) {
    var body = document.getElementById(panelType + '-body');
    var toggle = document.getElementById(panelType + '-toggle');
    if (!body) return;
    body.classList.toggle('collapsed');
    if (toggle) {
        toggle.textContent = body.classList.contains('collapsed') ? '▶' : '▼';
    }
}

// Auto-collapse WEAK/NONE panels on load
(function() {
    var weakPanels = document.querySelectorAll('.review-weak, .review-alpha-none');
    weakPanels.forEach(function(panel) {
        var body = panel.querySelector('.review-body');
        var toggle = panel.querySelector('.review-toggle');
        if (body) body.classList.add('collapsed');
        if (toggle) toggle.textContent = '▶';
    });
})();

// ── Symbol search (topnav) ──
(function initSymbolSearch() {
    var input = document.getElementById('symbol-search-input');
    var results = document.getElementById('symbol-search-results');
    var wrap = document.getElementById('nav-search');
    if (!input || !results || !wrap) return;

    var cache = null;      // [{symbol, display_name}]
    var loading = false;
    var filtered = [];
    var activeIdx = -1;

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function(c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function loadSymbols() {
        if (cache || loading) return Promise.resolve();
        loading = true;
        return fetch('/api/symbols')
            .then(function(r) { return r.ok ? r.json() : []; })
            .then(function(data) {
                cache = (Array.isArray(data) ? data : [])
                    .map(function(s) {
                        return {
                            symbol: String(s.symbol || '').toUpperCase(),
                            display_name: s.display_name || ''
                        };
                    })
                    .filter(function(s) { return s.symbol; });
                cache.sort(function(a, b) { return a.symbol.localeCompare(b.symbol); });
            })
            .catch(function() { cache = []; })
            .finally(function() { loading = false; });
    }

    function render() {
        var hasQuery = !!input.value.trim();
        if (!filtered.length) {
            results.innerHTML = hasQuery
                ? '<div class="nav-search-empty">No matches</div>' : '';
            results.classList.toggle('open', hasQuery);
            return;
        }
        results.innerHTML = filtered.map(function(s, i) {
            var name = (s.display_name && s.display_name !== s.symbol)
                ? '<span class="nav-search-name">' + escapeHtml(s.display_name) + '</span>' : '';
            return '<a href="/symbols/' + encodeURIComponent(s.symbol) + '"'
                + ' class="nav-search-item' + (i === activeIdx ? ' active' : '') + '"'
                + ' role="option" data-symbol="' + escapeHtml(s.symbol) + '">'
                + '<span class="nav-search-sym">' + escapeHtml(s.symbol) + '</span>'
                + name + '</a>';
        }).join('');
        results.classList.add('open');
    }

    function doFilter() {
        var q = input.value.trim().toUpperCase();
        activeIdx = -1;
        if (!q) { filtered = []; render(); return; }
        filtered = (cache || []).filter(function(s) {
            return s.symbol.indexOf(q) !== -1
                || (s.display_name && s.display_name.toUpperCase().indexOf(q) !== -1);
        }).slice(0, 12);
        render();
    }

    input.addEventListener('focus', function() { loadSymbols().then(doFilter); });
    input.addEventListener('input', function() { loadSymbols().then(doFilter); });

    input.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            input.value = ''; filtered = []; activeIdx = -1; render(); input.blur();
            return;
        }
        if (!filtered.length) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            activeIdx = Math.min(activeIdx + 1, filtered.length - 1); render();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            activeIdx = Math.max(activeIdx - 1, 0); render();
        } else if (e.key === 'Enter') {
            var pick = filtered[activeIdx >= 0 ? activeIdx : 0];
            if (pick) {
                e.preventDefault();
                window.location.href = '/symbols/' + encodeURIComponent(pick.symbol);
            }
        }
    });

    document.addEventListener('click', function(e) {
        if (!wrap.contains(e.target)) results.classList.remove('open');
    });
    input.addEventListener('focus', function() {
        if (filtered.length) results.classList.add('open');
    });
})();
