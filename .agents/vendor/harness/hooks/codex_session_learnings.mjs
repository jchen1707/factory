#!/usr/bin/env node
/**
 * Nonblocking SessionEnd adapter: hand the distillation to a detached process and return.
 *
 * `session_learnings.mjs` shells out to a headless `claude -p` and can run for minutes.
 * Claude Code allows that — its SessionEnd hook has a 300-second budget. Codex gives the
 * hook **three seconds**, so running the distiller inline there means it is killed every
 * time, and a killed distiller looks exactly like a session that taught nothing.
 *
 * So this adapter does the only thing that fits in three seconds: forward the payload to a
 * detached child in a new session, and exit. The child outlives Codex's timeout and the
 * terminating session both, and writes the note on its own schedule.
 *
 * Never blocks. Every failure path exits 0 — a second brain that cannot be written is not a
 * reason to interfere with ending a session.
 */

import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import { readPayload } from './lib.mjs';
import { canonicalProject, learningsDirectory, logOutcome } from './session_learnings.mjs';
import { mkdirSync } from 'node:fs';
import { vaultDir } from './vault_index.mjs';

const payload = await readPayload();
if (payload === null) process.exit(0);
if (process.env.CLAUDE_LEARNINGS_OFF === '1' || process.env.CLAUDE_LEARNINGS_SKIP === '1')
  process.exit(0);
const directory = learningsDirectory();
if (!directory) {
  process.stderr.write('session_learnings: unavailable: OBSIDIAN_VAULT_DIRECTORY not configured\n');
  process.exit(0);
}
if (!vaultDir()) {
  process.stderr.write('session_learnings: failed: configured vault unavailable\n');
  process.exit(0);
}
const project = canonicalProject(payload.cwd || process.cwd());
try {
  mkdirSync(directory, { recursive: true });
} catch {
  process.stderr.write('session_learnings: failed: learnings directory unavailable\n');
  process.exit(0);
}
logOutcome(
  directory,
  project,
  `queued: session ${String(payload.session_id ?? 'unknown').replace(/[^a-zA-Z0-9-]/g, '')}`,
);

payload.runtime = process.argv.includes('--claude') ? 'claude' : 'codex';

const script = join(dirname(fileURLToPath(import.meta.url)), 'session_learnings.mjs');

try {
  const child = spawn(process.execPath, [script], {
    detached: true,
    stdio: ['pipe', 'ignore', 'ignore'],
    windowsHide: true,
  });
  child.on('error', () => {
    logOutcome(directory, project, 'failed: capture worker could not start');
    process.exit(0);
  });
  child.stdin.on('error', () => {}); // A child that died before reading is not our problem.
  child.stdin.end(JSON.stringify(payload));
  child.unref();
} catch {
  logOutcome(directory, project, 'failed: capture worker could not start');
}

process.exit(0);
