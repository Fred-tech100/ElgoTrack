"use strict";

const addLineButton = document.getElementById("add-invoice-line");
const lineList = document.getElementById("invoice-lines");
const lineTemplate = document.getElementById("empty-invoice-line");
const totalForms = document.getElementById("id_items-TOTAL_FORMS");
const maxFormsInput = document.getElementById("id_items-MAX_NUM_FORMS");

if (addLineButton && lineList && lineTemplate && totalForms && maxFormsInput) {
  addLineButton.addEventListener("click", () => {
    const index = Number(totalForms.value);
    if (index >= Number(maxFormsInput.value)) return;

    const line = lineTemplate.content.cloneNode(true);
    line.querySelectorAll("[name], [for], [id]").forEach((element) => {
      for (const attribute of ["name", "for", "id"]) {
        if (element.hasAttribute(attribute)) {
          element.setAttribute(attribute, element.getAttribute(attribute).replaceAll("__prefix__", index));
        }
      }
    });
    lineList.append(line);
    totalForms.value = index + 1;
  });
}
