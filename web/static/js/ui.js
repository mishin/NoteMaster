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

    /* ── Attachment field: picker + removable chips ────────
       Поле «Вложения» не заполняется руками: кнопка открывает диалог браузера,
       выбранные файлы становятся чипами, а их имена пишутся в скрытое поле
       списком через запятую — ровно то, что потом кладётся в JSON ядром. */
    document.querySelectorAll('.file-picker').forEach(initAttachments);

    function initAttachments(picker) {
        var form = picker.closest('form');
        if (!form) return;
        var hidden = form.querySelector('input[name="attachments"]');
        var list = form.querySelector('[data-existing]');
        var input = picker.querySelector('.file-picker__input');
        var button = picker.querySelector('.file-picker__button');
        var info = picker.querySelector('.file-picker__info');
        if (!hidden || !list || !input) return;

        var picked = []; // File-объекты: нужны для предпросмотра и синхронизации input

        function chips() {
            return Array.prototype.slice.call(list.querySelectorAll('.attachment-chip'));
        }

        function findChip(name) {
            return chips().filter(function (chip) {
                return chip.getAttribute('data-name') === name;
            })[0];
        }

        function syncHidden() {
            var names = [];
            chips().forEach(function (chip) {
                var name = chip.getAttribute('data-name');
                if (name && names.indexOf(name) === -1) names.push(name);
            });
            hidden.value = names.join(',');
            if (info) {
                info.textContent = names.length
                    ? names.length + ' вложение' + plural(names.length) + ' в списке'
                    : 'Файлы не выбраны';
            }
            list.toggleAttribute('hidden', names.length === 0);
        }

        /* FileList неизменяемый: перечитываем его из оставшихся файлов,
           иначе убраннный чип всё равно ушёл бы на сервер и вернулся. */
        function syncInputFiles() {
            if (typeof DataTransfer === 'undefined') return;
            try {
                var transfer = new DataTransfer();
                picked.forEach(function (file) { transfer.items.add(file); });
                input.files = transfer.files;
            } catch (err) {
                /* Браузер не даёт подменить список: файл просто уйдёт на сервер. */
            }
        }

        function makeChip(name, isNew, file) {
            var chip = document.createElement('span');
            chip.className = 'attachment-chip' + (isNew ? ' attachment-chip--new' : '');
            chip.setAttribute('data-name', name);

            var icon = document.createElement('span');
            icon.className = 'attachment-chip__icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = iconFor(name);

            var label = document.createElement('span');
            label.className = 'attachment-chip__name';
            label.textContent = name;
            if (isNew && file) {
                label.classList.add('attachment-chip__name--preview');
                label.title = 'Открыть предпросмотр этого файла';
                label.addEventListener('click', function () {
                    window.open(URL.createObjectURL(file), '_blank', 'noopener');
                });
            }

            var remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'attachment-chip__remove';
            remove.setAttribute('aria-label', 'Убрать вложение ' + name);
            remove.innerHTML = '&times;';

            chip.appendChild(icon);
            chip.appendChild(label);
            chip.appendChild(remove);
            return chip;
        }

        if (button) {
            button.addEventListener('click', function () { input.click(); });
        }

        input.addEventListener('change', function () {
            Array.prototype.forEach.call(input.files, function (file) {
                if (findChip(file.name)) return;
                picked.push(file);
                list.appendChild(makeChip(file.name, true, file));
            });
            syncHidden();
        });

        list.addEventListener('click', function (event) {
            var remove = event.target.closest('.attachment-chip__remove');
            if (!remove) return;
            var chip = remove.closest('.attachment-chip');
            var name = chip.getAttribute('data-name');
            chip.remove();
            picked = picked.filter(function (file) { return file.name !== name; });
            syncInputFiles();
            syncHidden();
        });

        syncHidden();
    }

    function iconFor(name) {
        var ext = (name.split('.').pop() || '').toLowerCase();
        if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif'].indexOf(ext) !== -1) return '🖼';
        if (ext === 'pdf') return '📕';
        if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'].indexOf(ext) !== -1) return '🗜';
        if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a'].indexOf(ext) !== -1) return '🎵';
        if (['mp4', 'webm', 'mov', 'avi', 'mkv', 'm4v'].indexOf(ext) !== -1) return '🎬';
        if (['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'rtf', 'epub'].indexOf(ext) !== -1) return '📘';
        if (['txt', 'md', 'log', 'csv', 'json', 'yaml', 'yml', 'html', 'css', 'js', 'py'].indexOf(ext) !== -1) return '📄';
        return '📎';
    }

    function plural(count) {
        if (count === 1) return '';
        if (count >= 2 && count <= 4) return 'ия';
        return 'ий';
    }

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
