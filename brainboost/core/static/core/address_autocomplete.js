let addressAutocompleteInitialized = false;

async function initAddressAutocomplete() {
    if (addressAutocompleteInitialized) {
        return;
    }
    addressAutocompleteInitialized = true;

    const inputs = document.querySelectorAll(".address-autocomplete");
    if (!inputs.length || !window.google || !google.maps || !google.maps.importLibrary) {
        return;
    }

    await google.maps.importLibrary("places");

    inputs.forEach((input) => {
        const originalInput = input;
        const initialValue = originalInput.value;
        const form = originalInput.form;
        const deferredMode = originalInput.dataset.addressMode === "deferred";
        const wrapper = document.createElement("div");
        wrapper.className = "address-autocomplete-widget";

        const placeAutocomplete = new google.maps.places.PlaceAutocompleteElement();
        placeAutocomplete.placeholder = originalInput.getAttribute("placeholder") || "Wohnadresse eingeben";
        placeAutocomplete.includedRegionCodes = ["de"];

        let editorContainer = wrapper;
        if (deferredMode) {
            wrapper.classList.add("address-autocomplete-widget--deferred");

            const displayRow = document.createElement("div");
            displayRow.className = "address-display-row";

            const displayValue = document.createElement("div");
            displayValue.className = "address-display-value";
            displayValue.textContent = initialValue || "Keine Adresse hinterlegt";

            const editButton = document.createElement("button");
            editButton.type = "button";
            editButton.className = "address-edit-button";
            editButton.setAttribute("aria-label", "Adresse bearbeiten");
            editButton.textContent = "✎";

            editorContainer = document.createElement("div");
            editorContainer.className = "address-editor";
            editorContainer.hidden = true;

            editButton.addEventListener("click", () => {
                displayRow.hidden = true;
                editorContainer.hidden = false;
            });

            wrapper.appendChild(displayRow);
            wrapper.appendChild(editorContainer);
            displayRow.appendChild(displayValue);
            displayRow.appendChild(editButton);
        }

        editorContainer.appendChild(placeAutocomplete);
        originalInput.parentNode.insertBefore(wrapper, originalInput);
        if (initialValue) {
            placeAutocomplete.value = initialValue;
        }
        originalInput.style.display = "none";

        const syncTypedValue = () => {
            if (typeof placeAutocomplete.value === "string") {
                originalInput.value = placeAutocomplete.value;
            }
        };

        placeAutocomplete.addEventListener("input", syncTypedValue);
        placeAutocomplete.addEventListener("change", syncTypedValue);
        placeAutocomplete.addEventListener("gmp-select", async (event) => {
            const placePrediction = event.placePrediction;
            if (!placePrediction) {
                return;
            }
            const place = placePrediction.toPlace();
            await place.fetchFields({ fields: ["formattedAddress"] });
            if (place.formattedAddress) {
                originalInput.value = place.formattedAddress;
            }
        });

        if (form) {
            form.addEventListener("submit", syncTypedValue);
        }
    });
}

window.initAddressAutocomplete = initAddressAutocomplete;
