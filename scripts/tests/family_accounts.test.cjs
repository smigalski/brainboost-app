const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const source = readFileSync('brainboost/core/static/core/family_accounts.js', 'utf8');

function mount({ kind = 'with_parent', create = false, parentOnly = false } = {}) {
  const input = (properties = {}) => ({ disabled: false, required: false, ...properties,
    addEventListener(event, callback) { this[event] = callback; } });
  const kindInputs = parentOnly ? [] : [input({ value: 'with_parent', checked: kind === 'with_parent' }),
    input({ value: 'independent', checked: kind === 'independent' })];
  const createParent = parentOnly ? null : input({ checked: create });
  const parents = [input({ checked: true }), input({ checked: true })];
  const parentOptions = parentOnly ? null : { disabled: false, querySelectorAll: () => [...parents, createParent] };
  const contactInputs = Array.from({ length: 5 }, () => input({ value: 'retained contact' }));
  const newParentFields = { hidden: false, disabled: false, querySelectorAll: () => contactInputs };
  const parentSection = { hidden: false };
  const studentEmail = parentOnly ? null : input();
  const studentEmailRequired = { hidden: true };
  const errors = { focused: false, focus() { this.focused = true; } };
  const controls = {
    '[data-parent-section]': parentSection,
    '[data-parent-options]': parentOptions,
    '[data-new-parent-fields]': newParentFields,
    "[name='create_parent']": createParent,
    "[name='student_email']": studentEmail,
    '[data-student-email-required]': studentEmailRequired,
    '[data-form-errors]': errors,
  };
  const form = {
    querySelector(selector) {
      return selector === "[name='student_kind']:checked" ? kindInputs.find(i => i.checked) : controls[selector];
    },
    querySelectorAll: () => kindInputs,
  };
  vm.runInNewContext(source, { document: { querySelector: () => form } });
  return { parentSection, parentOptions, parents, newParentFields, contactInputs, createParent,
    studentEmail, studentEmailRequired, errors,
    setKind(value) {
      kindInputs.forEach(i => { i.checked = i.value === value; });
      kindInputs[0].change();
    },
  };
}

test('existing parents remain selectable; unchecked new account fields do not block submission', () => {
  const ui = mount();
  assert.equal(ui.parentSection.hidden, false);
  assert.equal(ui.parentOptions.disabled, false);
  assert.equal(ui.parents.filter(i => i.checked).length, 2);
  assert.equal(ui.newParentFields.hidden, true);
  assert.ok(ui.contactInputs.every(i => i.disabled && !i.required));
  assert.equal(ui.studentEmail.required, false);
});

test('new parent can be added alongside existing parents and requires all contact fields', () => {
  const ui = mount();
  ui.createParent.checked = true;
  ui.createParent.change();
  assert.equal(ui.newParentFields.hidden, false);
  assert.ok(ui.contactInputs.every(i => !i.disabled && i.required));
  assert.equal(ui.parents.filter(i => i.checked).length, 2);
});

test('switching account type disables irrelevant fields, preserves entries and updates email requirement', () => {
  const ui = mount({ create: true });
  ui.setKind('independent');
  assert.equal(ui.parentSection.hidden, true);
  assert.equal(ui.parentOptions.disabled, true);
  assert.ok(ui.parents.every(i => i.disabled));
  assert.ok(ui.contactInputs.every(i => i.disabled && !i.required));
  assert.equal(ui.studentEmail.required, true);
  assert.equal(ui.studentEmailRequired.hidden, false);
  ui.setKind('with_parent');
  assert.equal(ui.parentSection.hidden, false);
  assert.ok(ui.parents.every(i => !i.disabled && i.checked));
  assert.ok(ui.contactInputs.every(i => !i.disabled && i.required && i.value === 'retained contact'));
  assert.equal(ui.studentEmail.required, false);
  assert.equal(ui.studentEmailRequired.hidden, true);
});

test('independent entry hides parent section immediately', () => {
  const ui = mount({ kind: 'independent' });
  assert.equal(ui.parentSection.hidden, true);
  assert.equal(ui.studentEmail.required, true);
});

test('parent-only entry always enables contact fields and focuses submitted errors', () => {
  const ui = mount({ parentOnly: true });
  assert.equal(ui.parentSection.hidden, false);
  assert.ok(ui.contactInputs.every(i => !i.disabled && i.required));
  assert.equal(ui.errors.focused, true);
});
