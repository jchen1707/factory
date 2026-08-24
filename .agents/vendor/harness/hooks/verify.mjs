#!/usr/bin/env node
/**
 * Stop hook: refuse to end the turn while the Definition of Done is failing.
 *
 * Smart by design — the gates only run when the turn touched something that can actually
 * make them fail. That is the line: **code the gates check, plus the config that defines
 * the gates**. Prose, plans and docs still end freely, so writing work never burns toward
 * the 8-consecutive-block override Claude Code applies to Stop hooks.
 *
 * Which paths qualify is the consuming repo's declaration, in `harness.config.json` under
 * `hooks.gatedPaths`, `hooks.gatedFiles` and `hooks.gatedExtensions`. Both stacks arrived at
 * the same three groups by different routes, and the doctrine is worth restating because a
 * config is easy to narrow by accident:
 *
 * - The application and its tests.
 * - **The hooks themselves.** They are code the gates check, so a broken edit here fails
 *   the same gates as application code. Leaving them out meant the enforcement layer was
 *   the one thing the walk-away gate could not catch. Since phase 5 they are vendored
 *   rather than repo-owned, so what a stack gates is its own wiring — its settings file and
 *   its Codex adapter — plus the vendored tree, which its freshness check also covers.
 * - **The tool config that defines the gates.** A change to it can break every gate at once
 *   while touching no application code.
 *
 * The tradeoff this balances: widening the filter costs some override budget on config
 * work, but the excluded set that motivated the original narrow filter — Markdown, plans,
 * docs — is still excluded, and those are what sessions actually churn on.
 *
 * Convergence matters: every gate here is one an agent can actually fix. A check that can
 * never pass just wastes 8 turns of tokens before being overridden anyway.
 *
 * **Caveats are not printed here.** A gate's `caveat` names how it passes without having
 * checked anything, and the natural place to say so is beside a green result — but a Stop
 * hook that exits 0 has its stderr discarded, so printing them here reaches nobody. The
 * `/verify` skill prints them, because a human asked it a question and will read the
 * answer. What this hook adds instead is the other half: when it blocks, it names the
 * declared gates it did **not** run, so a passing subset is never mistaken for the whole
 * Definition of Done.
 *
 * **In a monorepo the gates dispatch by changed path.** A root config that names `apps`
 * delegates: each app declares its own Definition of Done, and a turn runs the gates of the
 * apps it actually touched. Running everything instead would make the monorepo cost what
 * split repos cost — a Python test suite on a CSS change — and running one app's gates from
 * the root would run them against the wrong tree. Which paths belong to which app needs no
 * new key: an app's own `gatedPaths` already says, and a path *outside* the app that it
 * names anyway — a shared contract package — is how both apps come to run when the contract
 * between them changed.
 *
 * Escape hatch: set `HARNESS_SKIP_VERIFY=1` to disable for a session. The legacy
 * `CLAUDE_SKIP_VERIFY` name remains supported — the gate is not Claude's, and naming it
 * after one harness is how the same escape hatch ended up with two names in two repos.
 *
 * **`claude --bare` disables this hook, and more.** Its own help says: "Minimal mode: skip
 * hooks, LSP, plugin sync, attribution, auto-memory, background prefetches, keychain reads,
 * and CLAUDE.md auto-discovery." So a `--bare` session has no Stop gate, no plugin — which
 * is to say no layer A at all where the plugin is the carrier — and no instruction file. It
 * is a claude flag, not a sandbox one; `sbx` has no such flag and does not skip hooks. An
 * unattended poller must not use `--bare` and expect the walk-away property, and that is
 * worth stating because the failure is the quiet kind: everything runs, nothing enforces.
 */

