(function () {
    const form = document.querySelector("[data-family-conversion-form]");
    if (!form) return;
    const parentSection = form.querySelector("[data-parent-section]");
    const parentOptions = form.querySelector("[data-parent-options]");
    const newParentFields = form.querySelector("[data-new-parent-fields]");
    const createParent = form.querySelector("[name='create_parent']");
    const kindInputs = form.querySelectorAll("[name='student_kind']");
    const studentEmail = form.querySelector("[name='student_email']");
    const studentEmailRequired = form.querySelector("[data-student-email-required]");

    function refresh() {
        const kind = form.querySelector("[name='student_kind']:checked");
        const needsParent = !kind || kind.value === "with_parent";
        parentSection.hidden = !needsParent;
        if (parentOptions) {
            parentOptions.disabled = !needsParent;
            parentOptions.querySelectorAll("input").forEach((input) => { input.disabled = !needsParent; });
        }
        const needsNewParent = needsParent && (!createParent || createParent.checked);
        newParentFields.hidden = !needsNewParent;
        newParentFields.disabled = !needsNewParent;
        newParentFields.querySelectorAll("input").forEach((input) => {
            input.disabled = !needsNewParent;
            input.required = needsNewParent;
        });
        if (studentEmail) studentEmail.required = !needsParent;
        if (studentEmailRequired) studentEmailRequired.hidden = needsParent;
    }

    kindInputs.forEach((input) => input.addEventListener("change", refresh));
    if (createParent) createParent.addEventListener("change", refresh);
    refresh();
    const errors = form.querySelector("[data-form-errors]");
    if (errors) errors.focus();
}());
