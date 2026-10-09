/* Google Places suggestions are advisory; manual input remains available. */
(function (root) {
    "use strict";
    const validPostalCode = value => /^[0-9]{5}$/.test(value);
    const component = (place, type, short = false) => {
        const part = (place.addressComponents || []).find(item => (item.types || []).includes(type));
        return part ? (short ? part.shortText : part.longText) || "" : "";
    };
    function verifiedAddress(place, kind, query, postalCode) {
        const code = component(place, "postal_code");
        if (component(place, "country", true) !== "DE" || !validPostalCode(code)) return null;
        const city = component(place, "locality") || component(place, "postal_town");
        if (kind === "postal_code") {
            if (!code.startsWith(query)) return null;
            return { value: code, label: `${code}${city ? " · " + city : ""}` };
        }
        const street = component(place, "route");
        // Never infer the postal code from free text or a viewport. Roads can cross postal boundaries.
        if (code !== postalCode || !street) return null;
        return { value: street, label: `${street} · ${code}${city ? " " + city : ""}` };
    }
    function withTimeout(promise, milliseconds = 8000) {
        let timer;
        return Promise.race([
            promise,
            new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error("places_timeout")), milliseconds); }),
        ]).finally(() => clearTimeout(timer));
    }
    async function lookupSuggestions(api, kind, query, postalCode, isCurrent = () => true) {
        if (kind === "postal_code" ? !/^[0-9]{2,5}$/.test(query) : query.length < 2 || !validPostalCode(postalCode)) return [];
        const result = await withTimeout(api.AutocompleteSuggestion.fetchAutocompleteSuggestions({
            input: kind === "postal_code" ? query : `${query}, ${postalCode}, Deutschland`,
            includedRegionCodes: ["de"],
            includedPrimaryTypes: kind === "postal_code" ? ["postal_code"] : ["route", "street_address", "premise"],
            language: "de",
            region: "de",
            // No session token: every prediction is independently checked before display.
            // Details calls here are not a user-selected session termination.
        }));
        if (!isCurrent()) return [];
        const predictions = (result.suggestions || []).slice(0, 5).map(item => item.placePrediction).filter(Boolean);
        const resolved = await Promise.allSettled(predictions.map(async prediction => {
            const place = prediction.toPlace();
            await withTimeout(place.fetchFields({ fields: ["addressComponents"] }));
            return verifiedAddress(place, kind, query, postalCode);
        }));
        if (!isCurrent()) return [];
        if (resolved.length && resolved.every(item => item.status === "rejected")) throw new Error("places_details_unavailable");
        const unique = new Map();
        for (const item of resolved) {
            if (item.status === "fulfilled" && item.value) {
                const key = item.value.value.toLocaleLowerCase("de");
                if (!unique.has(key)) unique.set(key, item.value);
            }
        }
        return Array.from(unique.values());
    }
    function makeCombobox(input, list, status, search, selected) {
        let revision = 0;
        let timer;
        let options = [];
        let active = -1;
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-controls", list.id);
        input.setAttribute("aria-describedby", status.id);
        input.setAttribute("aria-expanded", "false");
        input.setAttribute("autocomplete", "off");
        function invalidate() {
            revision += 1;
            clearTimeout(timer);
            options = [];
            active = -1;
            list.hidden = true;
            list.replaceChildren();
            input.setAttribute("aria-expanded", "false");
            input.removeAttribute("aria-activedescendant");
        }
        function choose(index) {
            const option = options[index];
            if (!option || input.disabled) return;
            input.value = option.value;
            invalidate();
            status.textContent = "";
            selected();
            input.dispatchEvent(new Event("change", { bubbles: true }));
        }
        function highlight(index) {
            active = index;
            Array.from(list.querySelectorAll('[role="option"]')).forEach((node, i) => node.setAttribute("aria-selected", String(i === index)));
            if (index >= 0) input.setAttribute("aria-activedescendant", `${list.id}_${index}`);
        }
        async function load(current) {
            const isCurrent = () => current === revision && !input.disabled && document.activeElement === input;
            try {
                const found = await search(input.value.trim(), isCurrent);
                if (!isCurrent()) return;
                options = found;
                list.replaceChildren();
                options.forEach((item, index) => {
                    const node = document.createElement("div");
                    node.className = "lead-address-option";
                    node.id = `${list.id}_${index}`;
                    node.setAttribute("role", "option");
                    node.setAttribute("aria-selected", "false");
                    node.textContent = item.label;
                    // Keep the combobox focused for mouse and touch selection.
                    node.addEventListener("pointerdown", event => event.preventDefault());
                    node.addEventListener("click", () => choose(index));
                    list.appendChild(node);
                });
                if (options.length) {
                    const attribution = document.createElement("div");
                    attribution.className = "lead-address-attribution";
                    attribution.setAttribute("translate", "no");
                    attribution.setAttribute("role", "presentation");
                    attribution.textContent = "Google Maps";
                    list.appendChild(attribution);
                    list.hidden = false;
                    input.setAttribute("aria-expanded", "true");
                    status.textContent = `${options.length} Vorschläge verfügbar. Mit Pfeiltasten auswählen und mit Enter übernehmen.`;
                }
            } catch (error) {
                if (!isCurrent()) return;
                invalidate();
                status.textContent = "Adressvorschläge sind gerade nicht verfügbar. Bitte manuell eingeben.";
            }
        }
        function schedule() {
            invalidate();
            const current = revision;
            timer = setTimeout(() => load(current), 350);
        }
        input.addEventListener("input", schedule);
        input.addEventListener("focus", schedule);
        input.addEventListener("blur", invalidate);
        input.addEventListener("keydown", event => {
            if (event.key === "Escape") { invalidate(); return; }
            if (list.hidden || !options.length) return;
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const next = active < 0 ? (event.key === "ArrowDown" ? 0 : options.length - 1)
                    : (active + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length;
                highlight(next);
            } else if (event.key === "Enter") {
                // Enter with an open list must never accidentally submit the contact form.
                event.preventDefault();
                if (active >= 0) choose(active);
            }
        });
        return { invalidate };
    }
    function mount(form) {
        const postal = form.querySelector('[name="postal_code"]');
        const street = form.querySelector('[name="street"]');
        if (!postal || !street) return;
        let api;
        let failed = false;
        let oldPostal = postal.value.trim();
        const statuses = {};
        const boxes = {};
        for (const kind of ["postal_code", "street"]) {
            const input = kind === "postal_code" ? postal : street;
            const list = document.getElementById(`${kind}_suggestions`);
            const status = document.getElementById(`${kind}_status`);
            statuses[kind] = status;
            boxes[kind] = makeCombobox(input, list, status, async (query, isCurrent) => {
                if (kind === "street" && !validPostalCode(postal.value.trim())) {
                    status.textContent = "Bitte zuerst eine fünfstellige PLZ eingeben oder auswählen.";
                    return [];
                }
                if (query.length < 2) { status.textContent = ""; return []; }
                if (!api) {
                    status.textContent = failed ? "Adressvorschläge sind gerade nicht verfügbar. Bitte manuell eingeben." : "Adressvorschläge werden geladen …";
                    return [];
                }
                if (kind === "postal_code" && !/^[0-9]{2,5}$/.test(query)) {
                    status.textContent = "Bitte eine fünfstellige PLZ eingeben.";
                    return [];
                }
                status.textContent = "Vorschläge werden gesucht …";
                const code = postal.value.trim();
                const stillCurrent = () => isCurrent() && postal.value.trim() === code;
                const results = await lookupSuggestions(api, kind, query, code, stillCurrent);
                if (!stillCurrent()) return [];
                if (stillCurrent()) status.textContent = results.length ? "" : kind === "street"
                    ? "Keine eindeutig passenden Straßen für diese PLZ gefunden. Bitte manuell eingeben."
                    : "Keine passende PLZ gefunden. Bitte manuell eingeben.";
                return results;
            }, kind === "postal_code" ? postalChanged : () => {});
        }
        function postalChanged() {
            const code = postal.value.trim();
            if (code === oldPostal) return;
            oldPostal = code;
            boxes.street.invalidate();
            const hadStreet = Boolean(street.value);
            street.value = "";
            statuses.street.textContent = hadStreet
                ? "Die PLZ wurde geändert. Bitte die Straße für die neue PLZ erneut eingeben."
                : "";
        }
        postal.addEventListener("input", postalChanged);
        postal.addEventListener("change", postalChanged);
        form.querySelectorAll('[name="role"]').forEach(input => input.addEventListener("change", () => {
            boxes.postal_code.invalidate();
            boxes.street.invalidate();
        }));
        const timeout = setTimeout(unavailable, 10000);
        function unavailable() {
            clearTimeout(timeout);
            failed = true;
            api = undefined;
            for (const kind of Object.keys(boxes)) {
                boxes[kind].invalidate();
                statuses[kind].textContent = "Adressvorschläge sind gerade nicht verfügbar. Bitte manuell eingeben.";
            }
        }
        root.leadAddressAutocompleteUnavailable = unavailable;
        root.initLeadAddressAutocomplete = async function () {
            try {
                api = await withTimeout(root.google.maps.importLibrary("places"));
                clearTimeout(timeout);
                failed = false;
                for (const status of Object.values(statuses)) status.textContent = "";
                const focused = document.activeElement;
                if (focused === postal || focused === street) focused.dispatchEvent(new Event("input", { bubbles: true }));
            } catch (error) { unavailable(); }
        };
        root.addEventListener("pageshow", () => {
            postalChanged();
            boxes.postal_code.invalidate();
            boxes.street.invalidate();
        });
    }
    if (typeof module !== "undefined" && module.exports) module.exports = { verifiedAddress, lookupSuggestions, withTimeout, makeCombobox, mount };
    if (typeof document !== "undefined") {
        const form = document.querySelector("[data-lead-form]");
        if (form) mount(form);
    }
})(typeof window !== "undefined" ? window : globalThis);
