#!/usr/bin/env node
/**
 * SessionEnd hook: distil what the session learned the hard way into the second brain.
 *
 * A hook is a shell command with no model of its own, so it cannot judge which mistakes
 * taught something. This one does the deterministic half — locate the vault, extract the
 * transcript, gather git context, write the file — and shells out to a headless
 * `claude -p` for the one part that needs judgement.
 *
 * **It writes nothing when the session taught nothing.** An empty note is worse than no
 * note: it dilutes the directory every later search has to sift. The distiller is told to
 * emit a single sentinel when there is no real lesson, and that path exits silently.
 *
 * **Every run appends one outcome line to `_hook.log`** beside the notes. SessionEnd stderr
 * is invisible, so before the log a failed distillation looked identical to a session that
 * taught nothing. Diagnosis: no line for a session means SessionEnd never fired — a closed
 * terminal window skips it; a `failed:` line names the reason.
 *
 * **It writes notes and it indexes them.** Until phase 5 those were two repos' jobs: the
 * frontend harness wrote notes and `python-harness` owned both indexes, so a note written
 * from the frontend repo stayed invisible to search until a session happened to end in the
 * other one. That was defect 7, and it survived as documented prose because there was
 * nowhere for shared machinery to live. There is now, and one implementation cannot be
 * installed in one repo and not the other.
 *
 * Configure with `OBSIDIAN_VAULT_DIRECTORY` (absolute path to the vault root). Unset means
 * disabled, which is the right default for a harness other people clone — nobody inherits a
 * path to somebody else's vault. Set it in **user** settings, never in a repo's committed
 * settings file.
 *
 * | Variable | Effect |
 * | --- | --- |
 * | `OBSIDIAN_VAULT_DIRECTORY` | Vault root. Notes go in `Project Learnings`. |
 * | `CLAUDE_LEARNINGS_OFF=1` | Disable without unsetting the directory. |
 * | `CLAUDE_LEARNINGS_MODEL` | Model for the distillation. Default `sonnet`. |
 * | `CLAUDE_LEARNINGS_SKIP=1` | Recursion guard; set on the child, never set by hand. |
 *
 * **Two recursion guards, because one is not enough.** The `claude -p` we spawn is a full
 * session and fires its own SessionEnd when it finishes. Its transcript contains the parent
 * transcript verbatim, so distilling it produces a near-copy of the parent's note under a
 * new session id. One shared vault collected four such copies while both implementations
 * carried the `CLAUDE_LEARNINGS_SKIP` guard — proof that an environment variable crossing a
 * process boundary we do not control is a guard that can leak silently. So the env guard is
 * backed by a second, propagation-free one: the payload we send starts with
 * `DISTILLER_MARKER`, and a transcript whose head carries it is never distilled again.
 *
 * `LEGACY_OPENINGS` recognises the markers the two predecessor implementations sent. Their
 * transcripts are still on disk and are exactly the ones the recovery script would reach, so
 * dropping them would re-open a bug that has already been fixed once.
 *
 * The distiller also runs **outside the repository**, in `DISTILLER_HOME`. `claude -p` files
 * its transcript under the project directory of its cwd, so a distiller started in the repo
 * drops a child transcript beside the real ones and loads that repo's own hooks into the
 * child. A fixed neutral directory does neither.
 *
 * **The note is keyed on the session, not on the clock.** One session can end more than once
 * — resume it, and SessionEnd fires again on a later date. A dated filename made that a
 * second note about the same session. An existing note for the same session is rewritten in
 * place, keeping its original name and `date:` so links and ordering hold.
 *
 * **A rewrite carries the earlier note forward.** The rewrite replaces the file, and the
 * transcript we send holds only the last `MAX_TRANSCRIPT_CHARS`. So the note this session
 * already has goes back to the distiller under `PRIOR_NOTE_HEADER`, and the distiller is
 * told to keep every learning in it. Without that, deduplication trades duplicate notes for
 * silent loss — a resumed session overwrites its own first lesson with its second.
 *
 * **A note whose body already exists verbatim is not written.** That is the last net under
 * both guards, and it costs one directory scan on the write path only.
 *
 * Never blocks. Every failure path exits 0 — a second brain that cannot be written is not a
 * reason to interfere with ending a session.
 */

