---
name: simplified-technical-english
description: Simplified Technical English for this repository's documentation. Use when writing or editing a site page, README, spec, ADR, GLOSSARY entry, docstring shown to readers, or issue and PR text, or when asked to simplify or apply STE to a document.
---

# Simplified Technical English

Write documentation that a reader can understand on the first read, also when English is not the reader's first language.

These rules are our summary of ASD-STE100 Issue 9, adapted for software. Numbers such as (5.1) refer to its rules. Items marked **(project)** are additions for this repository. This repository does not claim compliance with ASD-STE100.

ASD owns the standard and its dictionary. Keep copies of them out of git. To get a free copy, go to asd-ste100.org.

## Steps

1. **Find the text to change.** New text: all of it. An existing document: every sentence in the part that you were asked to change.
2. **Mark each sentence** as a *procedure* (the reader does something) or a *description* (the reader learns something).
3. **Apply the rules** below to each sentence. If a word replacement changes the meaning, write the sentence in a different way (9.1).
4. **Compare the meaning with the original.** Keep every number, name, condition, limit, warning and reason. If a sentence is not clear, read the code or the source. Then write what is true.
5. **Change generated text at its source:** a Python docstring, `GLOSSARY.md`, `--help` text or `site/nav.json`. Tests can pin some sentences. Update those tests in the same change.
6. **Run the checks** if you changed `site/`: `python -m scripts.site build`, then `python -S -m scripts.site check`.

The work is done when every changed sentence obeys the limits and step 4 found no lost fact.

## Text that stays as it is

- Code, commands, identifiers, file paths, output, URLs and field names.
- Direct quotes, such as an owner's decision or a vendor's documentation.
- The Decision section of an accepted ADR. To change a decision, write a new ADR (`docs/adr/README.md`).
- Dated records in `progress.md` and the decision logs, unless you were asked to change them.

## Sentences and paragraphs

- **Procedure:** 20 words or fewer (5.1).
- **Description or note:** 25 words or fewer (6.3, 5.5).
- **Paragraph:** one topic and 6 sentences or fewer (6.5, 6.6).
- **One topic in each sentence** (4.1). Give information gradually (6.1). Use key words to show the structure (6.2).
- **Keep the small words:** articles, "this" and "these" before a noun (4.5). Write "that" after verbs such as "make sure" (GR-1). Write words in full, with no contractions (4.2).
- **Connect related sentences** with words such as "then" and "but" (4.4).
- **Use a vertical list** for complex text (4.3).
- **Use a period, not a semicolon** (8.1).

Count these as one word:
- numbers, with or without units; abbreviations; alphanumeric identifiers; quoted text; titles and labels; names of people, groups and organizations (8.6);
- text in parentheses (8.5);
- hyphenated words (8.7);
- code spans and GLOSSARY terms **(project)**.

In a vertical list, a colon ends a sentence for the word count (8.4).

## Procedures

- Use the imperative: "Run the wizard." (5.3)
- Write one instruction in each sentence. Write two only if the actions occur at the same time (5.2).
- Put a condition first, then a comma, then the command: "If the check fails, read the log." (5.4)
- Number the steps that have an order (5.2).
- Write notes to give information only (5.5).
- **Warnings:** start with the word "Warning" or "Caution". Give the command or condition. Then give the risk (7.1–7.3). In this repository, use "Warning" for permanent data loss and "Caution" for cost or a failed deployment **(project)**.
- Descriptions do not use the imperative (section 6).

## Verbs

- Use only these forms: infinitive, imperative, simple present, simple past, simple future, and the past participle as an adjective ("the stored snapshot") (3.2, 3.3).
- Write one verb, with no auxiliary constructions such as "has been done" (3.4).
- Use the active voice. In a description, use the passive only when the agent is unknown (3.6).
- Use "-ing" words only in a technical noun, such as "Billing Line" (3.5).
- Use a verb to describe an action: "Validate the file", not "Do a validation of the file" (3.7).
- Write a single verb in place of a phrasal verb: "start", not "kick off" (9.3).

## Words

- **One word, one meaning.** Use a word only with its approved meaning and part of speech (1.2, 1.3). Use the same word for the same thing (1.11, 9.4).
- **Technical nouns are permitted** when they are in a category of Rule 1.5, which includes computer science and information technology. GLOSSARY.md is our terminology list (1.5, 1.8). Write GLOSSARY terms with their capitals, not their `_Avoid_` synonyms. Do not use a technical noun as a verb (1.7).
- **Technical verbs are permitted** in a category of Rule 1.12. Its computer examples include load, install, update, validate, delete, encrypt, process, download. Our additions, with their software meaning **(project)**: run, deploy, ingest, allocate, reconcile, query, parse, commit, merge, render.
- **Multi-word nouns:** three words or fewer (2.1). Use "of", "on", "in" or "for" to divide a longer one. A longer technical noun: write it in full, then give a short form (2.2).
- **Spelling:** American English (1.14).
- **Latin abbreviations:** write "for example" and "that is" (GR-6).
- **Pronouns:** if "it" or "this" can refer to two things, write the noun again (GR-3, GR-4).
- **People:** use gender-neutral words (GR-7).

**"Must" is a requirement. "Can" is a possibility.** "Should" and "may" are not approved. For advice, write "we recommend that".

These replacements are checked against the Issue 9 dictionary:

| Instead of | Use |
|---|---|
| utilize | use |
| ensure | make sure |
| prior to | before |
| subsequent to | after |
| due to | because, or because of |
| via | through |
| terminate | stop |
| obtain | get |
| perform | do |
| however | but |
| should | must, or "we recommend that" |
| may | can |
| need (verb) | must, or "it is necessary to" |
| in the event that | if |
| several, a number of | the number, if you know it |

To check another word, use a local copy of the dictionary: UPPERCASE entries are approved, and lowercase entries are not. If a word has no entry, it needs a technical category or a different construction **(project)**.

## Examples

Procedure:

> ~~In order to ensure that the deployment is not impacted, it is recommended that the package be rebuilt prior to running the wizard.~~
>
> Build the package again. Then run the wizard.

Description:

> ~~A Billing Line that spans several hours, which can occur when consecutive hours are merged by AWS, is scoped against the entirety of its charge period.~~
>
> AWS can merge consecutive hours into one Billing Line. The billing scope of that line is its full charge period.

Keep the limit:

> ~~Files are size-capped for safety.~~
>
> The collector rejects a file over 512 MiB before the download. The summary shows the key of that file.
