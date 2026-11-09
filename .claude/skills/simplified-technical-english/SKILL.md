---
name: simplified-technical-english
description: Simplified Technical English for this repository's documentation. Use when writing or editing a site page, README, spec, ADR, GLOSSARY entry, docstring shown to readers, or issue and PR text, or when asked to simplify or apply STE to a document.
---

# Simplified Technical English

Write documentation that a reader can understand on the first read, also when English is not the reader's first language. These rules are based on ASD-STE100 Issue 9. They are adapted for software. This repository does not claim ASD-STE100 compliance.

ASD owns the standard and its dictionary, so this file does not copy them. A free official copy is available from asd-ste100.org.

Rules marked **(project)** are this repository's own additions.

## Steps

1. **Find the text to change.** New text: all of it. An existing document: every sentence in the part you were asked to change.
2. **Mark each sentence** as a *procedure* (the reader does something) or a *description* (the reader learns something).
3. **Apply the rules** below to each sentence.
4. **Compare meaning with the original.** Keep every number, name, condition, limit, warning and reason. Simple words must not change the facts. If a sentence is unclear, read the code or the source. Then write what is true.
5. **Change generated text at its source:** a Python docstring, `GLOSSARY.md`, `--help` text or `site/nav.json`. Tests can pin some sentences. Update those tests in the same change.
6. **Run the checks** if you changed `site/`: `python -m scripts.site build`, then `python -S -m scripts.site check`.

The work is done when every changed sentence obeys the limits, and step 4 found no lost fact.

## Text that stays as it is

- Code, commands, identifiers, file paths, output, URLs and field names.
- Direct quotes, such as an owner's decision or a vendor's documentation.
- The Decision section of an accepted ADR. To change a decision, write a new ADR (`docs/adr/README.md`). Plain-language edits elsewhere in an ADR must keep the meaning.
- Dated records in `progress.md` and the decision logs, unless you were asked to change them.

## Sentences

- **Procedure:** 20 words or fewer.
- **Description:** 25 words or fewer.
- **Paragraph:** one topic, 6 sentences or fewer. Put the key fact first.
- **One idea per sentence.** Split a long sentence at each new idea.
- **Keep small words:** "the", "a", "is", "that". They help the reader see the structure.
- **Word count (project):** a code span, a number with its unit, a product name and a GLOSSARY term each count as one word.
- **Punctuation (project):** use a period where you would use a semicolon. Use a list for three or more parallel items.

## Procedures

- Use the imperative: "Run the wizard."
- Write one instruction per sentence. Write two only if the reader does them at the same time.
- Put a condition first: "If the check fails, read the log."
- Put a warning before the step it applies to.
- Number steps that have an order.

## Verbs

- Use these forms: imperative, infinitive, simple present, simple past, simple future.
- Use a past participle only as an adjective: "the stored snapshot".
- Use the active voice: "The collector writes the object."
- Use the passive only when the actor is unknown, and only in a description.
- Use one verb, not a verb made of several words: "stop", not "bring to a stop".
- Use "-ing" words only inside a technical noun, such as "Billing Line".
- Use the verb to name the action: "Validate the file", not "Perform validation of the file".

## Words

- **One word, one meaning.** Use each word for one meaning only. Use one word for one thing in the whole document.
- **Technical nouns are permitted:** GLOSSARY terms, code identifiers and product names. Write GLOSSARY terms with their capitals. Use the term, not its `_Avoid_` synonyms.
- **Technical verbs are permitted (project):** words with an exact meaning in this domain, such as deploy, ingest, allocate, reconcile, query, parse, commit, merge and render.
- **Noun clusters:** three words or fewer. Count a GLOSSARY term as one word. Break a longer cluster with "of" or "for".
- **Define an abbreviation** the first time you use it on a page.
- **"Must" is a requirement. "Can" is a possibility. "Should" is a recommendation.**

Use the plain word (project; this is not the ASD dictionary):

| Instead of | Use |
|---|---|
| utilize, leverage, make use of | use |
| in order to | to |
| ensure | make sure |
| prior to | before |
| subsequent to, following | after |
| due to, owing to | because of |
| via | through, with |
| terminate | stop |
| obtain, retrieve | get |
| perform, carry out | do, or the exact verb |
| facilitate, enable | help, or "let the reader …" |
| is able to | can |
| in the event that | if |
| a number of | several, or the number |
| however, thus, hence | but, so |

## Examples

Procedure:

> ~~In order to ensure that the deployment is not impacted, it is recommended that the package be rebuilt prior to running the wizard.~~
>
> Build the package again. Then run the wizard.

Description:

> ~~A Billing Line that spans several hours, which can occur when consecutive hours are merged by AWS, is scoped against the entirety of its charge period.~~
>
> AWS can merge consecutive hours into one Billing Line. The billing scope of that line is its whole charge period.

Keep the limit, with the reason:

> ~~Files are size-capped for safety.~~
>
> A file over 512 MiB is rejected before download. The summary names its key.
