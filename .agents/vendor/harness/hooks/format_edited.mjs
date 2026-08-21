#!/usr/bin/env node
/**
 * PostToolUse hook: format and autofix the file that was just edited.
 *
 * Deliberately non-blocking — it always exits 0. Formatting is a fixup, not a gate; the
 * gate is the Stop hook in `verify.mjs`. Keeping this advisory means a formatting hiccup
 * can never wedge a turn.
 *
 * **What runs is entirely the repo's declaration.** `harness.config.json` names, per
 * extension, the commands to run and the order to run them in; this file appends the edited
 * path to each and calls them. A repo that declares no formatter gets a hook that does
 * nothing, which is the correct behaviour and not a silent failure — nothing was claimed.
 *
 * Two properties both stacks arrived at independently, worth stating because a config is
 * easy to write without them:
 *
 * - **Format wider than the gate watches.** A repo whose format check covers Markdown and
 *   JSON should list those extensions here even though the Stop gate ignores them, or a doc
 *   edit skips formatting now and fails the gate later in a session that never touched it.
 * - **Never auto-remove an unused import.** This hook fires after every single edit, so in
 *   a batch it runs between the edit that adds an import and the edit that adds the
 *   import's first use. An autofix at that moment deletes the import and the next edit
 *   references an undefined name. `python-harness` spells that as
 *   `--unfixable F401` on its `ruff check --fix` entry, and pins the flag in its own suite;
 *   the equivalent in another toolchain belongs in that repo's config for the same reason.
 *
 * **The config is resolved from the edited file, not from the session.** In the monorepo
 * phase 6 scaffolds, `apps/api/src/x.py` wants ruff and `apps/web/src/x.tsx` wants Prettier,
 * and the root config knows neither. So each path finds the nearest `harness.config.json` at
 * or above it and runs that one's formatters, in that one's directory. In a single-app repo
 * the nearest config is the root's, which is what this always did.
 */

import { dirname, extname, isAbsolute, join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import { loadConfigs, readPayload, runArgv, shellSafe, toolPaths } from './lib.mjs';

const TIMEOUT = 120_000;

/**
 * Every command to run against `path`, in declaration order.
 *
 * Matching is on the lowercased extension, including the dot. A formatter entry with no
 * `match` list matches nothing: an entry meant to catch everything has to say so, because
 * an empty list silently meaning "all" is how a formatter ends up running against a
 * lockfile.
 */
export function commandsFor(formatters, path) {
  const extension = extname(path).toLowerCase();
  const commands = [];
  for (const entry of formatters) {
    if (!entry || !Array.isArray(entry.match) || !Array.isArray(entry.run)) continue;
    if (!entry.match.some((suffix) => String(suffix).toLowerCase() === extension)) continue;
    for (const argv of entry.run) {
      if (Array.isArray(argv) && argv.length > 0) commands.push([...argv, path]);
    }
  }
  return commands;
}

/**
 * The formatters that govern one edited path, and the absolute form to hand them.
 *
 * Absolute rather than as the harness spelled it: Claude reports a full path and Codex
 * reports one relative to the repository root, and a command running in an app's directory
 * would resolve the second against the wrong tree.
 */
export function formatPlan(raw, projectDir) {
  const path = isAbsolute(raw) ? raw : join(projectDir || process.cwd(), raw);
  const [config] = loadConfigs(dirname(path), projectDir);
  return { path, config: config ?? null };
}

async function main() {
  const payload = await readPayload();
  if (!payload) return 0;

  const cwd = payload.cwd ?? '';

  for (const raw of toolPaths(payload)) {
    const { path, config } = formatPlan(raw, cwd);
    if (!config) continue; // Nothing declares a formatter for this file.

    // On Windows these commands go through `cmd.exe`, so a path carrying a shell
    // metacharacter would be interpreted rather than passed. Skipping is safe: this hook is
    // advisory, and a commit-time formatter sees the file again.
    if (!shellSafe(path)) continue;

    for (const argv of commandsFor(config.hooks.formatters, path)) {
      runArgv(argv, { cwd: config.root, timeout: TIMEOUT });
    }
  }
  return 0;
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === resolve(fileURLToPath(import.meta.url))) {
  process.exit(await main());
}