import { spawnSync } from 'node:child_process';
import {
  appendFileSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import { output, readPayload } from './lib.mjs';
import * as vaultIndex from './vault_index.mjs';

const MODEL = process.env.CLAUDE_LEARNINGS_MODEL ?? 'sonnet';
const NO_LEARNINGS = 'NO_LEARNINGS';
const SUMMARY_PREFIX = 'SUMMARY:';

/** The learnings folder's own index. `_` sorts it to the top and reads as machinery. */
export const INDEX_NAME = '_INDEX.md';

/**
 * The distiller reads text only, so it needs no tools. Cap what we send: transcripts run to
 * megabytes, and the tail is where fixes land.
 */
const MAX_TRANSCRIPT_CHARS = 60_000;
const DISTILL_TIMEOUT = 240_000;

/** Below this a transcript is too short to have taught anything. */
const MIN_TRANSCRIPT_CHARS = 500;

/**
 * First line of everything we send to the distiller, so a distillation run is identifiable
 * from its own transcript alone. Treat it as a wire format, not a comment.
 */
export const DISTILLER_MARKER = 'SESSION-LEARNINGS-DISTILLER-V1';

/**
 * Openings the predecessor implementations sent. Their transcripts are on disk and would
 * otherwise be distilled into copies of notes the vault already holds.
 */
export const LEGACY_OPENINGS = [
  '[claude-learnings-distiller]',
  "You are writing a note for an engineer's personal knowledge base",
];

/** Every opening that identifies one of our own runs, current and historical. */
export const DISTILLER_OPENINGS = [DISTILLER_MARKER, ...LEGACY_OPENINGS];

/**
 * How many transcript lines the marker check reads before it gives up. The opening prompt is
 * within the first few entries of any transcript, and a real one runs to megabytes.
 */
const MARKER_MAX_LINES = 50;

/** Where the distiller runs. See the note on `DISTILLER_HOME` in the module docstring. */
const DISTILLER_HOME = join(homedir(), '.claude', 'learnings-distiller');

/** Introduces the note this session already has, when the session is distilled again. */
const PRIOR_NOTE_HEADER = '=== NOTE ALREADY WRITTEN FOR THIS SESSION ===';

const PROMPT = `${DISTILLER_MARKER}

The line above marks this call for the harness. Ignore it. Do not repeat it in your reply.

You are writing a note for an engineer's personal knowledge base, recording
what a coding session taught them. Someone will read this months from now with no memory
of the session.

Below is a transcript, plus the git context of what changed.

Extract only **technical learnings that came from a mistake, a wrong assumption, or
friction that was then resolved**. The value is in what was believed, why it was wrong,
and what turned out to be true.

Ignore: what the session accomplished, features shipped, files touched, anything that
reads like a changelog. That is recoverable from git. A learning is not.

Split the findings into exactly these two sections:

## Implementation learnings

Low-level and concrete. Tool flags and their real behaviour, API and config semantics,
environment, platform and browser quirks, error messages and what actually causes them,
commands that do not do what their name implies.

## Architecture & design learnings

Higher-level and transferable. Why a structure resisted a change, where a boundary was
drawn wrongly, a design tension and how it resolved, a rule that turned out to have an
exception, a process or workflow that broke down and why.

Rules:
- Every entry states the wrong belief and the correction. "X, not Y — because Z."
- Be specific. Name the tool, flag, file or concept. A vague lesson teaches nothing.
- Omit a section entirely if nothing qualifies. Do not pad it.
- If the session contained no genuine learning of either kind — no mistakes, only
  routine work — reply with exactly \`${NO_LEARNINGS}\` and nothing else.

If a \`${PRIOR_NOTE_HEADER}\` section follows, it is your own earlier note for this same
session, written from an earlier part of it. Your reply replaces that file. Keep every
learning it holds, and add what the transcript below teaches on top. State each learning
once. The transcript you receive is only the most recent part of the session, so the
earlier note is the only record of what came before it.

Start your reply with one line, exactly in this form:

${SUMMARY_PREFIX} <one sentence naming the topics covered, under 25 words>

That line is the only thing a search reads before deciding whether to open this note, so
name the concrete subjects — the tool, the system, the concept. Write "mypy file scope and
stacked-PR merge targets", not "various tooling lessons".

Then a blank line, then the first \`##\` heading. No other preamble, no closing summary.

Output GitHub-flavoured Markdown. Do not include front matter; it is added for you.`;

/** Return the Project Learnings directory from the configured Obsidian vault root. */
export function learningsDirectory(environment = process.env) {
  const vault = (environment.OBSIDIAN_VAULT_DIRECTORY ?? '').trim();
  return vault ? join(vault, 'Project Learnings') : '';
}

/** The session id as it appears in a note filename. */
export function shortId(sessionId) {
  return (
    String(sessionId ?? '')
      .replace(/[^a-zA-Z0-9]/g, '')
      .slice(0, 8) || 'session'
  );
}

/**
 * Append one outcome line to `_hook.log` beside the notes. SessionEnd stderr goes nowhere a
 * user looks, so without this a failed distillation and a session that taught nothing are
 * indistinguishable — both leave no note. Never blocks.
 */
export function logOutcome(directory, project, outcome) {
  const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
  try {
    appendFileSync(join(directory, '_hook.log'), `${stamp} ${project}: ${outcome}\n`, 'utf8');
  } catch {
    // A log that cannot be written is not a reason to interfere with ending a session.
  }
}

/** Branch, recent commits and dirty files — the facts a model should not have to infer. */
export function gitContext(cwd) {
  const branch = output('git', ['rev-parse', '--abbrev-ref', 'HEAD'], { cwd }) || '(unknown)';
  const log = output('git', ['log', '--oneline', '-15'], { cwd });
  const status = output('git', ['status', '--porcelain'], { cwd });
  const parts = [`Branch: ${branch}`];
  if (log) parts.push(`Recent commits:\n${log}`);
  if (status) parts.push(`Uncommitted:\n${status}`);
  return parts.join('\n\n');
}

/** Pull readable text out of one content block, whatever shape it arrived in. */
function extractText(block) {
  if (typeof block === 'string') return block;
  if (block && typeof block === 'object') {
    if (
      ['text', 'input_text', 'output_text'].includes(block.type) &&
      typeof block.text === 'string'
    )
      return block.text;
    if (block.type === 'tool_use' && typeof block.name === 'string') return `[tool: ${block.name}]`;
  }
  return '';
}

/** One transcript entry's message, whichever key the harness filed it under. */
function messageOf(entry) {
  if (entry?.message && typeof entry.message === 'object') return entry.message;
  const payload = entry?.payload;
  return payload && typeof payload === 'object' && 'role' in payload ? payload : null;
}

/** The transcript file as written, or `''` when it cannot be read. */
export function readRaw(path) {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    return '';
  }
}