import { join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import { loadConfig, readPayload, repoRelative, run, runArgv, tail } from './lib.mjs';

const MAX_LINES = 40;
const GATE_TIMEOUT = 540_000;

/**
 * Gate kinds a Stop hook runs. `build` is in because it is the only gate that sees the
 * deployment target — a top-level `await` passes a typecheck and still fails a build
 * against es2020. `e2e` and `integration` are out: they need a browser or a container, and
 * a gate that cannot start on a given machine converts a walk-away gate into a wedged turn.
 */
export const STOP_KINDS = new Set(['lint', 'format', 'types', 'test', 'build']);

/**
 * Extract the path from one `git status --porcelain` line.
 *
 * Format is two status chars, a space, then the path. Renames and copies read
 * `old -> new`; the destination is the one that exists on disk. Paths containing special
 * characters are quoted.
 */
export function porcelainPath(line) {
  const entry = line.slice(3).trim();
  const arrow = entry.lastIndexOf(' -> ');
  const path = arrow === -1 ? entry : entry.slice(arrow + 4);
  return path.trim().replace(/^"|"$/g, '');
}

/**
 * One declared path, as `git status` would spell it.
 *
 * `git status --porcelain` reports every path **relative to the repository root**, whatever
 * directory it ran in — porcelain output is stable by definition, and `status.relativePaths`
 * does not apply to it. A config declares its paths relative to itself, because that is the
 * only spelling that makes sense next to the code. So one of the two has to be converted,
 * and it is this one: `harness.config.json` in `apps/api` is `apps/api/harness.config.json`,
 * and `../../harness.config.json` is the root's.
 *
 * Getting this wrong is silent in the worst direction. A `gatedFiles` entry that never
 * matches does not error; the gate simply stops noticing that file.
 */
export function declaredPath(prefix, entry) {
  const parts = [];
  for (const part of `${prefix}/${entry}`.split('/')) {
    if (part === '.' || part === '') continue;
    if (part === '..' && parts.length > 0 && parts.at(-1) !== '..') parts.pop();
    else parts.push(part);
  }
  return parts.join('/');
}

/**
 * True if `path` is something the gates would read.
 *
 * `prefix` is the config's own directory relative to the repository root — empty in a
 * single-config repo, `apps/web` for an app in a monorepo.
 */
export function isGated(path, hooks, prefix = '') {
  if (hooks.gatedFiles.some((entry) => declaredPath(prefix, entry) === path)) return true;
  return hooks.gatedExtensions.some((extension) => path.endsWith(extension));
}

/**
 * True if the turn touched gated source or the tool config that gates it.
 *
 * The pathspec names every gated location explicitly rather than asking git about the whole
 * tree: a repo-wide `git status` in a large checkout is slow enough to notice on every
 * turn, and narrowing it here is what makes the filter affordable.
 */
export function gatedChange(cwd, hooks, prefix = '') {
  const pathspec = [...hooks.gatedPaths, ...hooks.gatedFiles];
  if (pathspec.length === 0) return false; // Nothing declared -> nothing to gate.

  // The pathspec is resolved by git against `cwd`, so it is declared as written. The
  // *output* is repo-root-relative regardless, which is what `prefix` reconciles.
  const result = run('git', ['status', '--porcelain', '--', ...pathspec], {
    cwd,
    timeout: 30_000,
  });
  if (result.status !== 0) return false; // Can't tell -> don't block.
  return result.stdout
    .split(/\r?\n/)
    .filter(Boolean)
    .map(porcelainPath)
    .some((path) => isGated(path, hooks, prefix));
}

/**
 * True if the commits `<base>..HEAD` touched gated source — the factory's post-commit
 * analogue of `gatedChange`.
 *
 * `gatedChange` reads uncommitted working-tree edits (`git status --porcelain`), which is
 * exactly right for the Stop hook: it fires at turn-end over the agent's not-yet-committed
 * edits. The factory verifies *after* the implement step has committed the agent's work, so
 * the working tree is clean, `git status` is empty, and `gatedChange` sees nothing — every
 * gate would be `skipped_unchanged` regardless of what the change touched. Against a base
 * ref, `git diff --name-only` sees the committed change instead. `gate_report.mjs --base
 * <ref>` switches on this; the Stop hook never passes a base, so `gatedChange` is unchanged.
 *
 * The diff output is repo-root-relative, like porcelain, so the same `isGated` filter and
 * `prefix` reconciliation apply. A rename line is `old\tnew`; the last segment is the path
 * that exists.
 */
export function gatedChangeSince(cwd, hooks, prefix = '', base) {
  const pathspec = [...hooks.gatedPaths, ...hooks.gatedFiles];
  if (pathspec.length === 0) return false; // Nothing declared -> nothing to gate.
  if (!base) return false; // No base ref -> nothing to diff against.

  const result = run('git', ['diff', '--name-only', `${base}..HEAD`, '--', ...pathspec], {
    cwd,
    timeout: 30_000,
  });
  if (result.status !== 0) return false; // Can't tell -> don't block.
  return result.stdout
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => line.split('\t').pop())
    .some((path) => isGated(path, hooks, prefix));
}

