#!/usr/bin/env node
/**
 * The machine-readable gate report: every gate the config declares, with a verdict.
 *
 * `verify.mjs` is a Stop *hook*: it runs only `STOP_KINDS`, it returns 0 when a gate could
 * not start, it never prints a `caveat`, and its output is prose on stderr. The `/verify`
 * skill prints caveats and covers `e2e`/`integration`, but its output is a chat message.
 * Neither is a machine record. This file is: one JSON document on stdout that says exactly
 * which gates ran, which passed, which were unavailable, which were not applicable, and
 * every caveat — the third of the three independent verification signals in §15.1.
 *
 * It reuses layer A rather than re-authoring it. `dispatch`, `gatedChange`, `isGated` and
 * `STOP_KINDS` come from `./verify.mjs`; `loadConfig`, `runArgv`, `repoRelative` and `tail`
 * come from `./lib.mjs`. No classification logic is copied, so the report and the Stop hook
 * cannot drift apart on what "this app was touched" or "this gate is opt-in" means. The
 * factory (layer D) reads this document; it must never re-derive which gates apply, because
 * that would re-author `dispatch()` and `STOP_KINDS` in Python where they would drift
 * silently — the single failure this repository exists to prevent.
 *
 * ```
 * node gate_report.mjs [--gate <name>]... [--all] [--json] [--base <ref>] [--cwd <dir>]
 * ```
 *
 * The `e2e`/`integration` gates are opt-in: without an assertion they are `not_applicable`,
 * because opt-in is not optional — the caller asserts a gate's `when` clause, and the factory
 * makes that decision from the agent's structured answer cross-checked by name against
 * `harness.config.json`'s own gates, never by pattern-matching the diff here.
 *
 * `--gate <name>` asserts **one** opt-in gate, and is repeatable. This is the form a machine
 * caller should use, because a `when` clause is prose (`"performance or accessibility budgets
 * are in scope"`) and nothing here can evaluate it — only the caller knows whether it holds,
 * and it holds per gate, not per run.
 *
 * `--all` asserts **every** opt-in gate at once. It is the interactive form — a human who
 * wants the full suite — and it is deliberately blunt: it does not re-read `when`, so it
 * asserts clauses the caller may not have meant. Measured on frontend-harness FRO-7,
 * 2026-08-22: an agent that honestly ran `playwright` forced `lighthouse` to run too, whose
 * `when` was plainly false for the change and which cannot pass on that machine at all
 * (its own caveat: the performance category scores null against the installed Chrome). The
 * run blocked on a gate that should never have executed. `--gate` is the repair; `--all`
 * keeps its meaning for the caller who really does want all of them.
 *
 * `--base <ref>` switches the "did this app change?" check from `git status --porcelain`
 * (uncommitted turn edits, what the Stop hook sees) to `git diff --name-only <base>..HEAD`
 * (the committed change since a base ref). The factory passes this — it commits the agent's
 * work before it verifies, so the working tree is clean and `git status` would see nothing;
 * against the run's base ref the diff sees the change instead. The Stop hook never passes
 * it, so its `gatedChange` is unchanged. See `gatedChangeSince` in `verify.mjs`.
 *
 * `--json` emits the document below. Without it, a compact human-readable summary goes to
 * stdout and the same exit code is returned, so an interactive run is legible without a
 * pipe. The factory always passes `--json`.
 *
 * ## Classification
 *
 * | status | Meaning |
 * | --- | --- |
 * | `pass` | exit 0, and the gate actually started |
 * | `fail` | non-zero exit |
 * | `unavailable` | the gate did not run: either the process could not be spawned (`result.error`) — the case `verify.mjs` must swallow and a report must not — or its `requires` probe said the environment is not there, in which case the gate was never attempted and carries no `exit` |
 * | `not_applicable` | `kind` is `e2e`/`integration` and the caller asserted neither `--gate <name>` nor `--all` |
 * | `skipped_unchanged` | the app's `gatedChange()` was false — a monorepo app the turn never touched |
 * | `disabled` | `enabled: false` in config — the gate is declared but switched off |
 *
 * ## The verdict
 *
 * `verdict` is `pass`, `fail` or `incomplete`. It is **`incomplete` — never `pass`** when any
 * gate is `unavailable`, or when any app named in a root config had no config of its own.
 * That single rule is the whole answer to "a green exit code does not prove every relevant
 * gate ran": a gate that could not start is reported, never rounded to green.
 *
 * Exit codes are distinct so a caller that reads only the exit code still cannot mistake
 * incomplete for pass: `0` pass, `1` fail, `3` incomplete. A real `fail` outranks
 * `incomplete`, because a failing gate is the more actionable signal; the exit code is the
 * verdict's, with that precedence.
 */

