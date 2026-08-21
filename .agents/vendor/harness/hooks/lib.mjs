/**
 * Shared plumbing for the hooks in this directory.
 *
 * Dependency-free on purpose, and that is a harder constraint here than it was in either
 * stack. These hooks run in a Python repository as well as a Node one, and in a vendored
 * tree that no package manager has ever visited. There is no `node_modules` to reach for
 * and no install step to add one, so the only requirement is Node 22 itself.
 *
 * Everything a hook needs to know about the repository it is running in comes from that
 * repository's `harness.config.json`. Layer A names no toolchain, no directory and no
 * team key; see `schema/harness.config.schema.json` for the contract.
 */

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, parse, resolve, sep } from 'node:path';
import process from 'node:process';

/**
 * Windows has no executable `pnpm` or `uv` on PATH — only `pnpm.cmd`, which `spawnSync`
 * finds only through a shell. POSIX needs no shell, so it does not get one.
 */
const USE_SHELL = process.platform === 'win32';

/**
 * Characters that change meaning inside `cmd.exe`. Any argument built from a tool payload
 * is checked against this before it reaches a shelled command.
 */
const SHELL_META = /[&|<>^"%!]/;

/** True when `value` is safe to place in a shelled command line. */
export function shellSafe(value) {
  return !SHELL_META.test(value);
}

/**
 * Run a command and return `{ status, stdout, stderr, error }`.
 *
 * `encoding` is explicit rather than the default Buffer: decoding a Buffer with the locale
 * codec (cp1252 on Windows) turns every em-dash and curly quote in a ruff, mypy, ESLint or
 * tsc diagnostic into mojibake — corrupting the exact message the hook exists to echo back.
 *
 * Never throws. A tool that will not start is a tooling problem, and a hook must not
 * convert one into a blocked turn.
 */
export function run(command, args, { cwd, timeout = 120_000, env } = {}) {
  const quoted = USE_SHELL ? args.map((a) => (a.includes(' ') ? `"${a}"` : a)) : args;
  const result = spawnSync(command, quoted, {
    cwd: cwd || undefined,
    encoding: 'utf8',
    timeout,
    shell: USE_SHELL,
    windowsHide: true,
    env,
  });
  return {
    status: result.error ? null : result.status,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
    error: result.error ?? null,
  };
}

/** Run an argv array — the form every command in `harness.config.json` takes. */
export function runArgv(argv, options) {
  const [command, ...args] = argv;
  return run(command, args, options);
}

/** stdout of a command, or `''` on any non-zero exit or failure to start. */
export function output(command, args, options) {
  const result = run(command, args, options);
  return result.status === 0 ? result.stdout.trim() : '';
}

/** Read the hook payload from stdin. Returns `null` when it is absent or malformed. */
export async function readPayload() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    return null; // Never block because the hook itself could not parse.
  }
}

/** Repo-relative, forward-slashed form of a path the harness reported. */
export function relativePath(raw, projectDir) {
  let path = raw.replaceAll('\\', '/');
  const root = (projectDir || '').replaceAll('\\', '/').replace(/\/+$/, '');
  if (root && path.toLowerCase().startsWith(`${root.toLowerCase()}/`)) {
    path = path.slice(root.length + 1);
  }
  // Collapse `./` and `a/../b` without touching a leading dot: stripping "./" with a trim
  // would turn ".env" into "env" and defeat every dotfile rule.
  const parts = [];
  for (const part of path.split('/')) {
    if (part === '.' || part === '') continue;
    if (part === '..' && parts.length > 0 && parts.at(-1) !== '..') parts.pop();
    else parts.push(part);
  }
  return parts.join('/');
}

/**
 * `dir` as a path relative to `root`, forward-slashed, or `''` when they are the same.
 *
 * The empty case is the one worth naming: `relativePath` strips a `root/` prefix, and a
 * directory that *is* the root has no such prefix, so it would come back as an absolute
 * path with its leading slash removed. Both callers -- the Stop gate's per-app prefix and
 * the guards' per-config scope -- mean "nothing to prepend" there.
 */
export function repoRelative(dir, root) {
  return resolve(dir) === resolve(root) ? '' : relativePath(dir, root);
}

/** Return every file path named by a file tool or a unified-patch call. */
export function toolPaths(payload) {
  const direct = payload?.tool_input?.file_path;
  if (typeof direct === 'string' && direct) return [direct];

  // Codex sends the patch body in `tool_input.command`. Claude sends a shell command under
  // the same key, so the patch markers rather than the tool name decide: a Bash call that
  // happens to be inspected here simply yields no paths.
  const command = payload?.tool_input?.command ?? payload?.tool_input?.patch;
  if (typeof command !== 'string') return [];

  return [...command.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)].map((match) =>
    match[1].trim(),
  );
}

/** The last `count` lines of `text`. */
export function tail(text, count) {
  return text.trim().split(/\r?\n/).slice(-count).join('\n');
}

/**
 * Compile one glob to a regular expression.
 *
 * `**` crosses directory separators, `*` does not — the distinction is what lets `dist/**`
 * cover a whole tree while `.env.*` stays confined to one path segment.
 */
