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
        const adminMode = originalInput.dataset.addressMode === "admin";
        const deferredMode = originalInput.dataset.addressMode === "deferred";
        const wrapper = document.createElement("div");
        wrapper.className = "address-autocomplete-widget";

        const adminFormRow = originalInput.closest(".form-row");
        if (adminFormRow) {
            adminFormRow.classList.add("address-autocomplete-form-row");
        }

        if (adminMode) {
            wrapper.classList.add("address-autocomplete-widget--admin");
            originalInput.parentNode.insertBefore(wrapper, originalInput);
            wrapper.appendChild(originalInput);

            const suggestionsList = document.createElement("div");
            suggestionsList.className = "address-autocomplete-suggestions";
            suggestionsList.setAttribute("role", "listbox");
            suggestionsList.hidden = true;
            wrapper.appendChild(suggestionsList);

            let requestNumber = 0;
            let debounceTimer;
            let sessionToken = new google.maps.places.AutocompleteSessionToken();

            const closeSuggestions = () => {
                suggestionsList.hidden = true;
                suggestionsList.replaceChildren();
            };

            const loadSuggestions = async () => {
                const value = originalInput.value.trim();
                if (value.length < 3) {
                    closeSuggestions();
                    return;
                }

                const currentRequest = ++requestNumber;
                try {
                    const result = await google.maps.places.AutocompleteSuggestion
                        .fetchAutocompleteSuggestions({
                            input: value,
                            includedRegionCodes: ["de"],
                            sessionToken,
                        });
                    if (currentRequest !== requestNumber) {
                        return;
                    }

                    suggestionsList.replaceChildren();
                    result.suggestions.forEach((suggestion) => {
                        const prediction = suggestion.placePrediction;
                        if (!prediction) {
                            return;
                        }
                        const option = document.createElement("button");
                        option.type = "button";
                        option.className = "address-autocomplete-option";
                        option.setAttribute("role", "option");
                        option.textContent = prediction.text.toString();
                        option.addEventListener("click", async () => {
                            const place = prediction.toPlace();
                            await place.fetchFields({ fields: ["formattedAddress"] });
                            originalInput.value = place.formattedAddress || prediction.text.toString();
                            sessionToken = new google.maps.places.AutocompleteSessionToken();
                            closeSuggestions();
                            originalInput.focus();
                        });
                        suggestionsList.appendChild(option);
                    });

                    if (suggestionsList.childElementCount) {
                        const attribution = document.createElement("div");
                        attribution.className = "address-autocomplete-attribution";
                        const attributionImage = document.createElement("img");
                        attributionImage.src = "https://maps.gstatic.com/mapfiles/api-3/images/powered-by-google-on-white3.png";
                        attributionImage.alt = "Powered by Google";
                        attribution.appendChild(attributionImage);
                        suggestionsList.appendChild(attribution);
                        suggestionsList.hidden = false;
                    } else {
                        closeSuggestions();
                    }
                } catch (error) {
                    closeSuggestions();
                    console.error("Google Places Autocomplete konnte nicht geladen werden:", error);
                }
            };

            originalInput.addEventListener("input", () => {
                window.clearTimeout(debounceTimer);
                debounceTimer = window.setTimeout(loadSuggestions, 250);
            });
            originalInput.addEventListener("keydown", (event) => {
                if (event.key === "Escape") {
                    closeSuggestions();
                }
            });
            document.addEventListener("click", (event) => {
                if (!wrapper.contains(event.target)) {
                    closeSuggestions();
                }
            });
            return;
        }

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