import { performance } from 'node:perf_hooks';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

import { loadConfig, repoRelative, runArgv, tail } from './lib.mjs';
import { dispatch, gatedChange, gatedChangeSince, STOP_KINDS } from './verify.mjs';

const MAX_LINES = 40;
const GATE_TIMEOUT = 540_000;
const PROBE_TIMEOUT = 30_000;

/** Schema version of the emitted document. Bumped only on a breaking shape change. */
export const REPORT_SCHEMA_VERSION = 1;

/** Exit codes, distinct so an exit-code-only caller cannot mistake incomplete for pass. */
export const EXIT = { pass: 0, fail: 1, incomplete: 3 };

/**
 * The run result a gate produced, plus how long it took.
 *
 * `status` is `null` when the process could not be spawned — the `unavailable` case. The
 * `durationMs` is measured by the caller that actually runs the gate, so the pure report
 * builder below never touches a clock.
 *
 * @typedef {Object} GateRun
 * @property {number|null} status
 * @property {string} stdout
 * @property {string} stderr
 * @property {Error|null} error
 * @property {number|null} durationMs
 */

/**
 * The outcome of one gate, in the shape the JSON document emits.
 *
 * @typedef {Object} GateEntry
 * @property {string} name
 * @property {string} kind
 * @property {string} status
 * @property {number|null} exit
 * @property {number|null} durationMs
 * @property {string|null} caveat
 * @property {string|null} when
 * @property {string} outputTail
 */

/**
 * Classify a gate that ran, from its run result.
 *
 * The one judgment this encodes: a process that could not start is `unavailable`, not `fail`
 * and not `pass`. `verify.mjs` swallows exactly this case (it returns 0 so a tooling problem
 * does not wedge the turn); a report must not, because "the gate could not run" is the fact
 * the factory most needs to hear.
 *
 * @param {GateRun} run
 * @returns {'pass'|'fail'|'unavailable'}
 */
export function classifyRun(run) {
  if (run.error) return 'unavailable';
  return run.status === 0 ? 'pass' : 'fail';
}

/**
 * Whether a gate's `requires` probe proved the gate's environment is actually there.
 *
 * The judgment here is the mirror of `classifyRun`'s, and it exists because the two failures
 * are indistinguishable from a gate's exit code alone. `pnpm test:e2e` with no browser
 * downloaded exits non-zero exactly the way it does for a real regression; so does
 * `pytest -m integration` with no container runtime. A report that calls both `fail` tells a
 * reader the code is wrong, and an agent handed that will spend an attempt trying to fix the
 * code -- repeatedly, because writing code never installs a browser.
 *
 * A probe that could not be spawned counts as unmet, not as met. The probe is the cheaper
 * command of the two; if it cannot start, the gate's own toolchain is not there either, and
 * guessing "probably fine" is the one answer that puts the expensive wrong result back on the
 * table.
 *
 * @param {GateRun} probe
 * @returns {boolean}
 */
export function requirementMet(probe) {
  if (probe.error) return false;
  return probe.status === 0;
}

