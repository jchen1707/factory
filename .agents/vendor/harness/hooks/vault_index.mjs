#!/usr/bin/env node
/**
 * Build an agent-readable index of the whole vault, not only the learnings folder.
 *
 * An Obsidian `.base` file is a **query**, evaluated by Obsidian's own UI. An agent that
 * reads it gets the query definition back, never its results — so a Base makes the vault
 * browsable for a human and does nothing at all for retrieval by an agent. The
 * agent-readable half has to be generated Markdown. `session_learnings.mjs` writes one for
 * the learnings folder; this writes one for every note in the vault.
 *
 * **Rows are derived, not required.** Most hand-written notes carry no front matter, and an
 * index that only listed notes with a `summary:` field would start out covering a fraction
 * of the vault and silently stay there. Front matter is used when present and inferred when
 * not: the description falls back to the first line of real prose.
 *
 * Configure with `OBSIDIAN_VAULT_DIRECTORY`. The value must be an absolute vault path. An
 * unset, empty, relative, or missing path means there is no vault.
 *
 * Refresh by hand with `node <this file>`.
 *
 * This was `python-harness`-only until phase 5, and that asymmetry was defect 7: the
 * frontend repo wrote notes and depended on a session ending in the *other* repo to index
 * them. One implementation in layer A cannot be installed in one repo and not the other.
 */

import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, resolve, sep } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

export const INDEX_NAME = '_VAULT_INDEX.md';

/**
 * Folders holding no indexable prose. `.obsidian` is editor config. An Excalidraw note is a
 * base64 drawing payload inside a `.md` file, so its first line of prose is kilobytes of
 * encoded binary.
 */
const SKIP_DIRS = new Set(['.obsidian', '.trash', '.git', 'Excalidraw', 'Attachments']);

/**
 * Generated indexes, this one included. Indexing an index yields a row that says nothing
 * and grows every time the thing it describes does.
 */
const SKIP_NAMES = new Set([INDEX_NAME, '_INDEX.md']);

const MAX_DESC = 160;

/**
 * Shortest line worth treating as a description. Below this it is a fragment — a stray
 * word, a table cell — and the next line is a better answer.
 */
const MIN_DESC = 25;

/**
 * Lines that carry no information about what a note covers: headings, bullets, table rows
 * and rules, fences, images, and front-matter delimiters.
 */
