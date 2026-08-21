#!/usr/bin/env node
/**
 * PreToolUse hook: refuse to write human-owned and generated paths, refuse to read the
 * paths that hold secrets, and refuse the shell commands that reach a secret without
 * naming a path at all.
 *
 * Exit 2 is the ONLY exit code that blocks a tool call; stderr becomes the reason the
 * agent sees. Exit 1 lets the call through with a warning, which is the most common hook
 * bug.
 *
 * **This is three guards that used to be two scripts and one deny list.** `python-harness`
 * split the write guard (`protect_paths.py`) from the read-and-command guard
 * (`protect_secrets.py`) and wired the second one into Codex only, so Claude Code had no
 * read-side guard at all — defect 1. `frontend-harness` folded reads into the write guard
 * but left commands to `permissions.deny`, where a rule is a literal prefix and `cat .env`
 * is stopped while every other spelling is not. One hook now covers all three surfaces.
 *
 * The reason they were ever separate was cost: both Python hooks shell out to `uv run`, so
 * matching `Read|Bash` meant paying for an interpreter start on every search in the
 * session. This implementation shells out to nothing — it reads stdin, tests strings, and
 * exits — so one matcher over the whole surface is cheap and no longer a trade.
 *
 * **The matcher must cover every tool that can write a file, not just `Edit|Write`.** A
 * hook matcher is a case-sensitive regex over the tool name, so `Edit|Write` misses
 * `NotebookEdit` and every MCP write tool. A protected path is protected only on the tool
 * surfaces the matcher names. See `hooks/hooks.json` and each stack's settings.
 *
 * **Reads are blocked for secrets only.** A write to a generated file is a mistake the
 * author can undo; a read of `.env` is not. The value enters the context window, the
 * transcript on disk and the API request in one step, and only rotation undoes that. So
 * `.env` refuses both verbs while `dist/` refuses only the write.
 *
 * A permission `deny` rule cannot express that: it has no exception syntax, so
 * `Read(./.env.*)` would also hide `.env.example` — the committed file that documents the
 * env contract. `ALLOWED` is that exception, which is why the rule lives in code.
 *
 * **The floor is built in, not configured.** `SECRET_FLOOR` and the command patterns below
 * apply whether or not `harness.config.json` was found or parsed. A guard that silently
 * protects nothing when its config goes missing is worse than no guard, because the repo
 * still reads as protected. The config adds to the floor; it cannot lower it.
 *
 * **A monorepo has more than one config, and every one of them guards.** The root's rules
 * hold over the whole tree; an app's hold inside that app, with its globs read relative to
 * the app — `uv.lock` in `apps/api/harness.config.json` means `apps/api/uv.lock`, not any
 * lockfile anywhere. Reading only one config would be the silent half-protection this hook
 * exists to end: adding a config to an app would quietly cancel the root's rules there.
 */

