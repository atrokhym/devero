# Linked and integrated forms

Read this reference when a Devero linked/composite section is empty, mismatched,
or stuck in a template or Plan of Care popup.

## Decision flow

For each linked target section:

1. Read the target note's committed section, outside any popup.
2. If it contains data, preserve it and stop processing that section.
3. Open the linked action once.
4. If the outer linked-form list has rows, select source-matching rows from that
   list and insert them. Do not open **Insert From Template**.
5. Only when the outer list is genuinely empty, open the template chooser.
6. Select source-matching template rows, commit them to the outer list or target,
   and then complete any required outer-list insert.
7. Verify committed target content outside the popup.

Do not infer step 7 from the number of matches found in the source, the number
of DOM checkboxes checked, or text still visible inside the Plan of Care modal.

## Target-first discovery across template versions

Devero can rename or replace OASIS templates (for example, E1 to E2) without
keeping a stable clinical-section catalog. Work backward from the target:

1. Visit every target page and relevant frame, waiting until the inventory of
   rendered `cmwf-integrated-action` elements stabilizes.
2. Ignore transient modal/template surfaces and nonclinical actions such as
   `Clear`.
3. Derive a section stem from action attributes such as `id`, `data-section`,
   and linked output IDs. Normalize case and punctuation so compact and
   underscored variants can match.
4. Resolve the source record using structural IDs and payload proximity first;
   use visible title text only as supporting evidence.
5. Open each unresolved empty target action with a trusted pointer click and
   select source-matching rows through the linked-form workflow.

Do not hardcode a list such as Vital Signs, Pain, Neurological, or Infusion.
Those are examples of current sections, not stable schema identifiers. Keep
explicit mappings only for confirmed many-to-one migrations where structural
matching is insufficient.

## Committed target detection

Preservation must be based on controller-backed output outside popup/template
surfaces. In particular:

- A visible **Clear** button is permanent UI chrome and says nothing about
  whether the section contains data.
- Hidden module rows, cached template choices, action titles, and flattened
  `Title + Clear` text are not committed target content.
- For `cmwf-cop`, prefer structural rows such as rendered
  `.cop-items .copElement` elements containing clinical payload.
- For linked text displays, require meaningful rendered output associated with
  the target section and exclude modal/template descendants.

The ordinary field injector must skip `cmwf-text-display`, `cmwf-cop`,
`cmwf-integrated-action`, and their descendants. Directly assigning
`innerText` creates a dangerous false preserve: later discovery sees the
injected text, prints that the linked section is populated, and never opens the
selector, but the text is not persisted by Devero. Only the dedicated linked
workflow may create linked selections.

## E1 to E2 Interventions/Goals mapping

The legacy source can expose two actions:

```text
interventionsGoalsRequest
otherInterventionsGoalsRequest
```

The newer target can expose one combined action:

```text
combinedInterventionsGoalsRequest
```

Merge both source sections into the combined target source set. Empty legacy
`Interventions/Goals` does not mean `Other Interventions/Goals` is empty.
Normalize dates and headings for matching, but preserve clinically meaningful
numbers when comparing row text. Variable template placeholders may require a
separate normalized comparison that removes the placeholder's numeric slot.

## Mutually exclusive accordions

The integrated GOALS/INTERVENTIONS template groups behave like a true accordion:
opening one group closes the previous group. Therefore:

1. Open one group using its actual disclosure chevron.
2. Confirm its choices became visible.
3. Match and physically select its rows immediately.
4. Verify each selected checkbox is still selected through the bound UI/model.
5. Continue to the next group.
6. Commit once after all groups have been processed.

Do not first expand every group and run one final selection pass; that only sees
the last open group.

## Trusted selection

The Plan of Care controller may ignore synthetic DOM manipulation. In
particular, this is not a reliable selection:

```javascript
checkbox.checked = true;
checkbox.dispatchEvent(new Event("change", { bubbles: true }));
```

Prefer a Playwright locator or mouse click on the visible checkbox/label. After
clicking, verify both the visual checkbox and, where inspectable, its Angular row
selection state. If the control cannot receive a trusted click, treat selection
as failed rather than forcing `checked` and incrementing a success counter.

Re-resolve locators after each accordion transition because Angular can replace
row nodes.

## Insert and popup semantics

The live Plan of Care **Insert to form** control has been observed with:

```html
ng-mousedown="carePlanModuleController.insertIntoForm($event, false)"
```

Use a trusted pointer click so `mousedown` fires. Do not call a guessed Angular
controller method in addition to the click; duplicate actions can produce
inconsistent state.

Classify the resulting overlay by its message:

- **“Please select one or more rows to insert into form” / “Not valid!”** is a
  selection failure. Its SweetAlert `OK` only dismisses the error. The module
  remains open and nothing was committed. Re-select using trusted clicks.
- **“The following warnings were found … Do you want to continue?”** is a
  continuation confirmation. Click its visible `OK` with a trusted click, then
  wait for the commit.
- An unknown Error/Warning/Confirmation must be inspected before continuing.

SweetAlert warnings may use a structure such as:

```text
.sweet-alert.submit-alert.showSweetAlert.visible
  .sa-button-container
    button.confirm   (OK)
```

They are not necessarily inside `.modal-dialog` or `[role="dialog"]`. Search
visible controls across current pages and frames, but scope an `OK` click to the
newly appeared overlay and classify its text first.

## Commit postconditions

Success requires all relevant conditions:

- no blocking warning remains;
- the Plan of Care/template surface has completed the expected transition;
- the target note's `combinedInterventionsGoalsRequest` section displays
  committed goal/intervention content outside `#formFramePopup`;
- reopening the target linked action shows the inserted list if that is the
  form's normal representation.

If the popup remains open with selected-looking rows, or the target content is
empty, report a failed/pending commit. Never print “Inserted N rows” solely from
the pre-commit selection count.

## Regression cases

Tests should cover:

- populated target is preserved without opening a template;
- generic field injection cannot write into read-only linked projections;
- persistent Clear chrome and hidden/cached module rows do not count as data;
- late-hydrating target actions are included after inventory stabilization;
- compact and underscored action IDs resolve without clinical-name hardcoding;
- empty outer list opens the template;
- one-open-at-a-time accordions retain selections across groups;
- source legacy IDs merge into the combined target ID;
- trusted checkbox selection updates the controller;
- the no-selection SweetAlert is treated as failure;
- the continuation warning is confirmed and then committed;
- an `OK` outside Bootstrap dialog wrappers is found;
- target verification excludes content inside the still-open Plan of Care
  modal;
- no false-success log is emitted when target content remains empty.