const NOISE = /^\s*(#{1,6}\s|[-*+]\s|\d+\.\s|\||```|!\[|-{3,}\s*$|={3,}\s*$)/;

/**
 * A short line built around a wikilink points at another note rather than describing this
 * one. Whole folders open with a shared pointer — "Refer to [[Template]] for guidelines" —
 * which would hand a dozen unrelated notes the same description and make the index useless
 * for choosing between them. Long lines are exempt: those carry their own content after the
 * reference.
 */
const CROSS_REFERENCE = /\[\[/;
const CROSS_REFERENCE_MAX = 100;

/**
 * Headings, bullets and numbered items are the part of NOISE that still names a topic. They
 * make a poor description while prose exists, and the only description there is when it does
 * not: a note written entirely as a bullet list is a real note, and returning `''` for it
 * hides it from the one file an agent reads before choosing what to open. Table rows, fences
 * and images stay out — they name nothing on their own.
 */
const OUTLINE = /^\s*(#{1,6}\s|[-*+]\s|\d+\.\s)/;

const OUTLINE_MAX = 12;
const OUTLINE_JOIN = ' · ';

/**
 * Strip blockquote and Obsidian callout markers, keeping the text inside.
 *
 * Separate from `stripMarkdown` because the noise test has to run on the *unwrapped* line:
 * a bullet nested in a callout (`>> - **Now:** ...`) is still a bullet, and testing the raw
 * line lets it through as though it were prose.
 */
export function unquote(line) {
  return line.replace(/^\s*>+\s*/, '').replace(/^\[![a-z]+\][-+]?\s*/i, '');
}

/** Reduce one line of Markdown to the text a human would read aloud. */
export function stripMarkdown(line) {
  return unquote(line)
    .replace(/!?\[\[(?:[^\]|]*\|)?([^\]]+)\]\]/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`~]+/g, '')
    .trim();
}

/**
 * Parse the flat `key: value` front matter these notes use.
 *
 * Deliberately not a YAML parser: layer A has no runtime dependencies anywhere it runs.
 * Block sequences (`tags:` then an indented `- hashmap`) fold into a comma-joined string,
 * because inline `[a, b]` and block form are the only two shapes the vault contains and
 * callers want one of them.
 */
export function frontMatter(text) {
  if (!text.startsWith('---')) return {};
  const after = text.slice(text.indexOf('---\n') + 4);
  const end = after.indexOf('\n---');
  // Unterminated front matter is body text that happens to start with a rule.
  if (end === -1) return {};
  const block = after.slice(0, end);

  const fields = {};
  let key = '';
  for (const line of block.split(/\r?\n/)) {
    const item = /^\s+-\s+(.*)$/.exec(line);
    if (item && key) {
      fields[key] = `${fields[key]}, ${item[1].trim()}`.replace(/^[, ]+/, '');
      continue;
    }
    const colon = line.indexOf(':');
    if (colon !== -1) {
      key = line.slice(0, colon).trim();
      fields[key] = line
        .slice(colon + 1)
        .trim()
        .replace(/^[[\]]+|[[\]]+$/g, '');
    }
  }
  return fields;
}

/** The note with its front matter removed. */
export function body(text) {
  if (!text.startsWith('---')) return text;
  const after = text.slice(text.indexOf('---\n') + 4);
  const end = after.indexOf('\n---');
  return end === -1 ? text : after.slice(end + 4);
}

/** Collapse whitespace and cut to MAX_DESC on a word boundary. */
export function truncate(text) {
  const collapsed = text.split(/\s+/).filter(Boolean).join(' ');
  if (collapsed.length <= MAX_DESC) return collapsed;
  return `${collapsed.slice(0, MAX_DESC).replace(/\s+\S*$/, '')}…`;
}

/**
 * One line saying what a note covers.
 *
 * A `summary:` field wins: the SessionEnd hook writes one, and anything hand-written is
 * better than anything inferred. Otherwise take the first line of real prose. Two fallbacks
 * follow, in order: a cross-reference line, then the note's own headings and bullets joined
 * together. Only a note with no readable text at all returns `''`.
 */
export function describe(text) {
  const summary = frontMatter(text).summary ?? '';
  if (summary) return truncate(summary);

  let pointer = '';
  const outline = [];
  for (const quoted of body(text).split(/\r?\n/)) {
    const raw = unquote(quoted);
    if (NOISE.test(raw)) {
      if (OUTLINE.test(raw) && outline.length < OUTLINE_MAX) {
        const item = stripMarkdown(raw.replace(OUTLINE, ''));
        if (item) outline.push(item);
      }
      continue;
    }
    const line = stripMarkdown(raw);
    if (line.length < MIN_DESC) continue;
    if (CROSS_REFERENCE.test(raw) && line.length < CROSS_REFERENCE_MAX) {
      pointer = pointer || line; // Hold as fallback. Keep looking for real prose.
      continue;
    }
    return truncate(line);
  }
  return truncate(pointer || outline.join(OUTLINE_JOIN));
}

/** Every indexable note in the vault, ordered by path. */
export function notes(vault) {
  let entries;
  try {
    entries = readdirSync(vault, { recursive: true, withFileTypes: true });
  } catch {
    return [];
  }
  const found = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.md')) continue;
    if (SKIP_NAMES.has(entry.name)) continue;
    // `parentPath` is absolute; the directories between it and the vault root are what the
    // skip list is about.
    const within = resolve(entry.parentPath ?? entry.path ?? vault).slice(resolve(vault).length);
    if (within.split(sep).some((part) => SKIP_DIRS.has(part))) continue;
    found.push(join(within.replace(/^[\\/]/, ''), entry.name).replaceAll('\\', '/'));
  }
  return found.sort((a, b) => (a.toLowerCase() < b.toLowerCase() ? -1 : 1));
}

/** Render the whole index. Pure: reads notes, returns Markdown, writes nothing. */
export function build(vault) {
  const rows = [];
  for (const relative of notes(vault)) {
    let text;
    try {
      text = readFileSync(join(vault, relative), 'utf8');
    } catch {
      continue; // A note we cannot read is one row missing, not a failed index.
    }
    rows.push([relative, frontMatter(text).tags ?? '', describe(text)]);
  }

  const lines = [
    '---',
    'tags: [vault-index]',
    '---',
    '',
    '# Vault index',
    '',
    'Generated - do not edit. Rebuilt by the shared SessionEnd hook when a session ends in',
    'any repo that installs the harness. Notes written from anywhere else appear here only',
    'after the next such session. Run the indexer directly to refresh sooner.',
    '',
    'One row per note. Read this first, then open only the notes whose row matches.',
    'Reading every note to answer one question is the cost this file exists to avoid.',
    '',
    'Paths are relative to the vault root, so a row is directly readable. The session notes',
    'under `Project Learnings/` carry a second, richer index in `Project Learnings/_INDEX.md`,',
    'which adds their date and originating project.',
    '',
    `${rows.length} notes.`,
    '',
    '| Note | Tags | What it covers |',
    '| --- | --- | --- |',
  ];
  for (const [path, tags, description] of rows) {
    const cells = [`\`${path}\``, tags, description];
    lines.push(`| ${cells.map((cell) => cell.replaceAll('|', '\\|')).join(' | ')} |`);
  }
  return `${lines.join('\n')}\n`;
}

/** The vault root, or `''` when none is configured or the path is not a directory. */
export function vaultDir(environment = process.env) {
  const raw = (environment.OBSIDIAN_VAULT_DIRECTORY ?? '').trim();
  if (!raw || !isAbsolute(raw)) return '';
  try {
    return statSync(raw).isDirectory() ? raw : '';
  } catch {
    return '';
  }
}

/**
 * Rebuild the index. Returns the file written, or `''` if there was nothing to do.
 *
 * Never throws. This runs from a SessionEnd hook, and a vault that cannot be indexed is not
 * a reason to interfere with ending a session.
 */
export function refresh() {
  const vault = vaultDir();
  if (!vault) return '';
  const target = join(vault, INDEX_NAME);
  try {
    // The written text uses `\n` throughout and is not translated on the way out. Several
    // repos share one vault; if each writer used its platform's line ending the file would
    // be rewritten end to end every time the platform changed.
    writeFileSync(target, build(vault), 'utf8');
  } catch (error) {
    process.stderr.write(`vault_index: could not write ${target} (${error.message})\n`);
    return '';
  }
  return target;
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === resolve(fileURLToPath(import.meta.url))) {
  const written = refresh();
  if (written) process.stderr.write(`vault_index: wrote ${written}\n`);
  else process.stderr.write('vault_index: no vault configured - set OBSIDIAN_VAULT_DIRECTORY\n');
  process.exit(0);
}