/**
 * One gate's entry, given its status and the run that produced it (or `null` when it did not
 * run). Fields are uniform across every status so a reader never has to special-case the
 * shape: `exit`, `durationMs` and `outputTail` are `null`/`''` for the statuses that did not
 * execute anything, and `caveat`/`when` are always present (they name how a green result
 * proved nothing, which matters beside a `pass` too).
 *
 * @param {Object} gate
 * @param {string} status
 * @param {GateRun|null} run
 * @returns {GateEntry}
 */
function gateEntry(gate, status, run) {
  const entry = {
    name: gate.name,
    kind: gate.kind,
    status,
    exit: null,
    durationMs: null,
    caveat: gate.caveat ?? null,
    when: gate.when ?? null,
    outputTail: '',
  };
  if (run && (status === 'pass' || status === 'fail' || status === 'unavailable')) {
    entry.exit = run.error ? null : run.status;
    entry.durationMs = run.durationMs ?? null;
    if (status !== 'pass') {
      const out = (run.stdout ?? '') + (run.stderr ?? '');
      entry.outputTail = tail(out, MAX_LINES) || (run.error?.message ?? '') || '(no output)';
    }
  }
  return entry;
}

/**
 * The verdict for a set of gate entries and the apps that had no config of their own.
 *
 * `fail` outranks `incomplete`: a failing gate is more actionable than a missing one, and the
 * exit code follows the verdict. `incomplete` — never `pass` — when anything was
 * `unavailable` or an app was missing, so a green exit code can never stand in for "every
 * relevant gate ran." Everything else is `pass`, including the `not_applicable` and
 * `skipped_unchanged` rows: those are documented dispatch decisions, not checks that failed
 * to run.
 *
 * @param {GateEntry[]} gates
 * @param {string[]} missing
 * @returns {'pass'|'fail'|'incomplete'}
 */
export function computeVerdict(gates, missing) {
  if (gates.some((gate) => gate.status === 'fail')) return 'fail';
  if (gates.some((gate) => gate.status === 'unavailable') || missing.length > 0)
    return 'incomplete';
  return 'pass';
}

/** The exit code for a verdict. */
export function exitCode(verdict) {
  return EXIT[verdict] ?? EXIT.incomplete;
}

/**
 * Build the full report from dispatched targets, without touching a subprocess.
 *
 * The two side effects a real run needs — "was this app touched?" and "run this gate" — are
 * injected, so the same logic runs in tests against fakes with no `git` and no toolchain.
 * That mirrors `gatedChangeWith` in the hook suite, and for the same reason: a unit test
 * must not shell out.
 *
 * `isChanged(target)` answers the same question `verify.mjs` asks before it runs an app's
 * gates: did the turn touch a path this app gates? When it did not, every gate for that app
 * is `skipped_unchanged` — a monorepo app the turn never reached, rather than a green tick
 * for a suite that never ran.
 *
 * `runGate(gate, target)` returns a {@link GateRun}. It is called only for gates that
 * actually run, so a fake never has to answer for a `not_applicable` or `skipped_unchanged`
 * gate.
 *
 * `probeGate(gate, target)` returns a {@link GateRun} for a gate's `requires` argv. It is
 * called only for a gate that declares one *and* was already going to run, so asserting no
 * opt-in gate probes nothing and costs nothing. It defaults to "met" so every existing caller
 * -- and every config with no `requires` anywhere -- behaves exactly as it did before.
 *
 * @param {Object} args
 * @param {Object} args.root - The root config, as `loadConfig` returns it.
 * @param {Object[]} args.targets - The dispatched targets, as `dispatch` returns them.
 * @param {string[]} args.missing - Apps named in the root config with no config of their own.
 * @param {boolean} [args.all=false] - Whether the caller asserted every opt-in `when` clause.
 * @param {string[]} [args.gates=[]] - Opt-in gate names the caller asserted individually.
 * @param {(target: Object) => boolean} args.isChanged
 * @param {(gate: Object, target: Object) => GateRun} args.runGate
 * @param {(gate: Object, target: Object) => GateRun} [args.probeGate]
 * @returns {Object} the JSON document
 */