/**
 * Text of the transcript's first user message, or `''` when it has none. Bounded by
 * `MARKER_MAX_LINES`, because the opening prompt sits in the first few entries.
 */
export function firstUserMessage(raw) {
  for (const line of raw.split(/\r?\n/, MARKER_MAX_LINES)) {
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    const message = messageOf(entry);
    if (!message || message.role !== 'user') continue;
    const content = message.content;
    const blocks = Array.isArray(content) ? content : [content];
    return blocks.map(extractText).filter(Boolean).join(' ').trim();
  }
  return '';
}

/**
 * True when this transcript is a distillation run of ours.
 *
 * The test is a prefix test on the first user message, not a search of the file. These repos
 * edit this hook, so a marker appears as ordinary text — in a tool result, a diff, or a
 * pasted quotation — in real sessions about it. Searching for it anywhere would make those
 * sessions skip their own note, and a skipped note looks exactly like a session that taught
 * nothing. Only a session whose opening prompt *is* a distillation prompt counts.
 */
export function isDistillerTranscript(raw) {
  const first = firstUserMessage(raw);
  return DISTILLER_OPENINGS.some((opening) => first.startsWith(opening));
}

/** Flatten the transcript JSONL into plain text, keeping the most recent tail. */
export function flatten(raw) {
  const lines = [];
  for (const line of raw.split(/\r?\n/)) {
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    const message = messageOf(entry);
    if (!message) continue;
    const content = message.content;
    const blocks = Array.isArray(content) ? content : [content];
    const text = blocks.map(extractText).filter(Boolean).join(' ').trim();
    if (text) lines.push(`${message.role ?? '?'}: ${text}`);
  }
  const joined = lines.join('\n');
  return joined.length > MAX_TRANSCRIPT_CHARS ? joined.slice(-MAX_TRANSCRIPT_CHARS) : joined;
}

