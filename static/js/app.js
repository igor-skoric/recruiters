(function () {
    const storageKey = "sidebar-collapsed";
    const shell = document.querySelector(".app-shell");
    const toggle = document.querySelector(".sidebar-toggle");
    const pageLoader = document.getElementById("page-loader");
    let loaderTimer = null;
    let loaderVisible = false;

    function showPageLoader() {
        if (!pageLoader || loaderVisible) {
            return;
        }
        loaderVisible = true;
        pageLoader.classList.add("is-active");
        pageLoader.setAttribute("aria-busy", "true");
        pageLoader.setAttribute("aria-hidden", "false");
    }

    function hidePageLoader() {
        if (!pageLoader) {
            return;
        }
        if (loaderTimer) {
            clearTimeout(loaderTimer);
            loaderTimer = null;
        }
        loaderVisible = false;
        pageLoader.classList.remove("is-active");
        pageLoader.setAttribute("aria-busy", "false");
        pageLoader.setAttribute("aria-hidden", "true");
    }

    function schedulePageLoader() {
        if (loaderTimer || loaderVisible) {
            return;
        }
        // Avoid flash on fast navigations.
        loaderTimer = setTimeout(function () {
            loaderTimer = null;
            showPageLoader();
        }, 120);
    }

    function shouldHandleNavigation(anchor) {
        if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) {
            return false;
        }
        const href = anchor.getAttribute("href");
        if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) {
            return false;
        }
        try {
            const url = new URL(href, window.location.href);
            if (url.origin !== window.location.origin) {
                return false;
            }
        } catch (err) {
            return false;
        }
        return true;
    }

    document.addEventListener("click", function (event) {
        if (event.defaultPrevented || event.button !== 0) {
            return;
        }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }
        const anchor = event.target.closest("a[href]");
        if (!shouldHandleNavigation(anchor)) {
            return;
        }
        const url = new URL(anchor.href, window.location.href);
        if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) {
            return;
        }
        schedulePageLoader();
    });

    document.addEventListener("submit", function (event) {
        const form = event.target;
        if (!form || form.tagName !== "FORM") {
            return;
        }
        if (form.target === "_blank" || form.hasAttribute("data-no-loader")) {
            return;
        }
        // Wait a tick so cancel/preventDefault (e.g. aborted submit) can win.
        window.setTimeout(function () {
            if (event.defaultPrevented) {
                hidePageLoader();
                return;
            }
            schedulePageLoader();
        }, 0);
    });

    window.addEventListener("pageshow", hidePageLoader);
    window.addEventListener("pagehide", function () {
        // Keep loader state if navigating away; pageshow clears on return.
    });
    document.addEventListener("DOMContentLoaded", hidePageLoader);
    hidePageLoader();

    if (shell && toggle) {
        function setCollapsed(collapsed) {
            shell.classList.toggle("sidebar-collapsed", collapsed);
            toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
            toggle.textContent = collapsed ? "›" : "‹";
            localStorage.setItem(storageKey, collapsed ? "1" : "0");
        }

        if (localStorage.getItem(storageKey) === "1") {
            setCollapsed(true);
        }

        toggle.addEventListener("click", function () {
            setCollapsed(!shell.classList.contains("sidebar-collapsed"));
        });
    }

    function getModal(name) {
        return document.getElementById("modal-" + name);
    }

    function openModal(name) {
        const modal = getModal(name);
        if (!modal || typeof modal.showModal !== "function") {
            return;
        }
        if (!modal.open) {
            modal.showModal();
        }
        const searchInput = modal.querySelector(".searchable-select-input");
        const firstField = searchInput || modal.querySelector("input, select, textarea");
        if (firstField) {
            firstField.focus();
        }
    }

    function closeModal(modal) {
        if (modal && typeof modal.close === "function") {
            modal.querySelectorAll(".searchable-select-input").forEach(function (input) {
                if (input.value) {
                    input.value = "";
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                }
            });
            modal.close();
        }
    }

    document.querySelectorAll("[data-modal-open]").forEach(function (button) {
        button.addEventListener("click", function () {
            const modalName = button.getAttribute("data-modal-open");
            if (button.hasAttribute("data-edit-action")) {
                fillEditForm(button);
            }
            if (button.hasAttribute("data-delete-action")) {
                fillDeleteConfirm(button);
            }
            openModal(modalName);
        });
    });

    function fillEditForm(button) {
        const modalName = button.getAttribute("data-modal-open");
        const modal = getModal(modalName);
        if (!modal) {
            return;
        }
        const form = modal.querySelector("form");
        if (!form) {
            return;
        }
        const action = button.getAttribute("data-edit-action");
        if (action) {
            form.setAttribute("action", action);
        }
        const prefix = button.getAttribute("data-edit-prefix") || "";
        Array.prototype.forEach.call(button.attributes, function (attr) {
            if (!attr.name.startsWith("data-field-")) {
                return;
            }
            const fieldName = attr.name.slice("data-field-".length).replace(/-/g, "_");
            const inputName = prefix ? prefix + "-" + fieldName : fieldName;
            const field = form.querySelector('[name="' + inputName + '"]');
            if (!field) {
                return;
            }
            field.value = attr.value;
        });
    }

    function fillDeleteConfirm(button) {
        const modal = getModal("delete-confirm");
        if (!modal) {
            return;
        }
        const form = modal.querySelector("[data-delete-confirm-form]");
        if (!form) {
            return;
        }
        const action = button.getAttribute("data-delete-action") || "#";
        form.setAttribute("action", action);
        const name = button.getAttribute("data-delete-name") || "this record";
        const kind = button.getAttribute("data-delete-kind") || "record";
        const nameEl = modal.querySelector("[data-delete-name]");
        const kindEl = modal.querySelector("[data-delete-kind-label]");
        if (nameEl) {
            nameEl.textContent = name;
        }
        if (kindEl) {
            kindEl.textContent = kind;
        }
    }

    document.querySelectorAll(".app-modal").forEach(function (modal) {
        modal.querySelectorAll("[data-modal-close]").forEach(function (button) {
            button.addEventListener("click", function () {
                closeModal(modal);
            });
        });

        modal.addEventListener("click", function (event) {
            if (event.target === modal) {
                closeModal(modal);
            }
        });

        if (modal.getAttribute("data-open-on-load") === "1") {
            openModal(modal.id.replace("modal-", ""));
        }
    });

    // Searchable selects (driver pickers in modals)
    function enhanceSearchableSelect(select) {
        if (!select || select.dataset.searchEnhanced === "1") {
            return;
        }
        select.dataset.searchEnhanced = "1";

        const wrap = document.createElement("div");
        wrap.className = "searchable-select";
        select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(select);

        const input = document.createElement("input");
        input.type = "search";
        input.className = "form-control searchable-select-input";
        input.placeholder = select.getAttribute("data-search-placeholder") || "Search…";
        input.setAttribute("autocomplete", "off");
        input.setAttribute("aria-label", select.getAttribute("data-search-placeholder") || "Search options");
        wrap.insertBefore(input, select);

        const empty = document.createElement("p");
        empty.className = "searchable-select-empty";
        empty.hidden = true;
        empty.textContent = "No drivers match.";
        wrap.appendChild(empty);

        const optionMeta = Array.from(select.options).map(function (opt) {
            return {
                el: opt,
                text: (opt.textContent || "").toLowerCase(),
                value: opt.value,
            };
        });

        function applyFilter() {
            const query = input.value.trim().toLowerCase();
            let visibleChoices = 0;
            optionMeta.forEach(function (opt) {
                if (!opt.value) {
                    opt.el.hidden = false;
                    return;
                }
                const match = !query || opt.text.indexOf(query) !== -1;
                opt.el.hidden = !match;
                if (match) {
                    visibleChoices += 1;
                }
            });

            empty.hidden = !(query && visibleChoices === 0);
            if (query) {
                select.size = Math.min(8, Math.max(4, visibleChoices || 1));
                select.classList.add("is-filtered");
            } else {
                select.size = 1;
                select.classList.remove("is-filtered");
            }
        }

        input.addEventListener("input", applyFilter);
        input.addEventListener("search", applyFilter);
        input.addEventListener("keydown", function (event) {
            if (event.key === "ArrowDown") {
                event.preventDefault();
                select.focus();
            }
        });
    }

    document.querySelectorAll("select[data-searchable-select]").forEach(enhanceSearchableSelect);

    // Week day preview on hover (fleet board + truck detail)
    let weekPreviewEl = null;
    function ensureWeekPreview() {
        if (!weekPreviewEl) {
            weekPreviewEl = document.createElement("div");
            weekPreviewEl.className = "week-day-preview";
            document.body.appendChild(weekPreviewEl);
        }
        return weekPreviewEl;
    }

    function hideWeekPreview() {
        if (weekPreviewEl) {
            weekPreviewEl.classList.remove("is-visible");
        }
    }

    function showWeekPreview(anchor) {
        let days;
        try {
            days = JSON.parse(anchor.getAttribute("data-days") || "[]");
        } catch (err) {
            return;
        }
        if (!days.length) {
            return;
        }

        const title = anchor.getAttribute("data-week-title") || "Week";
        const tip = anchor.getAttribute("data-tip") || anchor.getAttribute("title") || "";
        const preview = ensureWeekPreview();

        let html =
            '<div class="week-day-preview-head">' +
            '<strong></strong>' +
            '<span class="week-day-preview-legend">' +
            '<span><i class="dot-otr"></i> Busy</span>' +
            '<span><i class="dot-home"></i> Free</span>' +
            "</span></div>" +
            '<div class="week-day-preview-grid"></div>';
        if (tip) {
            html += '<p class="week-day-preview-tip"></p>';
        }
        preview.innerHTML = html;
        preview.querySelector("strong").textContent = title;

        const grid = preview.querySelector(".week-day-preview-grid");
        days.forEach(function (day) {
            const cell = document.createElement("div");
            cell.className = "week-day-cell day-" + (day.status || "home");
            cell.title = day.driver
                ? day.label + " " + day.date + " — " + day.driver
                : day.label + " " + day.date;
            cell.innerHTML =
                '<span class="week-day-name"></span>' +
                '<span class="week-day-date"></span>' +
                '<span class="week-day-swatch"></span>';
            cell.querySelector(".week-day-name").textContent = day.label;
            cell.querySelector(".week-day-date").textContent = day.date;
            grid.appendChild(cell);
        });

        if (tip) {
            preview.querySelector(".week-day-preview-tip").textContent = tip;
        }

        preview.classList.add("is-visible");
        const rect = anchor.getBoundingClientRect();
        const tipRect = preview.getBoundingClientRect();
        let left = rect.left + rect.width / 2 - tipRect.width / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
        let top = rect.top - tipRect.height - 10;
        if (top < 8) {
            top = rect.bottom + 10;
        }
        preview.style.left = left + "px";
        preview.style.top = top + "px";
    }

    // Styled tooltips for timeline cells without day preview
    let tipEl = null;
    function ensureTip() {
        if (!tipEl) {
            tipEl = document.createElement("div");
            tipEl.className = "app-tooltip";
            document.body.appendChild(tipEl);
        }
        return tipEl;
    }

    document.querySelectorAll(
        ".week-dot[title], .week-cell[title], .gantt-bar[title], .gantt-gap[title], .gantt-handoff[title], .gantt-status-seg[title]"
    ).forEach(function (el) {
        const text = el.getAttribute("title");
        if (!text) {
            return;
        }
        el.setAttribute("data-tip", text);
        el.removeAttribute("title");

        if (el.hasAttribute("data-week-preview")) {
            el.addEventListener("mouseenter", function () {
                showWeekPreview(el);
            });
            el.addEventListener("mouseleave", hideWeekPreview);
            return;
        }

        el.addEventListener("mouseenter", function () {
            const tip = ensureTip();
            tip.textContent = text;
            tip.classList.add("is-visible");
            const rect = el.getBoundingClientRect();
            const tipRect = tip.getBoundingClientRect();
            let left = rect.left + rect.width / 2 - tipRect.width / 2;
            left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
            tip.style.left = left + "px";
            tip.style.top = Math.max(8, rect.top - tipRect.height - 8) + "px";
        });

        el.addEventListener("mouseleave", function () {
            if (tipEl) {
                tipEl.classList.remove("is-visible");
            }
        });
    });

    // Cycle duration from Start Date + Home Time Date
    function formatCycleDuration(startISO, endISO) {
        if (!startISO || !endISO) {
            return "—";
        }
        const start = new Date(startISO + "T00:00:00");
        const end = new Date(endISO + "T00:00:00");
        const days = Math.round((end - start) / 86400000);
        if (days < 1) {
            return "—";
        }
        const weeks = Math.floor(days / 7);
        const rem = days % 7;
        const parts = [];
        if (weeks) {
            parts.push(weeks + (weeks === 1 ? " week" : " weeks"));
        }
        if (rem) {
            parts.push(rem + (rem === 1 ? " day" : " days"));
        }
        return parts.join(" ") || "0 days";
    }

    function bindCycleForms() {
        document.querySelectorAll("[data-cycle-form]").forEach(function (form) {
            const startInput = form.querySelector("[data-cycle-start]");
            const endInput = form.querySelector("[data-cycle-end]");
            const label = form.querySelector("[data-cycle-duration]");
            if (!endInput || !label) {
                return;
            }

            function refresh() {
                const startValue =
                    (startInput && startInput.value) ||
                    endInput.getAttribute("data-cycle-start-value") ||
                    "";
                const endValue = endInput.value || "";
                label.textContent = "Cycle length: " + formatCycleDuration(startValue, endValue);
            }

            if (startInput) {
                startInput.addEventListener("change", refresh);
                startInput.addEventListener("input", refresh);
            }
            endInput.addEventListener("change", refresh);
            endInput.addEventListener("input", refresh);
            refresh();
        });
    }

    bindCycleForms();

    // Toast notifications (top-right)
    const TOAST_MS = 4200;
    let toastRoot = document.getElementById("toast-root");
    if (!toastRoot) {
        toastRoot = document.createElement("div");
        toastRoot.id = "toast-root";
        toastRoot.className = "toast-root";
        toastRoot.setAttribute("aria-live", "polite");
        document.body.appendChild(toastRoot);
    }

    function normalizeToastLevel(level) {
        const value = (level || "info").toLowerCase();
        if (value.indexOf("success") !== -1) return "success";
        if (value.indexOf("error") !== -1 || value.indexOf("danger") !== -1) return "error";
        if (value.indexOf("warning") !== -1) return "warning";
        return "info";
    }

    function showToast(text, level) {
        if (!text) {
            return;
        }
        const toast = document.createElement("div");
        toast.className = "toast toast-" + normalizeToastLevel(level);
        toast.innerHTML =
            '<div class="toast-body"></div>' +
            '<button type="button" class="toast-close" aria-label="Close">×</button>';
        toast.querySelector(".toast-body").textContent = text;

        function dismiss() {
            toast.classList.remove("is-visible");
            toast.classList.add("is-leaving");
            setTimeout(function () {
                if (toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
            }, 200);
        }

        toast.querySelector(".toast-close").addEventListener("click", dismiss);
        toastRoot.appendChild(toast);
        requestAnimationFrame(function () {
            toast.classList.add("is-visible");
        });
        setTimeout(dismiss, TOAST_MS);
    }

    window.showToast = showToast;

    const toastTemplate = document.getElementById("django-toasts");
    if (toastTemplate && toastTemplate.content) {
        toastTemplate.content.querySelectorAll("[data-toast-level]").forEach(function (node) {
            showToast(node.textContent.trim(), node.getAttribute("data-toast-level"));
        });
    }

    // Fleet board live search (truck / current driver / next driver) — current page only
    const fleetSearch = document.querySelector("[data-fleet-search]");
    if (fleetSearch) {
        const rows = Array.from(document.querySelectorAll("[data-fleet-row]"));
        const emptyEl = document.querySelector("[data-fleet-search-empty]");
        const visibleCountEl = document.querySelector("[data-fleet-visible-count]");
        const scrollEl = document.querySelector(".fleet-scroll");
        const defaultVisibleLabel = visibleCountEl ? visibleCountEl.textContent : "";

        function applyFleetSearch() {
            const query = fleetSearch.value.trim().toLowerCase();
            let visible = 0;
            rows.forEach(function (row) {
                const haystack = (row.getAttribute("data-fleet-search-text") || "").toLowerCase();
                const match = !query || haystack.indexOf(query) !== -1;
                row.hidden = !match;
                if (match) {
                    visible += 1;
                }
            });
            if (visibleCountEl) {
                visibleCountEl.textContent = query ? String(visible) : defaultVisibleLabel;
            }
            if (emptyEl) {
                emptyEl.hidden = visible > 0 || rows.length === 0;
            }
            if (scrollEl) {
                scrollEl.hidden = visible === 0 && query !== "";
            }
        }

        fleetSearch.addEventListener("input", applyFleetSearch);
        fleetSearch.addEventListener("search", applyFleetSearch);
    }
})();
