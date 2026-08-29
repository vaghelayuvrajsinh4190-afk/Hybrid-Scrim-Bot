/**
 * Scrim Bot Dashboard — Client-side interactivity
 */

document.addEventListener('DOMContentLoaded', () => {
    // ── Confirmation dialogs for destructive actions ──────────────────
    document.querySelectorAll('.confirm-form').forEach(form => {
        form.addEventListener('submit', e => {
            const input = form.querySelector('input[name="confirmation"]');
            if (!input || !input.value.trim()) {
                e.preventDefault();
                input.classList.add('shake');
                input.focus();
                setTimeout(() => input.classList.remove('shake'), 500);
                return;
            }
        });
    });

    // ── Auto-dismiss alerts after 5 seconds ──────────────────────────
    document.querySelectorAll('.alert').forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 400ms ease, transform 400ms ease';
            alert.style.opacity = '0';
            alert.style.transform = 'translateY(-10px)';
            setTimeout(() => alert.remove(), 400);
        }, 5000);
    });

    // ── Live search filtering for tables ─────────────────────────────
    const searchInput = document.querySelector('input[name="search"]');
    if (searchInput) {
        let debounceTimer;
        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                const query = searchInput.value.toLowerCase();
                const table = document.getElementById('teams-table');
                if (!table) return;

                table.querySelectorAll('tbody tr').forEach(row => {
                    const text = row.textContent.toLowerCase();
                    row.style.display = text.includes(query) ? '' : 'none';
                });
            }, 200);
        });
    }

    // ── Mobile sidebar toggle ────────────────────────────────────────
    const sidebar = document.querySelector('.sidebar');
    if (sidebar && window.innerWidth <= 768) {
        // Create toggle button
        const toggle = document.createElement('button');
        toggle.className = 'sidebar-toggle';
        toggle.innerHTML = '☰';
        toggle.style.cssText = `
            position: fixed; top: 16px; left: 16px; z-index: 200;
            background: rgba(99, 102, 241, 0.2); border: 1px solid rgba(99, 102, 241, 0.3);
            color: #818cf8; font-size: 1.25rem; padding: 8px 12px;
            border-radius: 8px; cursor: pointer;
        `;
        document.body.appendChild(toggle);

        toggle.addEventListener('click', () => {
            sidebar.classList.toggle('open');
        });

        // Close sidebar when clicking outside
        document.addEventListener('click', e => {
            if (!sidebar.contains(e.target) && !toggle.contains(e.target)) {
                sidebar.classList.remove('open');
            }
        });
    }

    // ── Animate stat values on load ──────────────────────────────────
    document.querySelectorAll('.stat-value').forEach(el => {
        const target = parseInt(el.textContent, 10);
        if (isNaN(target) || target === 0) return;

        el.textContent = '0';
        const duration = 800;
        const start = performance.now();

        function tick(now) {
            const elapsed = now - start;
            const progress = Math.min(elapsed / duration, 1);
            // Ease out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            el.textContent = Math.floor(target * eased);
            if (progress < 1) requestAnimationFrame(tick);
            else el.textContent = target;
        }

        requestAnimationFrame(tick);
    });
});

// ── Shake animation (inline CSS) ─────────────────────────────────────
const shakeStyle = document.createElement('style');
shakeStyle.textContent = `
    @keyframes shake {
        0%, 100% { transform: translateX(0); }
        20% { transform: translateX(-4px); }
        40% { transform: translateX(4px); }
        60% { transform: translateX(-4px); }
        80% { transform: translateX(4px); }
    }
    .shake { animation: shake 0.3s ease; border-color: #ef4444 !important; }
`;
document.head.appendChild(shakeStyle);
