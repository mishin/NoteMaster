/* ── UI helpers ──────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', function () {

    /* ── Flash auto-dismiss ──────────────────────────────── */
    var flashes = document.querySelectorAll('.flash');
    flashes.forEach(function (el) {
        var btn = el.querySelector('.flash__close');
        if (btn) {
            btn.addEventListener('click', function () { el.remove(); });
        }
        // Auto-hide after 6s
        setTimeout(function () {
            if (el.parentNode) el.remove();
        }, 6000);
    });

    /* ── Form toggle (add form) ──────────────────────────── */
    var addBtn = document.getElementById('add-btn');
    var addForm = document.getElementById('add-form');
    if (addBtn && addForm) {
        addBtn.addEventListener('click', function () {
            var open = !addForm.hasAttribute('hidden');
            if (open) {
                addForm.setAttribute('hidden', '');
                addForm.classList.remove('form-panel--open');
            } else {
                addForm.removeAttribute('hidden');
                addForm.classList.add('form-panel--open');
                addForm.querySelector('input[name="title"]').focus();
            }
        });
    }

    /* ── Form toggle (edit form on note page) ────────────── */
    document.querySelectorAll('[data-toggle-form]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var panelId = this.getAttribute('data-toggle-form');
            var panel = document.getElementById(panelId);
            if (!panel) return;
            var open = !panel.hasAttribute('hidden');
            if (open) {
                panel.setAttribute('hidden', '');
                panel.classList.remove('form-panel--open');
            } else {
                panel.removeAttribute('hidden');
                panel.classList.add('form-panel--open');
                panel.querySelector('input[name="title"]').focus();
            }
        });
    });

    /* ── Dismiss forms ───────────────────────────────────── */
    document.querySelectorAll('[data-dismiss-form]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var form = this.closest('.form-panel');
            if (!form) return;
            form.setAttribute('hidden', '');
            form.classList.remove('form-panel--open');
        });
    });

    /* ── Confirm delete / edit ───────────────────────────── */
    document.querySelectorAll('[data-confirm]').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            var msg = this.getAttribute('data-confirm');
            if (!window.confirm(msg)) {
                e.preventDefault();
            }
        });
    });

    /* ── File picker info ────────────────────────────────── */
    document.querySelectorAll('.file-picker__input').forEach(function (input) {
        var info = input.parentElement.querySelector('.file-picker__info');
        input.addEventListener('change', function () {
            var count = this.files.length;
            if (info) {
                info.textContent = count
                    ? Array.from(this.files).map(function (f) { return f.name; }).join(', ')
                    : 'Файлы не выбраны';
            }
        });
    });

    /* ── Scroll to form panel if hash matches ────────────── */
    if (location.hash === '#edit') {
        var editPanel = document.getElementById('edit-form');
        if (editPanel) {
            editPanel.removeAttribute('hidden');
            editPanel.classList.add('form-panel--open');
            setTimeout(function () {
                editPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 100);
        }
    }

    /* ── Keyboard: Escape closes open forms ──────────────── */
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.form-panel:not([hidden])').forEach(function (panel) {
            panel.setAttribute('hidden', '');
            panel.classList.remove('form-panel--open');
        });
    });

});