import { join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import {
  globToRegExp,
  loadConfig,
  readPayload,
  relativePath,
  repoRelative,
  toolPaths,
} from './lib.mjs';

/** Refuse writes. Reading the file costs nothing, so reading stays allowed. */
export const WRITE = 'write';

/** Refuse writes and reads. The contents must not reach the transcript. */
export const SECRET = 'secret';

/**
 * Secret rules every consuming repo gets, config or no config. Both stacks declared these
 * identically, which is the definition of something that belongs in layer A.
 */
export const SECRET_FLOOR = [
  { glob: '.env', why: 'holds real secrets and is gitignored', scope: SECRET },
  { glob: '.env.*', why: 'holds real secrets and is gitignored', scope: SECRET },
];

/** Committed files that a `.env.*` rule would otherwise hide. Also a floor. */
export const ALLOWED_FLOOR = ['.env.example'];

/** Tool names that only read. Every other tool the matcher admits is treated as a write. */
const READ_TOOLS = new Set(['Read']);

/** Whole-environment dumps, across both shells the harnesses use. */
const ENV_DUMPS =
  /^(?:env|printenv|set|export(?:\s+-p)?|declare\s+-x|typeset\s+-x|compgen\s+-e|Get-ChildItem\s+Env:|gci\s+Env:|ls\s+Env:|dir\s+Env:|Get-Variable)(?:\s|$)/i;

/**
 * An inline interpreter reaches the same environment and defeats every other rule, because
 * the variable name need not appear in the command at all.
 */
const INLINE_INTERPRETERS =
  /^(?:python|python3|uv\s+run\s+python)\s+-c(?:\s|$)|^(?:node)\s+-(?:e|p)(?:\s|$)/;

/** The shell readers that reach a secret file's bytes without the Read tool. */
const FILE_READERS =
  /(?:cat|type|more|less|head|tail|nl|strings|Get-Content|gc)\s+\.?\/?\.env(?:\s|$)/;

/**
 * Why `toolName` must not touch `path`, or `null` when the call is allowed.
 *
 * `path` is repo-relative and forward-slashed. A read is refused by `SECRET` entries only;
 * a write is refused by every entry.
 */
export function blockReason(path, toolName, rules, allowed) {
  const name = path.split('/').pop() ?? path;
  if (allowed.has(name)) return null;

  const reading = READ_TOOLS.has(toolName);
  for (const { pattern, why, scope } of rules) {
    if (reading && scope !== SECRET) continue;
    if (pattern.test(path) || pattern.test(name)) return why;
  }
  return null;
}

/**
 * Why this shell command must not run, or `null`.
 *
 * `secretVars` comes from the config: the dumps and the interpreters are named by their own
 * spelling, but a single-variable expansion can only be recognised from the variable's name,
 * and layer A does not know which names a repo cares about.
 */
export function commandReason(command, secretVars) {
  if (typeof command !== 'string') return null;
  const stripped = command.trim();
  if (ENV_DUMPS.test(stripped)) return 'the command dumps environment variables';
  if (INLINE_INTERPRETERS.test(stripped)) return 'inline interpreters can read inherited secrets';
  if (FILE_READERS.test(stripped)) return 'the command reads a secret file';
  for (const name of secretVars) {
    if (name && new RegExp(name, 'i').test(stripped)) {
      return 'the command references a protected secret variable';
    }
  }
  return null;
}

/** The floor plus whatever the repo declared, compiled once. */
export function rulesFor(config) {
  const declared = config.hooks.protected
    .filter((entry) => entry && typeof entry.glob === 'string')
    .map((entry) => ({
      glob: entry.glob,
      why: typeof entry.why === 'string' ? entry.why : 'this path is protected',
      scope: entry.scope === SECRET ? SECRET : WRITE,
    }));
  return [...SECRET_FLOOR, ...declared].map((entry) => ({
    pattern: globToRegExp(entry.glob),
    why: entry.why,
    scope: entry.scope,
  }));
}

/** Filenames exempt from every rule: the floor plus whatever the repo declared. */
export function allowedFor(config) {
  return new Set([...ALLOWED_FLOOR, ...config.hooks.allowed.filter((n) => typeof n === 'string')]);
}

/**
 * Every config that guards something in this repository: the root's, plus each app it names.
 *
 * Read from the root config's `apps` rather than by searching the tree: a hook that walked
 * a large checkout looking for config files would pay for it on every tool call.
 */
export function repoConfigs(cwd) {
  const root = loadConfig(cwd);
  if (!root.found || root.apps.length === 0) return [root];

  const configs = [root];
  for (const app of root.apps) {
    const dir = join(root.root, app);
    const config = loadConfig(dir);
    // `loadConfig` walks upward, so an app with no config of its own returns the root's.
    // Taking it would apply the root's globs twice under an app-relative reading.
    if (config.found && resolve(config.root) === resolve(dir)) configs.push(config);
  }
  return configs;
}

/**
 * The compiled rule sets, each with the repo-relative prefix its globs are written against.
 *
 * The root's prefix is `''`; an app's is its directory. Both stay in the list — a rule at
 * the root is not superseded by an app declaring rules of its own.
 */
export function guardsFor(configs, rootDir) {
  return configs.map((config) => ({
    prefix: repoRelative(config.root, rootDir),
    rules: rulesFor(config),
    allowed: allowedFor(config),
  }));
}

/**
 * `path` as the config at `prefix` would spell it, or `null` when it lies outside that tree.
 */
export function scopedPath(path, prefix) {
  if (!prefix) return path;
  if (path === prefix) return '';
  return path.startsWith(`${prefix}/`) ? path.slice(prefix.length + 1) : null;
}

/** The first reason any guard refuses `path`, or `null`. */
export function guardedReason(path, toolName, guards) {
  for (const guard of guards) {
    const scoped = scopedPath(path, guard.prefix);
    if (scoped === null) continue;
    const reason = blockReason(scoped, toolName, guard.rules, guard.allowed);
    if (reason) return reason;
  }
  return null;
}

/** Every secret variable name any config in this repository declared. */
export function secretVarsFor(configs) {
  return [
    ...new Set(
      configs.flatMap((config) => config.hooks.secretVars.filter((n) => typeof n === 'string')),
    ),
  ];
}

async function main() {
  const payload = await readPayload();
  if (!payload) return 0;

  const cwd = payload.cwd ?? '';
  const configs = repoConfigs(cwd);
  const toolName = payload.tool_name ?? '';

  // The command guard first: a Bash call carries no `file_path`, so the path loop below
  // would pass it through silently. A shell command belongs to no app in particular, so
  // every config's secret names count.
  const why = commandReason(payload.tool_input?.command, secretVarsFor(configs));
  if (why && toolPaths(payload).length === 0) {
    // ASCII only: hook stderr is decoded by the harness, and a Windows console codepage
    // can mangle non-ASCII on the way out.
    process.stderr.write(`Refusing tool call - ${why}.\n`);
    return 2;
  }

  const guards = guardsFor(configs, configs[0].root);
  for (const raw of toolPaths(payload)) {
    const path = relativePath(raw, cwd);
    const reason = guardedReason(path, toolName, guards);
    if (!reason) continue;

    const [verb, advice] = READ_TOOLS.has(toolName)
      ? [
          'read',
          'A value read here enters the transcript, and only rotation undoes that.\n' +
            'Refer to the variable by name, or ask the user to check it.\n',
        ]
      : ['edit', 'Ask the user to make this change, or explain why it is required.\n'];
    process.stderr.write(`Refusing to ${verb} ${path} - ${reason}.\n${advice}`);
    return 2;
  }
  return 0;
}

// Run only when invoked as a hook, never on import — the test suite imports this module,
// and reading stdin on import would hang the suite. `resolve` on both sides because the
// harness spells the hook path with forward slashes even on Windows, where `argv[1]` keeps
// them.
const invoked = process.argv[1] ? resolve(process.argv[1]) : '';
if (invoked === resolve(fileURLToPath(import.meta.url))) {
  process.exit(await main());
}
