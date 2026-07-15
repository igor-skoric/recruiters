(function () {
    const storageKey = "sidebar-collapsed";
    const shell = document.querySelector(".app-shell");
    const toggle = document.querySelector(".sidebar-toggle");

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
        const firstField = modal.querySelector("input, select, textarea");
        if (firstField) {
            firstField.focus();
        }
    }

    function closeModal(modal) {
        if (modal && typeof modal.close === "function") {
            modal.close();
        }
    }

    document.querySelectorAll("[data-modal-open]").forEach(function (button) {
        button.addEventListener("click", function () {
            openModal(button.getAttribute("data-modal-open"));
        });
    });

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

    // Styled tooltips for timeline cells (visual only)
    let tipEl = null;
    function ensureTip() {
        if (!tipEl) {
            tipEl = document.createElement("div");
            tipEl.className = "app-tooltip";
            document.body.appendChild(tipEl);
        }
        return tipEl;
    }

    document.querySelectorAll(".week-dot[title], .week-cell[title]").forEach(function (el) {
        const text = el.getAttribute("title");
        if (!text) {
            return;
        }
        el.setAttribute("data-tip", text);
        el.removeAttribute("title");

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
})();