/** One line per declared gate this hook does not run, naming when it stops being optional. */
export function skippedNote(gates) {
  const skipped = gates.filter((gate) => !STOP_KINDS.has(gate?.kind));
  if (skipped.length === 0) return '';
  const lines = skipped.map(
    (gate) => `  - ${gate.name} (${gate.kind})${gate.when ? `: required when ${gate.when}` : ''}`,
  );
  return `\n\nGates this hook does not run, which are still part of the Definition of Done:\n${lines.join('\n')}`;
}

/**
 * The configs whose gates this turn might have to satisfy, and the apps that declared none.
 *
 * One config in an ordinary repo — the one at the root, exactly as before `apps` existed.
 * In a monorepo, one per app named in the root config, plus the root itself when it
 * declares gates of its own.
 *
 * An app that names no config of its own is reported rather than skipped. `loadConfig`
 * walks upward, so such an app would otherwise resolve to the root config and run the whole
 * repo's gates from the wrong directory — a wrong answer wearing a green tick.
 */
export function dispatch(root) {
  if (root.apps.length === 0) return { targets: [root], missing: [] };

  const targets = [];
  const missing = [];
  for (const app of root.apps) {
    const dir = join(root.root, app);
    const config = loadConfig(dir);
    if (config.found && resolve(config.root) === resolve(dir)) targets.push(config);
    else missing.push(app);
  }
  // The root joins the list only when it has gates of its own. A router config that exists
  // to name the apps has nothing to run, and an empty gate list would still cost a
  // `git status`.
  if (root.gates.length > 0) targets.unshift(root);
  return { targets, missing };
}

/** One line naming the apps whose gates could not be found, or `''`. */
export function missingNote(missing) {
  if (missing.length === 0) return '';
  return (
    `\n\nApps named in the root harness.config.json with no config of their own, ` +
    `whose gates did not run: ${missing.join(', ')}`
  );
}

async function main() {
  if (process.env.HARNESS_SKIP_VERIFY === '1' || process.env.CLAUDE_SKIP_VERIFY === '1') return 0;

  const payload = await readPayload();
  if (!payload) return 0;

  const cwd = payload.cwd ?? '';
  const root = loadConfig(cwd);
  if (!root.found) return 0; // Nothing declares this repo's Definition of Done.

  const { targets, missing } = dispatch(root);
  // Every gate the turn's changed paths put in scope, accumulated as the targets are
  // walked so that a failure can name what it did not get to.
  const considered = [];

  for (const target of targets) {
    // The target's own directory, as git spells the paths it reports.
    const prefix = repoRelative(target.root, root.root);
    if (!gatedChange(target.root, target.hooks, prefix)) continue;

    const gates = target.gates.filter((gate) => gate && Array.isArray(gate.run));
    considered.push(...gates);
    for (const gate of gates) {
      if (!STOP_KINDS.has(gate.kind)) continue;

      // In the app's own directory, which is where its commands are written to run.
      const result = runArgv(gate.run, { cwd: target.root, timeout: GATE_TIMEOUT });
      // Naming the app matters only when there is more than one to confuse.
      const label = targets.length > 1 && target.name ? `${gate.name} (${target.name})` : gate.name;
      if (result.error) {
        process.stderr.write(`Could not run \`${label}\`: ${result.error.message}\n`);
        return 0; // Tooling problem, not a code problem -> don't block.
      }
      if (result.status !== 0) {
        const out = tail(result.stdout + result.stderr, MAX_LINES) || '(no output)';
        // ASCII only - see protect_paths.mjs.
        process.stderr.write(
          `Definition of Done is failing at \`${label}\`. Fix this before finishing. ` +
            `Do not summarise the failure as if it were done.\n\n${out}` +
            `${skippedNote(considered)}${missingNote(missing)}\n`,
        );
        return 2;
      }
    }
  }
  return 0;
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === resolve(fileURLToPath(import.meta.url))) {
  process.exit(await main());
}
