/* ═══════════════════════════════════════════════════════════
   GOLDFINGER — Client-side logic
   ═══════════════════════════════════════════════════════════ */

(function() {
    'use strict';

    // ── State ──
    var signals = [];
    var isScanning = false;
    var autoScanTimer = null;
    var countdownTimer = null;
    var AUTO_SCAN_INTERVAL = 45; // seconds
    var countdownLeft = 0;

    var autoTradeTimer = null;
    var autoTradeCountdownTimer = null;
    var autoTradeCountdown = 0;
    var AUTO_TRADE_INTERVAL = 45; // seconds
    var AUTO_TRADE_MIN_STARS = 3;
    var isAutoTrading = false;

    // ── DOM refs ──
    var scanBtn = document.getElementById('scanBtn');
    var autoScanBtn = document.getElementById('autoScanBtn');
    var autoTradeBtn = document.getElementById('autoTradeBtn');
    var scanStatus = document.getElementById('scanStatus');
    var signalsGrid = document.getElementById('signals-grid');
    var autoTradeSection = document.getElementById('autoTradeSection');
    var autoTradeLog = document.getElementById('autoTradeLog');
    var autoTradeBadge = document.getElementById('autoTradeBadge');

    // ── Init ──
    initChart();
    scanBtn.addEventListener('click', function() { runScan(false); });
    autoScanBtn.addEventListener('click', toggleAutoScan);
    autoTradeBtn.addEventListener('click', toggleAutoTrade);

    // Event delegation for signal card buttons
    signalsGrid.addEventListener('click', function(e) {
        var btn = e.target.closest('[data-action]');
        if (!btn) return;
        var action = btn.getAttribute('data-action');
        var idx = parseInt(btn.getAttribute('data-idx'));
        if (action === 'buy') executeTrade(idx);
        else if (action === 'skip') skipSignal(idx);
    });


    // ── Scanner ──────────────────────────────────────────────

    function runScan(light) {
        if (isScanning) return;
        isScanning = true;

        scanBtn.disabled = true;
        scanBtn.innerHTML = '<span class="btn-icon">&#9906;</span> Scanning...';
        scanStatus.textContent = 'Fetching signals...';

        var url = light ? '/api/scan?settle=0' : '/api/scan';

        fetch(url)
            .then(function(resp) { return resp.json(); })
            .then(function(data) {
                if (data.error) {
                    scanStatus.textContent = 'Error: ' + data.error;
                    return;
                }

                // Update stats
                updateStats(data.stats);
                scanStatus.textContent = 'Scanning ' + data.scanning.join(', ');

                // Render signals
                signals = data.signals || [];
                renderSignals(signals);

                // Alert on auto-scan if signals found
                if (light && signals.length > 0) {
                    playAlert();
                    document.title = '\u26a0 ' + signals.length + ' SIGNAL(S) — GOLDFINGER';
                    setTimeout(function() { document.title = 'GOLDFINGER'; }, 10000);
                }
            })
            .catch(function(e) {
                scanStatus.textContent = 'Scan failed: ' + e.message;
            })
            .finally(function() {
                isScanning = false;
                scanBtn.disabled = false;
                scanBtn.innerHTML = '<span class="btn-icon">&#9906;</span> Scan';
            });
    }


    // ── Render Signals ───────────────────────────────────────

    function renderSignals(sigs) {
        if (sigs.length === 0) {
            renderEmptyState();
            return;
        }

        signalsGrid.innerHTML = '';

        for (var i = 0; i < sigs.length; i++) {
            var s = sigs[i];
            var isLong = s.direction === 'LONG';
            var dirClass = isLong ? 'direction-long' : 'direction-short';
            var btnClass = isLong ? 'btn-buy-long' : 'btn-buy-short';
            var stars = renderStars(s.signal_strength);
            var trendLabel = s.trend_label || 'Neutral';
            var trendClass = trendLabel === 'With Trend' ? 'trend-with'
                           : trendLabel === 'Counter Trend' ? 'trend-counter'
                           : 'trend-neutral';

            var card = document.createElement('div');
            card.className = 'signal-card';
            card.id = 'signal-' + i;
            card.innerHTML =
                '<div class="signal-top">' +
                    '<span class="asset-badge">' +
                        '<span class="asset-icon">' + s.asset.charAt(0) + '</span>' +
                        s.asset +
                    '</span>' +
                    '<span class="direction-pill ' + dirClass + '">' + s.direction + '</span>' +
                    '<span class="trend-pill ' + trendClass + '">' + trendLabel + '</span>' +
                '</div>' +
                '<div class="signal-stars">' + stars + '</div>' +
                '<div class="signal-label">' + s.signal_label + '</div>' +
                '<div class="signal-details">' +
                    '<div class="detail-item">' +
                        '<span class="detail-label">Entry</span>' +
                        '<span class="detail-value">$' + s.entry_price.toFixed(2) + '</span>' +
                    '</div>' +
                    '<div class="detail-item">' +
                        '<span class="detail-label">Payout</span>' +
                        '<span class="detail-value payout">$' + s.payout.toFixed(2) + '</span>' +
                    '</div>' +
                    '<div class="detail-item">' +
                        '<span class="detail-label">Contracts</span>' +
                        '<span class="detail-value">' + s.size + '</span>' +
                    '</div>' +
                    '<div class="detail-item">' +
                        '<span class="detail-label">Time Left</span>' +
                        '<span class="detail-value"><span class="clock">\u23f1</span> ' + s.time_left + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="signal-actions" id="signal-actions-' + i + '">' +
                    '<button class="btn-buy-signal ' + btnClass + '" data-action="buy" data-idx="' + i + '">' +
                        'BUY ' + s.direction +
                    '</button>' +
                    '<button class="btn-skip-signal" data-action="skip" data-idx="' + i + '">Skip</button>' +
                '</div>';

            signalsGrid.appendChild(card);
        }
    }

    function renderStars(strength) {
        var html = '';
        for (var i = 1; i <= 5; i++) {
            if (i <= strength) {
                html += '<span class="star-active">\u2605</span>';
            } else {
                html += '<span class="star-inactive">\u2605</span>';
            }
        }
        return html;
    }

    function renderEmptyState() {
        signalsGrid.innerHTML =
            '<div class="empty-state" id="emptyState">' +
                '<div class="radar-container">' +
                    '<div class="radar-sweep"></div>' +
                    '<div class="radar-dot"></div>' +
                    '<div class="radar-ring ring-1"></div>' +
                    '<div class="radar-ring ring-2"></div>' +
                    '<div class="radar-ring ring-3"></div>' +
                '</div>' +
                '<p class="empty-text">No Signals Right Now</p>' +
                '<p class="empty-subtext">Markets are being monitored. New signals appear when opportunities arise.</p>' +
            '</div>';
    }


    // ── Stats Update ─────────────────────────────────────────

    function updateStats(stats) {
        if (!stats) return;

        var balanceStr = '$' + stats.balance.toFixed(2);

        // Top bar
        var balVal = document.getElementById('balanceValue');
        var pnlVal = document.getElementById('pnlValue');
        var pnlBadge = document.getElementById('pnlBadge');
        if (balVal) balVal.textContent = balanceStr;
        if (pnlVal) pnlVal.textContent = '$' + stats.realized_pnl.toFixed(2);
        if (pnlBadge) {
            pnlBadge.classList.remove('pnl-positive', 'pnl-negative');
            pnlBadge.classList.add(stats.realized_pnl >= 0 ? 'pnl-positive' : 'pnl-negative');
        }

        // Stat cards
        var statBalance = document.getElementById('statBalance');
        var statPnl = document.getElementById('statPnl');
        var statWinRate = document.getElementById('statWinRate');
        var statPositions = document.getElementById('statPositions');

        if (statBalance) statBalance.textContent = balanceStr;
        if (statPnl) {
            statPnl.textContent = '$' + stats.realized_pnl.toFixed(2);
            statPnl.className = 'stat-value ' + (stats.realized_pnl >= 0 ? 'stat-positive' : 'stat-negative');
        }
        if (statWinRate) statWinRate.textContent = stats.win_rate.toFixed(0) + '%';
        if (statPositions) statPositions.textContent = stats.open_positions;
    }


    // ── Trade Execution ──────────────────────────────────────

    function executeTrade(index) {
        var s = signals[index];
        if (!s) { alert('No signal data. Re-scan first.'); return; }

        var actions = document.getElementById('signal-actions-' + index);
        actions.innerHTML = '<span class="trade-result" style="color:var(--gold);">Placing order...</span>';

        // Map direction back to side for the API
        var side = s.direction === 'LONG' ? 'yes' : 'no';

        fetch('/api/trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticker: s.id,
                side: side,
                price: s.entry_price,
                count: s.size
            })
        })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
            if (data.success) {
                actions.innerHTML =
                    '<span class="trade-result trade-success">' +
                        'Order placed! ' + data.order_id.slice(0, 8) + '...' +
                    '</span>';
                // Refresh page after short delay to update trade history
                setTimeout(function() { location.reload(); }, 2500);
            } else {
                actions.innerHTML =
                    '<span class="trade-result trade-fail">Failed: ' + data.error + '</span>';
            }
        })
        .catch(function(e) {
            actions.innerHTML =
                '<span class="trade-result trade-fail">Error: ' + e.message + '</span>';
        });
    }

    function skipSignal(index) {
        var card = document.getElementById('signal-' + index);
        if (card) {
            card.style.opacity = '0.3';
            card.style.pointerEvents = 'none';
        }
        var actions = document.getElementById('signal-actions-' + index);
        if (actions) {
            actions.innerHTML = '<span style="color:var(--text-dim);font-size:0.8rem;">Skipped</span>';
        }
    }


    // ── Auto-Scan ────────────────────────────────────────────

    function toggleAutoScan() {
        if (autoScanTimer) {
            // Turn OFF
            clearInterval(autoScanTimer);
            clearInterval(countdownTimer);
            autoScanTimer = null;
            countdownTimer = null;
            autoScanBtn.classList.remove('active');
            autoScanBtn.textContent = 'Auto: OFF';
        } else {
            // Turn ON
            autoScanBtn.classList.add('active');
            autoScanBtn.textContent = 'Auto: ON (' + AUTO_SCAN_INTERVAL + 's)';
            runScan(true);
            countdownLeft = AUTO_SCAN_INTERVAL;

            autoScanTimer = setInterval(function() {
                runScan(true);
                countdownLeft = AUTO_SCAN_INTERVAL;
            }, AUTO_SCAN_INTERVAL * 1000);

            countdownTimer = setInterval(function() {
                countdownLeft--;
                if (countdownLeft > 0 && !isScanning) {
                    autoScanBtn.textContent = 'Auto: ON (' + countdownLeft + 's)';
                }
            }, 1000);
        }
    }


    // ── Audio Alert ──────────────────────────────────────────

    function playAlert() {
        try {
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            // First beep — gold tone
            var osc1 = ctx.createOscillator();
            var gain1 = ctx.createGain();
            osc1.connect(gain1);
            gain1.connect(ctx.destination);
            osc1.frequency.value = 880;
            gain1.gain.value = 0.25;
            osc1.start();
            osc1.stop(ctx.currentTime + 0.12);

            // Second beep — higher
            setTimeout(function() {
                var osc2 = ctx.createOscillator();
                var gain2 = ctx.createGain();
                osc2.connect(gain2);
                gain2.connect(ctx.destination);
                osc2.frequency.value = 1100;
                gain2.gain.value = 0.25;
                osc2.start();
                osc2.stop(ctx.currentTime + 0.12);
            }, 150);

            // Third beep — resolve chord
            setTimeout(function() {
                var osc3 = ctx.createOscillator();
                var gain3 = ctx.createGain();
                osc3.connect(gain3);
                gain3.connect(ctx.destination);
                osc3.frequency.value = 1320;
                gain3.gain.value = 0.2;
                osc3.start();
                osc3.stop(ctx.currentTime + 0.18);
            }, 300);
        } catch(e) {
            // Audio not available — fail silently
        }
    }


    // ── Auto Trade ───────────────────────────────────────────

    function toggleAutoTrade() {
        if (autoTradeTimer) {
            // Turn OFF
            clearInterval(autoTradeTimer);
            clearInterval(autoTradeCountdownTimer);
            autoTradeTimer = null;
            autoTradeCountdownTimer = null;
            autoTradeBtn.classList.remove('active');
            autoTradeBtn.textContent = '\u25b3 AutoTrade: OFF';
        } else {
            // Turn ON
            autoTradeBtn.classList.add('active');
            autoTradeSection.style.display = '';
            autoTradeCountdown = AUTO_TRADE_INTERVAL;
            autoTradeBtn.textContent = '\u25b3 AutoTrade: ON (' + AUTO_TRADE_INTERVAL + 's)';
            runAutoTrade();

            autoTradeTimer = setInterval(function() {
                runAutoTrade();
                autoTradeCountdown = AUTO_TRADE_INTERVAL;
            }, AUTO_TRADE_INTERVAL * 1000);

            autoTradeCountdownTimer = setInterval(function() {
                autoTradeCountdown--;
                if (autoTradeCountdown > 0 && !isAutoTrading) {
                    autoTradeBtn.textContent = '\u25b3 AutoTrade: ON (' + autoTradeCountdown + 's)';
                }
            }, 1000);
        }
    }

    function runAutoTrade() {
        if (isAutoTrading) return;
        isAutoTrading = true;
        autoTradeBtn.textContent = '\u25b3 AutoTrade: SCANNING...';

        fetch('/api/autotrade?min_stars=' + AUTO_TRADE_MIN_STARS)
            .then(function(resp) { return resp.json(); })
            .then(function(data) {
                if (data.error) {
                    appendTradeLog('error', null, 'Scan failed: ' + data.error);
                    return;
                }
                // Update badge
                if (autoTradeBadge) {
                    autoTradeBadge.textContent = 'Scanned ' + data.scanned + ' signals, ' + data.eligible + ' eligible';
                }
                // Log each executed trade
                if (data.traded && data.traded.length > 0) {
                    for (var i = 0; i < data.traded.length; i++) {
                        appendTradeLog('executed', data.traded[i], null);
                    }
                    playAlert();
                } else {
                    appendTradeLog('idle', null, 'No ' + AUTO_TRADE_MIN_STARS + '\u2605+ signals found (' + data.scanned + ' scanned)');
                }
                // Log any errors
                if (data.errors && data.errors.length > 0) {
                    for (var j = 0; j < data.errors.length; j++) {
                        appendTradeLog('error', null, data.errors[j].ticker + ': ' + data.errors[j].error);
                    }
                }
            })
            .catch(function(e) {
                appendTradeLog('error', null, 'Request failed: ' + e.message);
            })
            .finally(function() {
                isAutoTrading = false;
                if (autoTradeTimer) {
                    autoTradeBtn.textContent = '\u25b3 AutoTrade: ON (' + autoTradeCountdown + 's)';
                }
            });
    }

    function appendTradeLog(type, trade, message) {
        var now = new Date().toLocaleTimeString();
        var row = document.createElement('div');

        if (type === 'executed' && trade) {
            var sideLabel = trade.side === 'yes' ? 'LONG' : 'SHORT';
            var sideClass = trade.side === 'yes' ? 'side-long' : 'side-short';
            row.className = 'autotrade-row autotrade-executed';
            row.innerHTML =
                '<span class="atlog-time">' + now + '</span>' +
                '<span class="atlog-status executed">EXECUTED</span>' +
                '<span class="atlog-ticker">' + trade.ticker + '</span>' +
                '<span class="atlog-side ' + sideClass + '">' + sideLabel + '</span>' +
                '<span class="atlog-price">$' + trade.price.toFixed(2) + ' \u00d7 ' + trade.count + '</span>' +
                '<span class="atlog-order">' + (trade.order_id ? trade.order_id.slice(0, 8) + '...' : '') + '</span>';
        } else if (type === 'error') {
            row.className = 'autotrade-row autotrade-error';
            row.innerHTML =
                '<span class="atlog-time">' + now + '</span>' +
                '<span class="atlog-status error">ERROR</span>' +
                '<span class="atlog-msg">' + (message || '') + '</span>';
        } else {
            row.className = 'autotrade-row autotrade-idle';
            row.innerHTML =
                '<span class="atlog-time">' + now + '</span>' +
                '<span class="atlog-status idle">IDLE</span>' +
                '<span class="atlog-msg">' + (message || '') + '</span>';
        }

        // Newest at top
        if (autoTradeLog.firstChild) {
            autoTradeLog.insertBefore(row, autoTradeLog.firstChild);
        } else {
            autoTradeLog.appendChild(row);
        }

        // Cap log at 50 rows
        while (autoTradeLog.children.length > 50) {
            autoTradeLog.removeChild(autoTradeLog.lastChild);
        }
    }


    // ── Chart ────────────────────────────────────────────────

    function initChart() {
        try {
            var data = window.__chartData;
            if (!data || !data.labels || data.labels.length === 0) {
                var container = document.querySelector('.chart-container');
                if (container) {
                    container.innerHTML = '<p style="color:var(--text-dim);text-align:center;padding:100px 0;">Chart appears after your first trade</p>';
                }
                return;
            }

            var ctx = document.getElementById('pnlChart');
            if (!ctx || typeof Chart === 'undefined') return;

            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.labels.map(function(l) { return l.slice(11) || l; }),
                    datasets: [
                        {
                            label: 'Cumulative PnL ($)',
                            data: data.cumPnl,
                            type: 'line',
                            borderColor: '#d4af37',
                            backgroundColor: 'rgba(212, 175, 55, 0.1)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 3,
                            pointBackgroundColor: '#d4af37',
                            yAxisID: 'y',
                            order: 0
                        },
                        {
                            label: 'Trade PnL ($)',
                            data: data.tradePnl,
                            backgroundColor: data.tradePnl.map(function(v) {
                                return v >= 0 ? 'rgba(0, 255, 136, 0.5)' : 'rgba(255, 68, 68, 0.5)';
                            }),
                            borderColor: data.tradePnl.map(function(v) {
                                return v >= 0 ? '#00ff88' : '#ff4444';
                            }),
                            borderWidth: 1,
                            yAxisID: 'y',
                            order: 1
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            labels: { color: '#888' }
                        }
                    },
                    scales: {
                        x: {
                            ticks: { color: '#555' },
                            grid: { color: 'rgba(255, 255, 255, 0.03)' }
                        },
                        y: {
                            ticks: {
                                color: '#888',
                                callback: function(v) { return '$' + v.toFixed(2); }
                            },
                            grid: { color: 'rgba(255, 255, 255, 0.05)' }
                        }
                    }
                }
            });
        } catch(e) {
            console.warn('Chart init error:', e);
        }
    }

})();
