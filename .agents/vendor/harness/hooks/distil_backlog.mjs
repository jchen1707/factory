#!/usr/bin/env node
/**
 * Recover second-brain learnings from sessions that never fired SessionEnd.
 *
 * `session_learnings.mjs` runs only when a session ends cleanly. A terminal that is closed,
 * killed, or simply left open never distils, and the loss is silent — the transcript sits in
 * `~/.claude/projects/<project>/` with no note in the vault and nothing reporting the gap.
 * This script finds those transcripts and distils them through the same pipeline the hook
 * uses.
 *
 * Run it from the repo whose sessions you want to recover:
 *
 *     node <this file>          # dry run: list only
 *     node <this file> --run    # distil (spends tokens)
 *
 * Dry run is the default because each distillation is a `claude -p` call that costs real
 * tokens. `--limit` caps how many transcripts one invocation processes.
 *
 * `--audit` runs the opposite direction: it reads the vault and reports notes that should not
 * be there. Two writer bugs put duplicates in the vault before they were fixed, and fixing a
 * writer does not remove what it already wrote.
 *
 *     node <this file> --audit         # report only
 *     node <this file> --audit --run   # delete echo notes
 *
 * Two caveats, both accepted:
 *
 * - A session that is **still open** shows up in the backlog too — it has a transcript and no
 *   note. Distilling it writes a partial note. When the session later ends, SessionEnd
 *   overwrites that same note with the full one, on any date: the note is keyed on the
 *   session id, not on the day it ran.
 * - `gitContext` reads the repo as it is *now*, not as it was when the session ran. The
 *   transcript carries the real history; the git context is only framing.
 */