/** One flat `key: value` from the front matter, or `''`. */
export function frontMatterValue(text, key) {
  return vaultIndex.frontMatter(text)[key] ?? '';
}

/** The note with its front matter and heading removed, normalised for comparison. */
export function noteBody(text) {
  return vaultIndex
    .body(text)
    .replace(/^\s*#[^\n]*\n/, '')
    .trim();
}

/**
 * Every note already in the directory, as `{ path, key, session, date, body }`. Read once and
 * reused by every dedupe check, so the write path costs one scan rather than three.
 *
 * `key` is the short session id: from the `session:` field when a note has one, and from the
 * filename suffix when it does not. Notes written before that field existed carry only the
 * suffix, and they are precisely the ones the recovery script would reach twice.
 */
export function readNotes(directory) {
  let names;
  try {
    names = readdirSync(directory);
  } catch {
    return [];
  }
  const notes = [];
  for (const name of names) {
    if (!name.endsWith('.md') || name.startsWith('_')) continue;
    const path = join(directory, name);
    let text;
    try {
      text = readFileSync(path, 'utf8');
    } catch {
      continue;
    }
    const session = frontMatterValue(text, 'session');
    notes.push({
      path,
      session,
      key: session ? shortId(session) : name.replace(/\.md$/, '').split(' ').at(-1),
      date: frontMatterValue(text, 'date'),
      body: noteBody(text),
    });
  }
  return notes;
}

/** The note this session already has, or `undefined`. */
export function existingNote(notes, sessionId) {
  if (!sessionId) return undefined;
  const key = shortId(sessionId);
  return notes.find((note) => note.key === key);
}

/**
 * The body of the note this session already has, or `''`.
 *
 * It goes back to the distiller, so a rewrite adds to the earlier note instead of replacing
 * it. Front matter is stripped: the distiller is asked for a body, and handing back the
 * `summary:` line it wrote invites it to reproduce that line inside the note.
 */
export function priorBody(notes, sessionId) {
  return existingNote(notes, sessionId)?.body ?? '';
}

/**
 * Where this note belongs and whether it is worth writing.
 *
 * Returns `{ target, date, skip }`. `skip` names the reason when an existing note already
 * carries this content — the caller logs it and writes nothing.
 */
export function placeNote(notes, body, sessionId, fallbackPath) {
  const mine = existingNote(notes, sessionId);
  if (mine) {
    // The same session ending twice — resumed, or ended once per window. One note.
    if (mine.body === body) {
      return { target: mine.path, date: mine.date, skip: `unchanged: ${basename(mine.path)}` };
    }
    return { target: mine.path, date: mine.date, skip: null };
  }
  const twin = notes.find((note) => note.body === body);
  if (twin) {
    return { target: twin.path, date: twin.date, skip: `duplicate of ${basename(twin.path)}` };
  }
  return { target: fallbackPath, date: '', skip: null };
}

/** Dated, project-scoped, session-suffixed so two sessions a day cannot collide. */
export function notePath(directory, project, sessionId) {
  const stamp = new Date().toISOString().slice(0, 10);
  return join(directory, `${stamp} ${project} ${shortId(sessionId)}.md`);
}

/** Separate the `SUMMARY:` line from the note body. A missing prefix is not fatal. */
export function splitSummary(text) {
  const newline = text.indexOf('\n');
  const first = (newline === -1 ? text : text.slice(0, newline)).trim();
  if (!first.startsWith(SUMMARY_PREFIX)) return ['', text];
  const rest = newline === -1 ? '' : text.slice(newline + 1).replace(/^\n+/, '');
  return [first.slice(SUMMARY_PREFIX.length).trim(), rest];
}

/**
 * Ask a headless Claude for the lessons. Returns `{ text, failure }`: empty text with a null
 * failure means the session taught nothing; a non-null failure names what broke, so the log
 * can tell the two apart.
 */
export function distil(transcript, context, prior = '') {
  const earlier = prior.trim() ? `\n\n${PRIOR_NOTE_HEADER}\n${prior.trim()}` : '';
  const payload = `${PROMPT}\n\n=== GIT CONTEXT ===\n${context}${earlier}\n\n=== TRANSCRIPT ===\n${transcript}`;

  // Run outside the repo so the child files its own transcript where nothing here scans, and
  // loads none of the repo's hooks. A directory we cannot create is not a reason to skip the
  // note: fall back to the inherited cwd, where both guards still hold.
  let home;
  try {
    mkdirSync(DISTILLER_HOME, { recursive: true });
    home = DISTILLER_HOME;
  } catch {
    home = undefined;
  }

  const result = spawnSync('claude', ['-p', '--model', MODEL], {
    input: payload,
    cwd: home,
    // Explicit, not the default Buffer: decoding with the locale codec (cp1252 on Windows)
    // turns the model's em-dashes into mojibake in the written note.
    encoding: 'utf8',
    timeout: DISTILL_TIMEOUT,
    shell: process.platform === 'win32',
    windowsHide: true,
    env: { ...process.env, CLAUDE_LEARNINGS_SKIP: '1' },
  });

  if (result.error) return { text: '', failure: `could not run claude (${result.error.message})` };
  if (result.status !== 0) return { text: '', failure: `claude exited ${result.status}` };
  const text = (result.stdout ?? '').trim();
  if (!text) return { text: '', failure: 'distiller returned empty output' };
  if (text.startsWith(NO_LEARNINGS)) return { text: '', failure: null };
  return { text, failure: null };
}

/**
 * Regenerate the learnings folder's `_INDEX.md` from every note's front matter.
 *
 * Rebuilt rather than appended. An append-only index drifts the moment a note is edited,
 * renamed or deleted by hand, and a stale index is worse than none — a search that trusts it
 * silently misses notes.
 */
export function rebuildIndex(directory) {
  let names;
  try {
    names = readdirSync(directory);
  } catch {
    return '';
  }
  const rows = [];
  for (const name of names
    .filter((n) => n.endsWith('.md') && n !== INDEX_NAME)
    .sort()
    .reverse()) {
    let text;
    try {
      text = readFileSync(join(directory, name), 'utf8');
    } catch {
      continue;
    }
    const fields = vaultIndex.frontMatter(text);
    if (Object.keys(fields).length === 0) continue;
    rows.push([
      fields.date ?? '',
      fields.project ?? '',
      fields.summary ?? '',
      name.replace(/\.md$/, ''),
    ]);
  }

  const lines = [
    '---',
    'tags: [project-learnings-index]',
    '---',
    '',
    '# Learnings index',
    '',
    'Generated by the `SessionEnd` hook. Do not edit: it is rebuilt on every write.',
    '',
    'Search this file first, then open only the notes whose summary matches. Reading',
    'every note to answer one question is the cost this index exists to avoid.',
    '',
    `${rows.length} notes.`,
    '',
    '| Date | Project | Summary | Note |',
    '| --- | --- | --- | --- |',
  ];
  for (const [date, project, summary, stem] of rows) {
    lines.push(`| ${date} | ${project} | ${summary.replaceAll('|', '\\|')} | [[${stem}]] |`);
  }

  const target = join(directory, INDEX_NAME);
  try {
    writeFileSync(target, `${lines.join('\n')}\n`, 'utf8');
  } catch (error) {
    process.stderr.write(`session_learnings: could not write index (${error.message})\n`);
    return '';
  }
  return target;
}

/**
 * Distil one transcript into a note. Returns `{ target, outcome }`; `target` is `''` when
 * nothing was written and `outcome` is the line for the log.
 *
 * Shared by the SessionEnd path and the recovery path in `distil_backlog.mjs`, so the two
 * cannot drift on what a note is.
 */
export function distilTranscript({ transcriptPath, sessionId, cwd, directory }) {
  const raw = readRaw(transcriptPath);

  // The propagation-free guard: this one survives an environment that did not reach the
  // child, which is the failure that filled a vault with near-copies.
  if (isDistillerTranscript(raw)) {
    return { target: '', outcome: 'skipped: distiller session (transcript marker)' };
  }

  const transcript = flatten(raw);
  if (transcript.length < MIN_TRANSCRIPT_CHARS) {
    return { target: '', outcome: `skipped: transcript under ${MIN_TRANSCRIPT_CHARS} chars` };
  }

  // Read the vault before the distiller runs, not after: a rewrite has to send the earlier
  // note back, and the earlier note only exists in the vault.
  const notes = readNotes(directory);
  const project = basename(cwd) || 'session';

  const { text: distilled, failure } = distil(
    transcript,
    gitContext(cwd),
    priorBody(notes, sessionId),
  );
  if (failure) return { target: '', outcome: `failed: ${failure}` };
  if (!distilled) return { target: '', outcome: 'no learnings: the session taught nothing' };

  const [summary, text] = splitSummary(distilled);
  const body = text.trim();
  const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ');

  const { target, date, skip } = placeNote(
    notes,
    body,
    sessionId,
    notePath(directory, project, sessionId),
  );
  if (skip) return { target: '', outcome: `skipped: ${skip}` };

  // A rewrite keeps the note's original date, so it holds its place in the vault, and records
  // the later end in `updated:` instead.
  const first = date || stamp;
  const front =
    '---\n' +
    `date: ${first}\n` +
    (date && date !== stamp ? `updated: ${stamp}\n` : '') +
    `project: ${project}\n` +
    `session: ${sessionId}\n` +
    `summary: ${summary}\n` +
    'tags: [project-learnings, session-retro]\n' +
    '---\n\n' +
    `# ${project} — session learnings (${first})\n\n`;

  try {
    writeFileSync(target, `${front}${body}\n`, 'utf8');
  } catch (error) {
    return { target: '', outcome: `failed: could not write note (${error.message})` };
  }
  return { target, outcome: `${date ? 'rewrote' : 'wrote'} ${basename(target)}` };
}

async function main() {
  if (process.env.CLAUDE_LEARNINGS_OFF === '1') return 0;

  const directory = learningsDirectory();
  if (!directory) return 0; // Not configured -> not this clone's business.

  const payload = await readPayload();
  if (!payload) return 0;

  const cwd = payload.cwd || process.cwd();
  const project = basename(cwd) || 'session';

  // Guard one: the environment variable we set on the child. Logged rather than silent,
  // because a missing line beside a distillation run is how a leaking guard shows itself.
  if (process.env.CLAUDE_LEARNINGS_SKIP === '1') {
    logOutcome(directory, project, 'skipped: distiller session (env guard)');
    return 0;
  }

  // Unconditional, and before every early return below: the vault gains hand-written notes
  // between sessions, and those need indexing even when this session distilled nothing.
  vaultIndex.refresh();

  try {
    if (!statSync(directory).isDirectory()) throw new Error('not a directory');
  } catch {
    process.stderr.write(`session_learnings: ${directory} is not a directory\n`);
    return 0;
  }

  const { target, outcome } = distilTranscript({
    transcriptPath: payload.transcript_path ?? '',
    sessionId: String(payload.session_id ?? ''),
    cwd,
    directory,
  });

  logOutcome(directory, project, outcome);
  if (!target) return 0;

  rebuildIndex(directory);
  process.stderr.write(`session_learnings: ${outcome}\n`);
  return 0;
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === resolve(fileURLToPath(import.meta.url))) {
  process.exit(await main());
}
