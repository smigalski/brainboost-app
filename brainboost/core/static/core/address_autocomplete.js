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
        const form = originalInput.form;
        const wrapper = document.createElement("div");
        wrapper.className = "address-autocomplete-widget";

        const placeAutocomplete = new google.maps.places.PlaceAutocompleteElement();
        placeAutocomplete.placeholder = originalInput.getAttribute("placeholder") || "Wohnadresse eingeben";
        placeAutocomplete.includedRegionCodes = ["de"];

        wrapper.appendChild(placeAutocomplete);
        originalInput.parentNode.insertBefore(wrapper, originalInput);
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