import { readdirSync, rmSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import * as learnings from './session_learnings.mjs';
import * as vaultIndex from './vault_index.mjs';

/**
 * The directory name Claude Code uses for a project's transcripts. Every character that is
 * not a letter or digit becomes `-`, so `C:\\Users\\x\\repo` and `/home/x/repo` both map the
 * way the harness maps them.
 */
export function projectSlug(cwd) {
  return String(cwd).replace(/[^A-Za-z0-9]/g, '-');
}

/** Where Claude Code keeps every project's transcripts. */
export function projectsRoot() {
  return join(homedir(), '.claude', 'projects');
}

/** Where Claude Code keeps this project's session transcripts. */
export function transcriptsDir(cwd) {
  return join(projectsRoot(), projectSlug(cwd));
}

function listFiles(directory, suffix) {
  try {
    return readdirSync(directory)
      .filter((name) => name.endsWith(suffix))
      .map((name) => join(directory, name));
  } catch {
    return [];
  }
}

/**
 * Transcripts worth distilling, newest first.
 *
 * Two exclusions. A session that already has a note needs no second one. A transcript written
 * by the distiller itself holds the prompt and the finished note, so distilling it copies a
 * note the vault already has under a new session id — the single largest source of duplicates
 * before this filter existed.
 */
export function backlog(transcripts, notes) {
  const missing = listFiles(transcripts, '.jsonl').filter((path) => {
    const id = path
      .split(/[\\/]/)
      .at(-1)
      .replace(/\.jsonl$/, '');
    if (learnings.existingNote(notes, id)) return false;
    return !learnings.isDistillerTranscript(learnings.readRaw(path));
  });
  return missing
    .map((path) => ({ path, mtime: statSync(path).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime)
    .map((entry) => entry.path);
}

/**
 * Short ids of every transcript the distiller wrote, across all projects.
 *
 * One vault serves several repos, so an echo note in it can come from any of them. Scanning
 * only the current project's transcripts leaves the rest unexplained, and an unexplained note
 * is one the audit has to leave alone.
 */
export function distillerIds(root) {
  const ids = new Set();
  let projects;
  try {
    projects = readdirSync(root, { withFileTypes: true }).filter((e) => e.isDirectory());
  } catch {
    return ids;
  }
  for (const project of projects) {
    for (const path of listFiles(join(root, project.name), '.jsonl')) {
      if (learnings.isDistillerTranscript(learnings.readRaw(path))) {
        ids.add(
          learnings.shortId(
            path
              .split(/[\\/]/)
              .at(-1)
              .replace(/\.jsonl$/, ''),
          ),
        );
      }
    }
  }
  return ids;
}

/** Every learnings note, grouped by the session that produced it, oldest first. */
export function notesBySession(notes) {
  const groups = new Map();
  for (const note of [...notes].sort((a, b) => (a.path < b.path ? -1 : 1))) {
    const list = groups.get(note.key) ?? [];
    list.push(note);
    groups.set(note.key, list);
  }
  return groups;
}

/**
 * Notes whose session id belongs to the distiller, not to a real session.
 *
 * Every distillation is itself a `claude -p` session, so it files a transcript holding the
 * prompt *and* the finished note. Distilling that transcript returned the note a second time
 * under the child's id. These notes are artifacts of the bug: the lesson in each one is
 * already in the vault under the session that truly learned it.
 */
export function echoNotes(notes, ids) {
  return [...notesBySession(notes)]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .filter(([key]) => ids.has(key))
    .map(([, group]) => group[0]);
}

/**
 * Sessions holding more than one note, because each write was dated afresh.
 *
 * Reported, never deleted. Both files are real distillations of one session, taken from
 * different parts of it, so which learnings survive a merge is a judgement the tool cannot
 * make. The write path stops new ones; these are the ones already written.
 */
export function splitSessions(notes) {
  return [...notesBySession(notes)]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .filter(([, group]) => group.length > 1);
}

function runAudit(directory, root, remove) {
  const notes = learnings.readNotes(directory);
  const echoes = echoNotes(notes, distillerIds(root));
  const splits = splitSessions(notes);

  if (echoes.length === 0 && splits.length === 0) {
    process.stdout.write('distil_backlog: no duplicate notes found\n');
    return 0;
  }

  if (echoes.length > 0) {
    process.stdout.write(
      `distil_backlog: ${echoes.length} note(s) written from the distiller's own transcript:\n`,
    );
    for (const note of echoes) process.stdout.write(`  ${note.path}\n`);
  }
  if (splits.length > 0) {
    process.stdout.write(
      `distil_backlog: ${splits.length} session(s) holding more than one note:\n`,
    );
    for (const [key, group] of splits) {
      process.stdout.write(`  ${key}: ${group.map((n) => n.path).join(', ')}\n`);
    }
    process.stdout.write(
      '  Merge these by hand. Each file distils a different part of one session.\n',
    );
  }

  if (!remove) {
    process.stdout.write("Re-run with --audit --run to delete the distiller's own notes.\n");
    return 0;
  }

  let removed = 0;
  for (const note of echoes) {
    try {
      rmSync(note.path);
    } catch (error) {
      process.stderr.write(`distil_backlog: could not delete ${note.path} (${error.message})\n`);
      continue;
    }
    process.stdout.write(`distil_backlog: deleted ${note.path}\n`);
    removed += 1;
  }
  if (removed > 0) {
    learnings.rebuildIndex(directory);
    vaultIndex.refresh();
  }
  process.stdout.write(`distil_backlog: deleted ${removed} of ${echoes.length}\n`);
  return 0;
}

function parseArgs(argv) {
  const args = { run: false, audit: false, limit: 5 };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--run') args.run = true;
    else if (argv[i] === '--audit') args.audit = true;
    else if (argv[i] === '--limit') {
      i += 1;
      args.limit = Number.parseInt(argv[i], 10);
    } else if (argv[i].startsWith('--limit=')) {
      args.limit = Number.parseInt(argv[i].slice('--limit='.length), 10);
    }
  }
  if (!Number.isFinite(args.limit) || args.limit < 0) args.limit = 0;
  return args;
}

function main(argv) {
  const args = parseArgs(argv);

  const raw = (process.env.OBSIDIAN_VAULT_DIRECTORY ?? '').trim();
  if (!raw) {
    process.stderr.write('distil_backlog: OBSIDIAN_VAULT_DIRECTORY is not set\n');
    return 1;
  }
  if (!isAbsolute(raw)) {
    process.stderr.write(`distil_backlog: ${raw} is not an absolute directory\n`);
    return 1;
  }
  const directory = learnings.learningsDirectory();
  try {
    if (!statSync(directory).isDirectory()) throw new Error('not a directory');
  } catch {
    process.stderr.write(`distil_backlog: ${directory} is not a directory\n`);
    return 1;
  }

  if (args.audit) return runAudit(directory, projectsRoot(), args.run);

  const cwd = process.cwd();
  const candidates = backlog(transcriptsDir(cwd), learnings.readNotes(directory)).slice(
    0,
    args.limit,
  );
  if (candidates.length === 0) {
    process.stdout.write('distil_backlog: no undistilled transcripts found\n');
    return 0;
  }

  if (!args.run) {
    process.stdout.write(
      `distil_backlog: ${candidates.length} transcript(s) without a note (dry run):\n`,
    );
    for (const path of candidates) process.stdout.write(`  ${path}\n`);
    process.stdout.write('Re-run with --run to distil them. Each one is a `claude -p` call.\n');
    return 0;
  }

  let wrote = 0;
  for (const path of candidates) {
    const sessionId = path
      .split(/[\\/]/)
      .at(-1)
      .replace(/\.jsonl$/, '');
    const { target, outcome } = learnings.distilTranscript({
      transcriptPath: path,
      sessionId,
      cwd,
      directory,
    });
    process.stdout.write(`distil_backlog: ${target || outcome}\n`);
    if (target) wrote += 1;
  }
  if (wrote > 0) {
    learnings.rebuildIndex(directory);
    vaultIndex.refresh();
  }
  process.stdout.write(`distil_backlog: ${wrote} of ${candidates.length} produced a note\n`);
  return 0;
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === resolve(fileURLToPath(import.meta.url))) {
  process.exit(main(process.argv.slice(2)));
}