export function buildReport({
  root,
  targets,
  missing,
  all = false,
  gates: asserted = [],
  isChanged,
  runGate,
  probeGate = () => ({ status: 0, stdout: '', stderr: '', error: null, durationMs: null }),
}) {
  // Names, not indices: a monorepo dispatches the same gate name across several targets and
  // the caller asserts the gate, not one app's copy of it. An asserted name that no config
  // declares is inert rather than an error — the caller named a gate this repo does not have,
  // which is the same nothing as not naming it.
  const assertedNames = new Set(asserted);
  const gates = [];
  for (const target of targets) {
    const prefix = repoRelative(target.root, root.root);
    const dir = prefix || '.';
    const changed = isChanged(target);
    // The same filter the Stop hook uses: a gate without a `run` argv is malformed config,
    // not a gate to attempt. Reporting it would mean executing `undefined`.
    const declared = target.gates.filter((gate) => gate && Array.isArray(gate.run));
    for (const gate of declared) {
      // Switched off in config, and that outranks every other reason a gate might or
      // might not run: an operator who set `enabled: false` gets the same answer whether
      // or not the turn touched the app, and whether or not the caller asserted it.
      //
      // Reported rather than omitted. A deleted gate is invisible; a `disabled` row tells
      // the reader the gate exists and is off, which is the difference between a suite
      // that does not check something and a suite that never claimed to. It is not a
      // `pass` — nothing ran — but it is not `incomplete` either, because nothing failed
      // to start that was meant to start. `computeVerdict` special-cases only `fail` and
      // `unavailable`, so this falls through to `pass` on its own.
      if (gate.enabled === false) {
        gates.push(gateEntry(gate, 'disabled', null));
        continue;
      }
      if (!changed) {
        gates.push(gateEntry(gate, 'skipped_unchanged', null));
        continue;
      }
      // The opt-in kinds are exactly the ones the Stop hook does not run — the complement
      // of `STOP_KINDS` — so this reuses layer A's own line rather than re-authoring the
      // e2e/integration list here. A new opt-in kind added to the enum is `not_applicable`
      // unasserted automatically, the same way it stops being a Stop gate.
      //
      // Asserted per gate first, then the blanket `--all`. That order is the whole fix:
      // a caller naming `playwright` gets `playwright`, and does not silently also assert
      // `lighthouse`'s unrelated `when` clause.
      if (!STOP_KINDS.has(gate.kind) && !all && !assertedNames.has(gate.name)) {
        gates.push(gateEntry(gate, 'not_applicable', null));
        continue;
      }
      // The environment assertion, after every reason not to run and before the run itself.
      // After, because probing a gate that `not_applicable` or `skipped_unchanged` already
      // settled would spend a subprocess to answer a question nobody asked. Before, because
      // the whole point is that the gate does not run: `unavailable` here means "this was
      // never attempted", so it carries no `exit` and no `durationMs` -- the gate has no
      // exit code, and saying it exited would be a lie about which command produced it.
      // The probe's own output goes in the tail, because that is the only place a reader
      // can find out which requirement was missing.
      if (Array.isArray(gate.requires) && gate.requires.length > 0) {
        const probe = probeGate(gate, target);
        if (!requirementMet(probe)) {
          const entry = gateEntry(gate, 'unavailable', null);
          entry.outputTail =
            tail((probe.stdout ?? '') + (probe.stderr ?? ''), MAX_LINES) ||
            probe.error?.message ||
            `requires: ${gate.requires.join(' ')}`;
          gates.push(entry);
          continue;
        }
      }
      // Run once: `runGate` shells out in production, so calling it twice would run every
      // gate twice. The result is classified and recorded from the same run.
      const run = runGate(gate, target);
      gates.push(gateEntry(gate, classifyRun(run), run));
    }
  }

  const verdict = computeVerdict(gates, missing);
  return {
    schemaVersion: REPORT_SCHEMA_VERSION,
    root: root.root,
    targets: targets.map((target) => ({
      name: target.name,
      dir: repoRelative(target.root, root.root) || '.',
    })),
    missingApps: missing,
    gates,
    verdict,
  };
}

