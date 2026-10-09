const { test } = require('node:test');
const assert = require('node:assert/strict');
const { verifiedAddress, lookupSuggestions, withTimeout, makeCombobox } = require('../../brainboost/core/static/core/lead_address_autocomplete.js');
const part = (type, longText, shortText = longText) => ({ types: [type], longText, shortText });
const address = (code = '01067', street = 'Straße des 17. Juni', country = 'DE') => ({ addressComponents: [
    part('country', 'Deutschland', country), part('postal_code', code), part('route', street), part('street_number', '12'), part('locality', 'Dresden'),
] });
const suggestion = (place, error = null) => ({ placePrediction: { toPlace: () => ({
    ...place, fetchFields: async request => { assert.deepEqual(request.fields, ['addressComponents']); if (error) throw error; },
}) } });
test('PLZ suggestions contain just the PLZ as value, preserve zeroes and match the numeric prefix', () => {
    assert.deepEqual(verifiedAddress(address(), 'postal_code', '01'), { value: '01067', label: '01067 · Dresden' });
    assert.equal(verifiedAddress(address(), 'postal_code', '38'), null);
    assert.equal(verifiedAddress(address('1234'), 'postal_code', '12'), null);
    assert.equal(verifiedAddress(address('01067', 'Test', 'AT'), 'postal_code', '01'), null);
});
test('streets must have the exact structured postal code and country; house numbers are excluded', () => {
    assert.equal(verifiedAddress(address(), 'street', 'Stra', '01067').value, 'Straße des 17. Juni');
    assert.equal(verifiedAddress(address('01069'), 'street', 'Stra', '01067'), null);
    assert.equal(verifiedAddress(address('', 'Test'), 'street', 'Test', '01067'), null);
    assert.equal(verifiedAddress(address('01067', '', 'DE'), 'street', 'Test', '01067'), null);
    assert.equal(verifiedAddress(address('01067', 'Test', 'AT'), 'street', 'Test', '01067'), null);
    assert.equal(verifiedAddress({ formattedAddress: 'Teststraße, 01067 Dresden' }, 'street', 'Test', '01067'), null);
});
test('lookup filters before display, deduplicates streets and tolerates individual detail failures', async () => {
    let request;
    const api = { AutocompleteSuggestion: { fetchAutocompleteSuggestions: async value => {
        request = value;
        return { suggestions: [suggestion(address()), suggestion(address('01069')), suggestion(address()), suggestion(address(), new Error('quota')), {}] };
    } } };
    const found = await lookupSuggestions(api, 'street', 'Stra', '01067');
    assert.equal(found.length, 1);
    assert.equal(found[0].value, 'Straße des 17. Juni');
    assert.deepEqual(request.includedRegionCodes, ['de']);
    assert.ok(request.input.includes('01067'));
    assert.deepEqual(request.includedPrimaryTypes, ['route', 'street_address', 'premise']);
});
test('invalid PLZ / short input makes no requests; postal input uses postal_code filter', async () => {
    let calls = 0;
    const api = { AutocompleteSuggestion: { fetchAutocompleteSuggestions: async request => {
        calls++;
        assert.deepEqual(request.includedPrimaryTypes, ['postal_code']);
        return { suggestions: [suggestion(address())] };
    } } };
    assert.deepEqual(await lookupSuggestions(api, 'street', 'Test', '0106'), []);
    assert.deepEqual(await lookupSuggestions(api, 'postal_code', 'Dresden'), []);
    assert.deepEqual(await lookupSuggestions(api, 'postal_code', '0'), []);
    assert.equal(calls, 0);
    assert.equal((await lookupSuggestions(api, 'postal_code', '01')).length, 1);
});
test('outdated requests stop before details and never return stale options', async () => {
    let details = 0;
    const api = { AutocompleteSuggestion: { fetchAutocompleteSuggestions: async () => ({ suggestions: [
        { placePrediction: { toPlace: () => { details++; return address(); } } },
    ] }) } };
    assert.deepEqual(await lookupSuggestions(api, 'postal_code', '01', '', () => false), []);
    assert.equal(details, 0);
    let current = true;
    api.AutocompleteSuggestion.fetchAutocompleteSuggestions = async () => ({ suggestions: [{ placePrediction: { toPlace: () => ({
        ...address(), fetchFields: async () => { current = false; },
    }) } }] });
    assert.deepEqual(await lookupSuggestions(api, 'postal_code', '01', '', () => current), []);
});
test('service and detail failures propagate; hung requests time out', async () => {
    const api = { AutocompleteSuggestion: { fetchAutocompleteSuggestions: async () => { throw new Error('quota'); } } };
    await assert.rejects(lookupSuggestions(api, 'postal_code', '01'), /quota/);
    api.AutocompleteSuggestion.fetchAutocompleteSuggestions = async () => ({ suggestions: [suggestion(address(), new Error('denied'))] });
    await assert.rejects(lookupSuggestions(api, 'postal_code', '01'), /places_details_unavailable/);
    await assert.rejects(withTimeout(new Promise(() => {}), 5), /places_timeout/);
});
// Minimal DOM double exercises real combobox events, including in-flight responses.
class Element {
    constructor() { this.attributes = {}; this.events = {}; this.children = []; this.value = ''; this.hidden = true; this.disabled = false; }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(key, handler) { (this.events[key] ||= []).push(handler); }
    dispatchEvent(event) { for (const handler of this.events[event.type] || []) handler(event); }
    replaceChildren() { this.children = []; }
    appendChild(node) { this.children.push(node); }
    querySelectorAll() { return this.children.filter(node => node.attributes.role === 'option'); }
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const key = (input, name) => { let prevented = false; input.dispatchEvent({ type: 'keydown', key: name, preventDefault: () => { prevented = true; } }); return prevented; };
test('keyboard selection, Escape, blur and input changes invalidate delayed results', async () => {
    const input = new Element(), list = new Element(), status = new Element();
    list.id = 'suggestions'; status.id = 'status';
    global.document = { activeElement: input, createElement: () => new Element() };
    let resolve;
    const controller = makeCombobox(input, list, status, () => new Promise(done => { resolve = done; }), () => {});
    input.value = '01'; input.dispatchEvent({ type: 'input' });
    await pause(380);
    key(input, 'Escape'); resolve([{ value: '01067', label: '01067 · Dresden' }]);
    await pause(0); assert.equal(list.hidden, true);
    input.dispatchEvent({ type: 'input' }); await pause(380);
    input.value = ''; input.dispatchEvent({ type: 'input' });
    resolve([{ value: '01067', label: 'old' }]); await pause(0); assert.equal(list.hidden, true);
    controller.invalidate();
    input.value = '01'; input.dispatchEvent({ type: 'input' }); await pause(380);
    resolve([{ value: '01067', label: '01067 · Dresden' }]); await pause(0);
    assert.equal(list.hidden, false);
    assert.equal(key(input, 'Enter'), true); assert.equal(input.value, '01');
    key(input, 'ArrowUp'); assert.equal(input.attributes['aria-activedescendant'], 'suggestions_0');
    key(input, 'ArrowDown'); assert.equal(input.attributes['aria-activedescendant'], 'suggestions_0');
    key(input, 'Enter'); assert.equal(input.value, '01067'); assert.equal(list.hidden, true);
    input.dispatchEvent({ type: 'input' }); await pause(380);
    input.dispatchEvent({ type: 'blur' }); resolve([{ value: '01067', label: 'late' }]); await pause(0);
    assert.equal(list.hidden, true);
    delete global.document;
});
test('changing PLZ clears the street and discards in-flight suggestions; API failure keeps manual input', async () => {
    const { mount } = require('../../brainboost/core/static/core/lead_address_autocomplete.js');
    const postal = new Element(), street = new Element();
    const nodes = Object.fromEntries(['postal_code_suggestions', 'street_suggestions', 'postal_code_status', 'street_status'].map(id => {
        const node = new Element(); node.id = id; return [id, node];
    }));
    const form = {
        querySelector: selector => selector.includes('postal_code') ? postal : street,
        querySelectorAll: () => [],
    };
    global.document = { activeElement: street, getElementById: id => nodes[id], createElement: () => new Element() };
    global.addEventListener = () => {};
    let resolve;
    global.google = { maps: { importLibrary: async () => ({ AutocompleteSuggestion: {
        fetchAutocompleteSuggestions: () => new Promise(done => { resolve = done; }),
    } }) } };
    postal.value = '01067'; street.value = 'Stra';
    mount(form);
    try {
        await global.initLeadAddressAutocomplete();
        await pause(380);
        postal.value = '01069'; postal.dispatchEvent({ type: 'input' });
        assert.equal(street.value, '');
        assert.match(nodes.street_status.textContent, /erneut eingeben/);
        resolve({ suggestions: [suggestion(address())] });
        await pause(0);
        assert.equal(nodes.street_suggestions.hidden, true);
        assert.match(nodes.street_status.textContent, /erneut eingeben/);
        street.value = 'Manuelle Straße';
        global.leadAddressAutocompleteUnavailable();
        assert.equal(street.value, 'Manuelle Straße');
        assert.equal(postal.value, '01069');
        assert.match(nodes.street_status.textContent, /manuell/);
    } finally {
        global.leadAddressAutocompleteUnavailable();
        delete global.document; delete global.google; delete global.addEventListener;
    }
});