export function globToRegExp(pattern) {
  let source = '';
  for (let i = 0; i < pattern.length; i += 1) {
    const char = pattern[i];
    if (char === '*' && pattern[i + 1] === '*') {
      if (pattern[i + 2] === '/') {
        source += '(?:.*/)?'; // A `**/` prefix must also match zero directories.
        i += 2;
      } else {
        source += '.*';
        i += 1;
      }
    } else if (char === '*') source += '[^/]*';
    else if (char === '?') source += '[^/]';
    else source += char.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${source}$`);
}

export const CONFIG_NAME = 'harness.config.json';

/** True when `dir` holds a readable `harness.config.json`. */
function hasConfig(dir) {
  try {
    readFileSync(join(dir, CONFIG_NAME));
    return true;
  } catch {
    return false;
  }
}

/** True when `dir` is `ceiling` or sits inside it. Case-folded, because macOS and Windows are. */
function within(dir, ceiling) {
  const a = dir.toLowerCase();
  const b = ceiling.toLowerCase();
  return a === b || a.startsWith(b.endsWith(sep) ? b : b + sep);
}

/**
 * Every `harness.config.json` from `start` upwards, nearest first, stopping at `ceiling`.
 *
 * Upward rather than at a fixed path, for the shape phase 6 scaffolds: a monorepo declares
 * one config per app, and a hook firing on `apps/web/src/x.ts` must find `apps/web`'s
 * formatters rather than the root's. A repo with one config at its root is the same search,
 * terminating on the first step.
 *
 * **The whole chain, not just the nearest, for the guards.** An app declares what its own
 * lockfile and migrations are; the root declares what holds for the whole tree. A guard
 * reading only the nearest would drop every root rule the moment an app grew a config of
 * its own — and it would drop them silently, which is the only way these guards ever fail.
 *
 * `ceiling` is the session's project directory. Bounding the walk there rather than at the
 * filesystem root keeps a checkout from inheriting rules out of whatever happens to sit
 * above it. An empty `ceiling` walks to the root, which is what a caller wanting only the
 * nearest config asks for.
 */
export function configChain(start, ceiling) {
  const top = ceiling ? resolve(ceiling) : '';
  let dir = resolve(start || process.cwd());
  if (top && !within(dir, top)) dir = top;

  const { root } = parse(dir);
  const found = [];
  for (;;) {
    if (hasConfig(dir)) found.push(join(dir, CONFIG_NAME));
    if (top && within(top, dir)) return found; // Reached the ceiling.
    if (dir === root) return found;
    const parent = dirname(dir);
    if (parent === dir) return found;
    dir = parent;
  }
}

/** The nearest `harness.config.json` at or above `start`, or `''`. */
export function findConfig(start) {
  return configChain(start, '')[0] ?? '';
}

/** Every key a hook reads, with the value it takes when the config does not say. */
const HOOK_DEFAULTS = {
  gatedPaths: [],
  gatedFiles: [],
  gatedExtensions: [],
  protected: [],
  allowed: [],
  secretVars: [],
  formatters: [],
};

/** The shape every reader gets when there is no config, or it does not parse. */
function emptyConfig(root) {
  return {
    found: false,
    root: root || process.cwd(),
    name: '',
    apps: [],
    gates: [],
    hooks: { ...HOOK_DEFAULTS },
  };
}

/**
 * One `harness.config.json`, read from `path` and normalised.
 *
 * **Never throws and never reports a problem**: a hook that dies on a malformed config is a
 * hook that stops enforcing, and one that writes to stderr on every tool call is one that
 * gets disabled. `found` is false when the file does not parse, and each caller decides
 * what that means — `verify` has no gates to run, while `protect_paths` still applies its
 * built-in floor.
 */
function readConfig(path) {
  const root = dirname(path);
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return emptyConfig(root);
  }
  if (!parsed || typeof parsed !== 'object') return emptyConfig(root);

  const declared = parsed.hooks && typeof parsed.hooks === 'object' ? parsed.hooks : {};
  const hooks = { ...HOOK_DEFAULTS };
  for (const key of Object.keys(HOOK_DEFAULTS)) {
    if (Array.isArray(declared[key])) hooks[key] = declared[key];
  }

  return {
    found: true,
    root,
    name: typeof parsed.name === 'string' ? parsed.name : '',
    // The repo-relative directories of a monorepo's apps, each holding a config of its
    // own. Absent in a single-app repo, which is every repo layer A served before phase 6.
    apps: Array.isArray(parsed.apps)
      ? parsed.apps.filter((app) => typeof app === 'string' && app)
      : [],
    gates: Array.isArray(parsed.gates) ? parsed.gates : [],
    hooks,
  };
}

/**
 * The consuming repository's config, normalised. The nearest one at or above `cwd`.
 *
 * Returns `{ found, root, name, apps, gates, hooks }`.
 */
export function loadConfig(cwd) {
  const path = findConfig(cwd);
  return path ? readConfig(path) : emptyConfig(cwd);
}

/**
 * Every config governing `start`, nearest first, bounded by the project directory.
 *
 * The guards read this rather than `loadConfig` so that a rule declared at the root still
 * holds inside an app that declares rules of its own. Order is nearest-first, which is the
 * order a reader would expect to see reasons reported in.
 */
export function loadConfigs(start, projectDir) {
  return configChain(start, projectDir).map(readConfig);
}