/** Run a gate's argv and time it. Returns a {@link GateRun} with `durationMs` set. */
function timedRun(argv, options) {
  const start = performance.now();
  const result = runArgv(argv, options);
  return { ...result, durationMs: Math.round(performance.now() - start) };
}

/**
 * Parse the CLI flags. Deliberately tiny: `--gate <name>` (repeatable), `--all`, `--json`,
 * `--base <ref>`, `--cwd <dir>` (or `--gate=<name>` / `--cwd=<dir>` / `--base=<ref>`). No
 * abbreviations, no `--no-*` — a report's caller is a machine or a person who read the help
 * line above, and a forgiving parser is a parser that silently does the wrong thing.
 *
 * An empty `--gate` value is dropped rather than asserting a gate named `''`: the argv came
 * from a caller interpolating a name it did not have, and asserting nothing is the honest
 * reading of that.
 */
export function parseArgs(argv) {
  const args = { all: false, gates: [], json: false, base: '', cwd: '' };
  const addGate = (name) => {
    if (name) args.gates.push(name);
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === '--all') args.all = true;
    else if (flag === '--gate') addGate(argv[++i] ?? '');
    else if (flag.startsWith('--gate=')) addGate(flag.slice('--gate='.length));
    else if (flag === '--json') args.json = true;
    else if (flag === '--base') args.base = argv[++i] ?? '';
    else if (flag.startsWith('--base=')) args.base = flag.slice('--base='.length);
    else if (flag === '--cwd') args.cwd = argv[++i] ?? '';
    else if (flag.startsWith('--cwd=')) args.cwd = flag.slice('--cwd='.length);
  }
  return args;
}

/** One line per gate for a human reader, then the verdict. ASCII only, like the hook's stderr. */
function humanReport(report) {
  const lines = [];
  for (const gate of report.gates) {
    const where = report.targets.length > 1 ? ` (${gate.name})` : '';
    lines.push(`${gate.status}\t${gate.name}${where}`);
    if (gate.caveat && (gate.status === 'pass' || gate.status === 'not_applicable')) {
      lines.push(`\tcaveat: ${gate.caveat}`);
    }
  }
  if (report.missingApps.length > 0) {
    lines.push(`incomplete\tapps with no config: ${report.missingApps.join(', ')}`);
  }
  lines.push(`verdict: ${report.verdict}`);
  return lines.join('\n');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const cwd = args.cwd || process.cwd();
  const root = loadConfig(cwd);
  const { targets, missing } = dispatch(root);

  const report = buildReport({
    root,
    targets,
    missing,
    all: args.all,
    gates: args.gates,
    isChanged: (target) =>
      args.base
        ? gatedChangeSince(
            target.root,
            target.hooks,
            repoRelative(target.root, root.root),
            args.base,
          )
        : gatedChange(target.root, target.hooks, repoRelative(target.root, root.root)),
    runGate: (gate, target) => timedRun(gate.run, { cwd: target.root, timeout: GATE_TIMEOUT }),
    // A far shorter timeout than a gate's: a requirement probe that has not answered in
    // thirty seconds is not a slow probe, it is a hung one, and its whole reason to exist
    // is being cheaper than the gate it guards.
    probeGate: (gate, target) =>
      timedRun(gate.requires, { cwd: target.root, timeout: PROBE_TIMEOUT }),
  });

  if (args.json) process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  else process.stdout.write(`${humanReport(report)}\n`);
  return exitCode(report.verdict);
}

const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === resolve(fileURLToPath(import.meta.url))) {
  process.exit(await main());
}
