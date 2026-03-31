import os
import re

file_path = "src/nuki_integration/static/assets/app.js"
with open(file_path, "r") as f:
    content = f.read()

# New Modernized Functions
new_functions = """
function renderChecksStepOverview() {
  const win = state.checksSession.windows.find((w) => w.id === state.checksWindowId);
  const funnel = state.checksFunnel;
  const funnelLabel = state.checksFunnelType === "checkin" ? "Check-In" : "Check-Out";
  return `
    <section class="panel">
      <h2 class="panel-title">Übersicht</h2>
      ${funnel.description ? `<p class="subtle mt-8">${escapeHtml(funnel.description)}</p>` : ""}
      <div class="funnel-overview-grid">
        <div class="summary-item">
          <span class="summary-label">Zeitfenster</span>
          <strong>${fmtDate(win?.starts_at)} → ${fmtDate(win?.ends_at)}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">Schritte</span>
          <strong>${funnel.steps.length} Aufgaben</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">Typ</span>
          <strong>${escapeHtml(funnelLabel)}</strong>
        </div>
      </div>
      <div class="funnel-actions mt-24">
        <button type="button" id="checks-funnel-back" class="secondary">Abbrechen</button>
        <button type="button" id="checks-funnel-next">${funnelLabel} starten</button>
      </div>
    </section>
  `;
}

function renderChecksStepN(stepIndex) {
  const step = state.checksFunnel.steps[stepIndex - 1];
  if (!step) return "";
  const draft = state.checksFunnelDraft[step.id] || { checked: false, note: "" };
  const totalSteps = state.checksFunnel.steps.length;
  const isLast = stepIndex === totalSteps;
  const canProceed = step.requires_note
    ? Boolean(draft.note && draft.note.trim().length > 0)
    : Boolean(draft.checked);
  const stepError = state.checksStepError;
  return `
    <section class="panel">
      <div class="split mb-16">
        <h2 class="panel-title">${escapeHtml(step.title)}</h2>
        <span class="subtle">Schritt ${stepIndex} von ${totalSteps}</span>
      </div>
      ${step.body ? `<div class="step-body-content">${escapeHtml(step.body).replace(/\\n/g, "<br />")}</div>` : ""}
      ${step.image_path ? `<img class="step-image" src="${escapeHtml(step.image_path)}" alt="${escapeHtml(step.title)}" />` : ""}
      ${step.requires_note ? `
        <div class="mt-16">
          <label for="step-note-${step.id}">Ihre Rückmeldung (Pflichtfeld)
            <textarea id="step-note-${step.id}" class="mt-8 ${stepError ? " input-error" : ""}" data-checks-note="${step.id}"
              placeholder="Geben Sie hier Ihre Informationen ein..."
              aria-required="true">${escapeHtml(draft.note || "")}</textarea>
          </label>
          ${stepError ? `<p class="pill bad mt-8" style="display: block; text-align:center;">${escapeHtml(stepError)}</p>` : ""}
        </div>
      ` : `
        <label class="checkbox-row mt-16" for="checks-check-${step.id}">
          <input id="checks-check-${step.id}" type="checkbox" data-checks-check="${step.id}"
            ${draft.checked ? "checked" : ""} />
          <span style="font-weight: 600;">Ich habe diesen Punkt gelesen und bestätigt.</span>
        </label>
        ${stepError ? `<p class="pill bad mt-8" style="display: block; text-align:center;">${escapeHtml(stepError)}</p>` : ""}
      `}
      <div class="funnel-actions mt-32">
        <button type="button" id="checks-funnel-back" class="secondary">Zurück</button>
        <button type="button" id="checks-funnel-next"
          ${canProceed ? "" : 'style="opacity: 0.5; cursor: not-allowed;" disabled'}>
          ${isLast ? "Prozess abschließen" : "Nächster Schritt"}
        </button>
      </div>
    </section>
  `;
}

function renderChecksStepDone() {
  const win = state.checksSession.windows.find((w) => w.id === state.checksWindowId);
  const funnelLabel = state.checksFunnelType === "checkin" ? "Check-In" : "Check-Out";
  const successText = state.checksFunnelType === "checkin"
    ? "Check-In erfolgreich erfasst. Wir wünschen Ihnen ein angenehmes Training!"
    : "Check-Out erfolgreich erfasst. Vielen Dank und bis zum nächsten Mal!";
  return `
    <section class="panel">
      <h2 class="panel-title">${escapeHtml(funnelLabel)} erfolgreich</h2>
      <div class="pill good mt-16" style="display: block; padding: 16px; font-size: 1rem; text-align: center;">${escapeHtml(successText)}</div>
      
      <div class="funnel-overview-grid mt-24">
        <div class="summary-item">
          <span class="summary-label">Zeitraum</span>
          <strong>${fmtDate(win?.starts_at)} → ${fmtDate(win?.ends_at)}</strong>
        </div>
        <div class="summary-item">
          <span class="summary-label">Status</span>
          <strong>Abgeschlossen</strong>
        </div>
      </div>
      <div class="funnel-actions mt-32">
        <button type="button" id="checks-funnel-back-list" style="width: 100%;">Zurück zur Übersicht</button>
      </div>
    </section>
  `;
}
"""

# Pattern to find the existing block of functions
pattern = r"function renderChecksStepOverview\(\) \{[\s\S]*?function renderChecksStepDone\(\) \{[\s\S]*?\}\n\nfunction renderChecksWindowList"
replacement = new_functions + "\n\nfunction renderChecksWindowList"

patched_content = re.sub(pattern, replacement, content)

with open(file_path, "w") as f:
    f.write(patched_content)
print("SUCCESS: Public check-in functions patched.")
