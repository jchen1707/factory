#!/usr/bin/env node
/**
 * Codex SessionEnd adapter: hand the distillation to a detached process and return.
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

const payload = await readPayload();
if (payload === null) process.exit(0);

const script = join(dirname(fileURLToPath(import.meta.url)), 'session_learnings.mjs');

try {
  const child = spawn(process.execPath, [script], {
    detached: true,
    stdio: ['pipe', 'ignore', 'ignore'],
    windowsHide: true,
  });
  child.on('error', () => process.exit(0));
  child.stdin.on('error', () => {}); // A child that died before reading is not our problem.
  child.stdin.end(JSON.stringify(payload));
  child.unref();
} catch {
  // Nothing a hook can usefully do about a process it could not start.
}

process.exit(0);
