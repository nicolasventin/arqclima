(() => {
    "use strict";

    const interactiveSelector = [
        "a",
        "button",
        "input",
        "select",
        "textarea",
        "label",
        "form",
        "summary",
        "[role='button']",
        "[role='menuitem']",
        "[contenteditable='true']",
        "[data-row-ignore]",
    ].join(",");

    const rowFromEvent = (event) => {
        const target = event.target instanceof Element ? event.target : null;
        return target ? target.closest("[data-row-href]") : null;
    };

    const clickedInteractiveControl = (event, row) => {
        const target = event.target instanceof Element ? event.target : null;
        if (!target) {
            return false;
        }
        const control = target.closest(interactiveSelector);
        return control !== null && row.contains(control) && control !== row;
    };

    const hasTextSelection = () => {
        const selection = window.getSelection();
        return selection !== null && selection.toString().trim().length > 0;
    };

    const navigate = (row, newTab = false) => {
        const href = row.dataset.rowHref;
        if (!href) {
            return;
        }

        if (newTab) {
            window.open(href, "_blank", "noopener");
            return;
        }

        window.location.assign(href);
    };

    document.addEventListener("click", (event) => {
        if (event.button !== 0) {
            return;
        }

        const row = rowFromEvent(event);
        if (!row || clickedInteractiveControl(event, row) || hasTextSelection()) {
            return;
        }

        navigate(row, event.ctrlKey || event.metaKey);
    });

    document.addEventListener("auxclick", (event) => {
        if (event.button !== 1) {
            return;
        }

        const row = rowFromEvent(event);
        if (!row || clickedInteractiveControl(event, row)) {
            return;
        }

        event.preventDefault();
        navigate(row, true);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
            return;
        }

        const row = rowFromEvent(event);
        if (!row || event.target !== row) {
            return;
        }

        event.preventDefault();
        navigate(row, event.ctrlKey || event.metaKey);
    });
})();
