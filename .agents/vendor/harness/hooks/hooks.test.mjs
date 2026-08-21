/**
 * The shared hooks' suite, run with `node --test`.
 *
 * This is the merge of four suites: `python-harness`'s `test_verify_hook.py`,
 * `test_hook_matchers.py`, `test_secret_paths.py` and `test_format_hook.py`, and the
 * frontend's 886-line `hooks.test.mjs`. Both repos kept these because every hook here fails
 * *silently* when it fails: a narrowed matcher still runs on the tools it matches, a dropped
 * pathspec entry still reports on the paths it names, and nothing says the guard went quiet.
 *
 * **Parameterised over both configs, not written against one.** Layer A is one
 * implementation reading N configs, so the behaviour tests run against a Python-shaped and a
 * Node-shaped `harness.config.json` — the two real consumers' shapes. A rule that only holds
 * for one of them is a rule that has quietly moved back into a stack.
 *
 * `node --test` rather than pytest or Vitest, deliberately. This suite has to run in a
 * Python repo, a pnpm repo, and this one, from a vendored tree no package manager has
 * visited. The only runner all three have is the one built into Node.
 */

import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { after, describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  CONFIG_NAME,
  configChain,
  findConfig,
  globToRegExp,
  loadConfig,
  relativePath,
  toolPaths,
} from './lib.mjs';
import {
  ALLOWED_FLOOR,
  SECRET,
  WRITE,
  allowedFor,
  blockReason,
  commandReason,
  guardedReason,
  guardsFor,
  repoConfigs,
  rulesFor,
  scopedPath,
  secretVarsFor,
} from './protect_paths.mjs';
import { commandsFor, formatPlan } from './format_edited.mjs';
import {
  STOP_KINDS,
  declaredPath,
  dispatch,
  gatedChange,
  isGated,
  missingNote,
  porcelainPath,
  skippedNote,
} from './verify.mjs';
import {
  DISTILLER_MARKER,
  LEGACY_OPENINGS,
  existingNote,
  firstUserMessage,
  isDistillerTranscript,
  learningsDirectory,
  noteBody,
  placeNote,
  priorBody,
  readNotes,
  rebuildIndex,
  shortId,
  splitSummary,
} from './session_learnings.mjs';
import { describe as describeNote, frontMatter, notes as vaultNotes } from './vault_index.mjs';

const HOOKS_DIR = dirname(fileURLToPath(import.meta.url));
const HOOKS_JSON = JSON.parse(readFileSync(join(HOOKS_DIR, 'hooks.json'), 'utf8'));

const temporary = [];
after(() => {
  for (const path of temporary) rmSync(path, { recursive: true, force: true });
});

/** A throwaway directory that the suite cleans up on the way out. */
function scratch() {
  const path = mkdtempSync(join(tmpdir(), 'harness-hooks-'));
  temporary.push(path);
  return path;
}

/**
 * The two shapes layer A actually serves, reduced to what the hooks read.
 *
 * Kept as fixtures rather than read from the real repos: this file is vendored into both of
 * them, and a suite that reads the config it is sitting next to can only ever test one shape.
 * Each stack pins its own real config in its own suite; this pins the behaviour.
 */
const CONFIGS = {
  'python-shaped': {
    name: 'python-harness',
    gates: [
      { name: 'ruff check', kind: 'lint', run: ['uv', 'run', 'ruff', 'check', '.'] },
      { name: 'mypy', kind: 'types', run: ['uv', 'run', 'mypy'] },
      { name: 'pytest', kind: 'test', run: ['uv', 'run', 'pytest'] },
      {
        name: 'pytest -m integration',
        kind: 'integration',
        run: ['uv', 'run', 'pytest', '-m', 'integration'],
        when: 'the change touches a repository or a migration',
      },
    ],
    hooks: {
      gatedPaths: ['src', 'tests'],
      gatedFiles: ['pyproject.toml', '.claude/settings.json', '.codex/hooks.json'],
      gatedExtensions: ['.py'],
      protected: [
        { glob: '**/migrations/**', why: 'schema migrations are irreversible', scope: 'write' },
        { glob: '**/generated/**', why: 'generated output; change the generator', scope: 'write' },
        { glob: 'uv.lock', why: 'regenerate with `uv lock`, never hand-edit', scope: 'write' },
      ],
      secretVars: ['LINEAR_API_KEY', 'GH_TOKEN'],
      formatters: [
        {
          match: ['.py'],
          run: [
            ['uv', 'run', 'ruff', 'format'],
            ['uv', 'run', 'ruff', 'check', '--fix', '--unfixable', 'F401'],
          ],
        },
      ],
    },
  },
  'node-shaped': {
    name: 'frontend-harness',
    gates: [
      { name: 'eslint', kind: 'lint', run: ['pnpm', 'lint'] },
      { name: 'tsc --noEmit', kind: 'types', run: ['pnpm', 'typecheck'] },
      { name: 'vitest', kind: 'test', run: ['pnpm', 'test'] },
      { name: 'vite build', kind: 'build', run: ['pnpm', 'build'] },
      {
        name: 'playwright',
        kind: 'e2e',
        run: ['pnpm', 'test:e2e'],
        when: 'the change alters user-visible behaviour',
      },
    ],
    hooks: {
      gatedPaths: ['src', 'e2e'],
      gatedFiles: ['package.json', 'tsconfig.json', '.mcp.json', '.claude/settings.json'],
      gatedExtensions: ['.ts', '.tsx', '.js', '.mjs', '.css'],
      protected: [
        { glob: 'pnpm-lock.yaml', why: 'regenerate with `pnpm install`', scope: 'write' },
        { glob: 'dist/**', why: 'build output; change the source instead', scope: 'write' },
        { glob: '**/__generated__/**', why: 'codegen output; change the schema', scope: 'write' },
        { glob: '**/*.gen.ts', why: 'generated output; change the generator', scope: 'write' },
      ],
      secretVars: ['LINEAR_API_KEY', 'GH_TOKEN'],
      formatters: [
        {
          match: ['.ts', '.tsx'],
          run: [
            ['pnpm', 'exec', 'prettier', '--write'],
            ['pnpm', 'exec', 'eslint', '--fix'],
          ],
        },
        { match: ['.md', '.json', '.css'], run: [['pnpm', 'exec', 'prettier', '--write']] },
      ],
    },
  },
};

/** The shape `loadConfig` returns, without touching disk. */
function normalise(raw) {
  const root = scratch();
  writeFileSync(join(root, CONFIG_NAME), JSON.stringify(raw), 'utf8');
  return loadConfig(root);
}

const LOADED = Object.fromEntries(
  Object.entries(CONFIGS).map(([label, raw]) => [label, normalise(raw)]),
);

describe('config discovery', () => {
  it('walks up for the nearest config, so a monorepo app finds its own', () => {
    const root = scratch();
    const app = join(root, 'apps', 'web', 'src');
    mkdirSync(app, { recursive: true });
    writeFileSync(join(root, CONFIG_NAME), '{"name":"root","gates":[]}', 'utf8');
    writeFileSync(join(root, 'apps', 'web', CONFIG_NAME), '{"name":"web","gates":[]}', 'utf8');

    assert.equal(loadConfig(app).name, 'web');
    assert.equal(loadConfig(root).name, 'root');
  });

  it('reports not-found rather than throwing when there is no config', () => {
    const config = loadConfig(scratch());
    assert.equal(config.found, false);
    assert.deepEqual(config.gates, []);
    assert.deepEqual(config.hooks.protected, []);
  });

  it('reports not-found rather than throwing when the config does not parse', () => {
    const root = scratch();
    writeFileSync(join(root, CONFIG_NAME), '{ this is not json', 'utf8');
    assert.equal(loadConfig(root).found, false);
  });

  it('ignores a key of the wrong type instead of crashing the hook', () => {
    const root = scratch();
    writeFileSync(
      join(root, CONFIG_NAME),
      '{"name":"x","gates":"nope","hooks":{"gatedPaths":7}}',
      'utf8',
    );
    const config = loadConfig(root);
    assert.equal(config.found, true);
    assert.deepEqual(config.gates, []);
    assert.deepEqual(config.hooks.gatedPaths, []);
  });

  it('finds nothing rather than walking past the filesystem root', () => {
    assert.equal(findConfig(scratch()), '');
  });

  it('collects the whole chain, nearest first, so a root rule survives an app config', () => {
    const root = scratch();
    const app = join(root, 'apps', 'web', 'src');
    mkdirSync(app, { recursive: true });
    writeFileSync(join(root, CONFIG_NAME), '{"name":"root","gates":[]}', 'utf8');
    writeFileSync(join(root, 'apps', 'web', CONFIG_NAME), '{"name":"web","gates":[]}', 'utf8');

    assert.deepEqual(configChain(app, root), [
      join(root, 'apps', 'web', CONFIG_NAME),
      join(root, CONFIG_NAME),
    ]);
  });

  it('stops at the project directory rather than inheriting from whatever sits above it', () => {
    const outer = scratch();
    const project = join(outer, 'project');
    mkdirSync(project, { recursive: true });
    writeFileSync(join(outer, CONFIG_NAME), '{"name":"outer","gates":[]}', 'utf8');
    writeFileSync(join(project, CONFIG_NAME), '{"name":"project","gates":[]}', 'utf8');

    assert.deepEqual(configChain(project, project), [join(project, CONFIG_NAME)]);
  });
});

describe('glob compilation', () => {
  it('confines a single star to one path segment', () => {
    const pattern = globToRegExp('.env.*');
    assert.ok(pattern.test('.env.local'));
    assert.ok(!pattern.test('.env.a/b'));
  });

  it('lets a leading double star match zero directories', () => {
    const pattern = globToRegExp('**/generated/**');
    assert.ok(pattern.test('generated/api.ts'));
    assert.ok(pattern.test('src/deep/generated/api.ts'));
  });

  it('does not treat a dot in a pattern as a regex wildcard', () => {
    assert.ok(!globToRegExp('.env').test('xenv'));
  });
});

describe('repo-relative paths', () => {
  it('strips the project directory without eating a leading dot', () => {
    assert.equal(relativePath('/repo/.env', '/repo'), '.env');
    assert.equal(relativePath('./.env', ''), '.env');
  });

  it('reads the direct file path used by Claude file tools', () => {
    assert.deepEqual(toolPaths({ tool_input: { file_path: 'src/a.ts' } }), ['src/a.ts']);
  });

  it('reads every path from a Codex apply_patch call', () => {
    const command = [
      '*** Begin Patch',
      '*** Update File: src/a.ts',
      '*** Add File: src/b.ts',
      '*** Delete File: src/c.ts',
      '*** End Patch',
    ].join('\n');
    assert.deepEqual(toolPaths({ tool_input: { command } }), ['src/a.ts', 'src/b.ts', 'src/c.ts']);
  });

  it('yields no paths for a shell command, so the command guard sees it instead', () => {
    assert.deepEqual(toolPaths({ tool_input: { command: 'cat .env' } }), []);
  });
});

describe('protected paths — the floor holds without any config', () => {
  const bare = loadConfig(scratch());
  const rules = rulesFor(bare);
  const allowed = allowedFor(bare);

  it('still refuses to write a secret file', () => {
    assert.ok(blockReason('.env', 'Write', rules, allowed));
    assert.ok(blockReason('.env.production', 'Write', rules, allowed));
  });

  it('still refuses to read a secret file', () => {
    assert.ok(blockReason('.env', 'Read', rules, allowed));
  });

  it('still exempts the committed template that documents the env contract', () => {
    assert.equal(blockReason('.env.example', 'Read', rules, allowed), null);
    assert.equal(blockReason('.env.example', 'Write', rules, allowed), null);
    assert.ok(ALLOWED_FLOOR.includes('.env.example'));
  });

  it('still refuses the shell readers a path rule cannot see', () => {
    assert.ok(commandReason('cat .env', []));
    assert.ok(commandReason('printenv', []));
    assert.ok(commandReason('node -e "console.log(process.env)"', []));
  });
});

for (const [label, config] of Object.entries(LOADED)) {
  describe(`protected paths — ${label}`, () => {
    const rules = rulesFor(config);
    const allowed = allowedFor(config);
    const declared = CONFIGS[label].hooks.protected;

    it('refuses a write to every path this repo declared', () => {
      for (const { glob } of declared) {
        const sample = glob
          .replaceAll('**/', 'src/')
          .replaceAll('/**', '/file.txt')
          .replaceAll('*', 'x');
        assert.ok(
          blockReason(sample, 'Write', rules, allowed),
          `${glob} declared protected but ${sample} was allowed`,
        );
      }
    });

    it('leaves reads of the write-protected paths alone', () => {
      for (const { glob, scope } of declared) {
        if (scope === SECRET) continue;
        const sample = glob
          .replaceAll('**/', 'src/')
          .replaceAll('/**', '/file.txt')
          .replaceAll('*', 'x');
        assert.equal(blockReason(sample, 'Read', rules, allowed), null, `${sample} blocked a read`);
      }
    });

    it('treats an unknown tool as a write, so a new write tool is covered by default', () => {
      assert.ok(blockReason('.env', 'mcp__something__brand_new', rules, allowed));
    });

    it('leaves ordinary source alone', () => {
      assert.equal(blockReason('src/app/main.py', 'Write', rules, allowed), null);
      assert.equal(blockReason('src/App.tsx', 'Write', rules, allowed), null);
    });

    it('refuses every route to a secret, not just the obvious one', () => {
      const { secretVars } = config.hooks;
      for (const command of [
        'env',
        'printenv',
        'set',
        'export -p',
        'declare -x',
        'compgen -e',
        'Get-ChildItem Env:',
        'gci Env:',
        'Get-Variable',
        'python3 -c "import os; print(os.environ)"',
        'uv run python -c "import os"',
        'node -e "process.env"',
        'node -p "process.env.GH_TOKEN"',
        'cat .env',
        'cat ./.env',
        'head .env',
        'Get-Content .env',
        'echo $GH_TOKEN',
        'echo "${LINEAR_API_KEY}"',
      ]) {
        assert.ok(commandReason(command, secretVars), `${command} was allowed through`);
      }
    });

    it('leaves ordinary shell commands alone', () => {
      const { secretVars } = config.hooks;
      for (const command of [
        'git status',
        'ls -la',
        'cat README.md',
        'pnpm test',
        'uv run pytest',
      ]) {
        assert.equal(commandReason(command, secretVars), null, `${command} was refused`);
      }
    });
  });

  describe(`Stop gate — ${label}`, () => {
    const { hooks, gates } = config;
    const raw = CONFIGS[label];

    it('names every gated location in the git pathspec', () => {
      // Asserted against the fixture's literals rather than against `hooks`: reading the
      // same constant the hook reads would make a dropped entry pass vacuously, while git
      // silently stopped reporting that directory and the gate went quiet.
      const captured = [];
      const runner = (_command, args) => {
        captured.push(args);
        return { status: 0, stdout: '', stderr: '', error: null };
      };
      gatedChangeWith(runner, hooks);
      for (const expected of [...raw.hooks.gatedPaths, ...raw.hooks.gatedFiles]) {
        assert.ok(captured[0].includes(expected), `${expected} missing from the git pathspec`);
      }
    });

    it('gates a change to source, to the hooks wiring, and to the gate config', () => {
      for (const path of [
        `${raw.hooks.gatedPaths[0]}/thing${raw.hooks.gatedExtensions[0]}`,
        raw.hooks.gatedFiles[0],
        '.claude/settings.json',
      ]) {
        assert.ok(isGated(path, hooks), `${path} should trigger the gates`);
      }
    });

    it('leaves prose ungated so writing work never burns override budget', () => {
      for (const path of ['README.md', 'docs/architecture.md', '.agents/plans/plan.md']) {
        assert.ok(!isGated(path, hooks), `${path} should not trigger the gates`);
      }
    });

    it('runs the fast gates and the build, and leaves the opt-in ones out', () => {
      const run = gates.filter((gate) => STOP_KINDS.has(gate.kind)).map((gate) => gate.kind);
      assert.ok(run.includes('lint') && run.includes('types') && run.includes('test'));
      assert.ok(!run.includes('e2e') && !run.includes('integration'));
    });

    it('names the gates it did not run, so a passing subset is not the whole gate', () => {
      const optIn = gates.filter((gate) => !STOP_KINDS.has(gate.kind));
      const note = skippedNote(gates);
      for (const gate of optIn) assert.ok(note.includes(gate.name), `${gate.name} unmentioned`);
      if (optIn.length === 0) assert.equal(note, '');
    });
  });

  describe(`formatters — ${label}`, () => {
    const { formatters } = config.hooks;

    it('appends the edited path to every declared command, in order', () => {
      const entry = CONFIGS[label].hooks.formatters[0];
      const path = `x${entry.match[0]}`;
      const commands = commandsFor(formatters, path);
      assert.deepEqual(
        commands,
        entry.run.map((argv) => [...argv, path]),
      );
    });

    it('runs nothing for an extension no entry claims', () => {
      assert.deepEqual(commandsFor(formatters, 'notes.rst'), []);
    });

    it('matches the extension case-insensitively', () => {
      const suffix = CONFIGS[label].hooks.formatters[0].match[0];
      assert.ok(commandsFor(formatters, `X${suffix.toUpperCase()}`).length > 0);
    });
  });
}

describe('Stop gate — mechanics', () => {
  it('reads the destination of a rename, which is the file that exists', () => {
    assert.equal(porcelainPath('R  src/old.ts -> src/new.ts'), 'src/new.ts');
  });

  it('unquotes a path git quoted for special characters', () => {
    assert.equal(porcelainPath('?? "src/a b.ts"'), 'src/a b.ts');
    assert.equal(porcelainPath('R  "src/old name.ts" -> "src/new name.ts"'), 'src/new name.ts');
  });

  it('does not block when git cannot say what changed', () => {
    const hooks = LOADED['python-shaped'].hooks;
    const failing = () => ({ status: 1, stdout: '', stderr: 'boom', error: null });
    assert.equal(gatedChangeWith(failing, hooks), false);
  });

  it('does not ask git anything when the repo declares no gated paths', () => {
    let asked = false;
    const runner = () => {
      asked = true;
      return { status: 0, stdout: '', stderr: '', error: null };
    };
    assert.equal(
      gatedChangeWith(runner, { gatedPaths: [], gatedFiles: [], gatedExtensions: [] }),
      false,
    );
    assert.equal(asked, false);
  });

  it('says nothing about skipped gates when every gate runs', () => {
    assert.equal(skippedNote([{ name: 'a', kind: 'lint', run: ['x'] }]), '');
  });

  // `git status --porcelain` reports repo-root-relative paths whatever directory it ran in.
  // A config declares its paths relative to itself. Reconciling the two is what makes
  // `gatedFiles` work at all inside an app, and getting it wrong is silent: the entry simply
  // never matches, and the gate stops noticing that file.
  it('reads a declared file as git would spell it, from inside an app', () => {
    assert.equal(declaredPath('apps/api', 'harness.config.json'), 'apps/api/harness.config.json');
    assert.equal(declaredPath('apps/api', '../../harness.config.json'), 'harness.config.json');
    assert.equal(declaredPath('', 'harness.config.json'), 'harness.config.json');
  });

  it('matches a gated file inside an app, and the root file it points up at', () => {
    const hooks = {
      gatedFiles: ['harness.config.json', '../../.claude/settings.json'],
      gatedExtensions: [],
    };
    assert.ok(isGated('apps/web/harness.config.json', hooks, 'apps/web'));
    assert.ok(isGated('.claude/settings.json', hooks, 'apps/web'));
    assert.ok(!isGated('apps/api/harness.config.json', hooks, 'apps/web'));
  });

  it('still matches an extension anywhere, which needs no prefix', () => {
    const hooks = { gatedFiles: [], gatedExtensions: ['.py'] };
    assert.ok(isGated('apps/api/src/main.py', hooks, 'apps/api'));
    assert.ok(isGated('src/main.py', hooks));
  });
});

/**
 * `gatedChange` shells out to git, which a unit test must not do. Rather than mock the
 * module, re-run its decision against an injected runner: the pathspec construction and the
 * `isGated` filter are the parts worth pinning, and both are visible here.
 */
function gatedChangeWith(runner, hooks) {
  const pathspec = [...hooks.gatedPaths, ...hooks.gatedFiles];
  if (pathspec.length === 0) return false;
  const result = runner('git', ['status', '--porcelain', '--', ...pathspec]);
  if (result.status !== 0) return false;
  return result.stdout
    .split(/\r?\n/)
    .filter(Boolean)
    .map(porcelainPath)
    .some((path) => isGated(path, hooks));
}

/**
 * The shape phase 6 scaffolds, reduced to what the hooks read: a router config at the root
 * that names its apps and declares nothing to run, and one config per app carrying that
 * app's own Definition of Done.
 *
 * `../../packages/contracts` in both apps' `gatedPaths` is the whole monorepo argument in
 * one line — the contract belongs to neither app, so a change to it puts both back in
 * scope, and no new key was needed to say so.
 */
const ROUTER = {
  name: 'acme-portal',
  apps: ['apps/api', 'apps/web'],
  hooks: {
    gatedFiles: ['harness.config.json'],
    protected: [
      { glob: 'packages/contracts/**', why: 'generated from the api schema', scope: 'write' },
    ],
    secretVars: ['LINEAR_API_KEY'],
  },
};

const API = {
  name: 'api',
  gates: [{ name: 'pytest', kind: 'test', run: ['uv', 'run', 'pytest'] }],
  hooks: {
    gatedPaths: ['src', 'tests', '../../packages/contracts'],
    gatedExtensions: ['.py'],
    protected: [{ glob: 'uv.lock', why: 'regenerate with `uv lock`', scope: 'write' }],
    secretVars: ['DATABASE_URL'],
    formatters: [{ match: ['.py'], run: [['uv', 'run', 'ruff', 'format']] }],
  },
};

const WEB = {
  name: 'web',
  gates: [{ name: 'vitest', kind: 'test', run: ['pnpm', 'test'] }],
  hooks: {
    gatedPaths: ['src', '../../packages/contracts'],
    gatedExtensions: ['.ts', '.tsx'],
    formatters: [{ match: ['.ts', '.tsx'], run: [['pnpm', 'exec', 'prettier', '--write']] }],
  },
};

/** A throwaway repository whose configs sit where the map says. */
function repoWith(configs) {
  const dir = scratch();
  for (const [relative, config] of Object.entries(configs)) {
    const path = relative ? join(dir, relative, CONFIG_NAME) : join(dir, CONFIG_NAME);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(config), 'utf8');
  }
  return dir;
}

const MONOREPO = { '': ROUTER, 'apps/api': API, 'apps/web': WEB };

describe('monorepo dispatch — one verify, gates per app', () => {
  it('runs one gate set per app and leaves a router config out of it', () => {
    const root = repoWith(MONOREPO);
    const { targets, missing } = dispatch(loadConfig(root));

    assert.deepEqual(
      targets.map((target) => target.name),
      ['api', 'web'],
    );
    assert.deepEqual(missing, []);
  });

  it('keeps a single-config repo exactly as it was, with no dispatch at all', () => {
    for (const config of Object.values(LOADED)) {
      const { targets, missing } = dispatch(config);
      assert.deepEqual(targets, [config]);
      assert.deepEqual(missing, []);
    }
  });

  it('runs the root first when the root has gates of its own', () => {
    const root = repoWith({
      ...MONOREPO,
      '': { ...ROUTER, gates: [{ name: 'prettier', kind: 'format', run: ['pnpm', 'format'] }] },
    });
    const { targets } = dispatch(loadConfig(root));
    assert.deepEqual(
      targets.map((target) => target.name),
      ['acme-portal', 'api', 'web'],
    );
  });

  it('reports an app with no config rather than running the root gates from its directory', () => {
    const root = repoWith({ '': ROUTER, 'apps/api': API });
    mkdirSync(join(root, 'apps', 'web'), { recursive: true });
    const { targets, missing } = dispatch(loadConfig(root));

    assert.deepEqual(
      targets.map((target) => target.name),
      ['api'],
    );
    assert.deepEqual(missing, ['apps/web']);
    assert.match(missingNote(missing), /apps\/web/);
    assert.equal(missingNote([]), '');
  });

  it("runs each app's gates in that app's own directory", () => {
    const root = repoWith(MONOREPO);
    const { targets } = dispatch(loadConfig(root));
    assert.deepEqual(
      targets.map((target) => relativePath(target.root, root)),
      ['apps/api', 'apps/web'],
    );
  });
});

describe('monorepo formatting — the file decides, not the session', () => {
  it("formats a Python file with the api's formatters and a tsx file with the web's", () => {
    const root = repoWith(MONOREPO);

    const python = formatPlan(join(root, 'apps', 'api', 'src', 'main.py'), root);
    assert.deepEqual(commandsFor(python.config.hooks.formatters, 'main.py'), [
      ['uv', 'run', 'ruff', 'format', 'main.py'],
    ]);

    const tsx = formatPlan(join(root, 'apps', 'web', 'src', 'App.tsx'), root);
    assert.deepEqual(commandsFor(tsx.config.hooks.formatters, 'App.tsx'), [
      ['pnpm', 'exec', 'prettier', '--write', 'App.tsx'],
    ]);
  });

  it('resolves a repo-relative path against the project, which is how Codex spells one', () => {
    const root = repoWith(MONOREPO);
    const plan = formatPlan('apps/api/src/main.py', root);

    assert.equal(plan.path, join(root, 'apps/api/src/main.py'));
    assert.equal(plan.config.name, 'api');
  });

  it('runs the formatter in the directory the config was found in', () => {
    const root = repoWith(MONOREPO);
    const plan = formatPlan(join(root, 'apps', 'web', 'src', 'App.tsx'), root);
    assert.equal(plan.config.root, join(root, 'apps', 'web'));
  });

  it('formats nothing when no config governs the file', () => {
    assert.equal(formatPlan(join(scratch(), 'src', 'x.ts'), scratch()).config, null);
  });
});

describe('monorepo protection — every config guards, each in its own scope', () => {
  const root = repoWith(MONOREPO);
  const configs = repoConfigs(root);
  const guards = guardsFor(configs, root);

  it('reads the root config and every app it names', () => {
    assert.deepEqual(
      configs.map((config) => config.name),
      ['acme-portal', 'api', 'web'],
    );
  });

  it('still applies a root rule inside an app that has rules of its own', () => {
    assert.ok(guardedReason('packages/contracts/openapi.yaml', 'Write', guards));
  });

  it("reads an app's bare glob as that app's file, not as any file anywhere", () => {
    assert.match(guardedReason('apps/api/uv.lock', 'Write', guards), /uv lock/);
    assert.equal(guardedReason('apps/web/uv.lock', 'Write', guards), null);
  });

  it('keeps the floor in every scope', () => {
    assert.ok(guardedReason('apps/web/.env', 'Read', guards));
    assert.ok(guardedReason('.env.local', 'Read', guards));
  });

  it('scopes a path to the config that declared the rule', () => {
    assert.equal(scopedPath('apps/api/uv.lock', 'apps/api'), 'uv.lock');
    assert.equal(scopedPath('apps/api/uv.lock', ''), 'apps/api/uv.lock');
    assert.equal(scopedPath('apps/web/uv.lock', 'apps/api'), null);
    assert.equal(scopedPath('apps/api', 'apps/api'), '');
  });

  it("refuses a command naming any config's secret variable, because a shell has no app", () => {
    assert.deepEqual(secretVarsFor(configs), ['LINEAR_API_KEY', 'DATABASE_URL']);
    assert.ok(commandReason('echo $DATABASE_URL', secretVarsFor(configs)));
  });

  it('leaves a single-config repo with exactly one guard', () => {
    const single = repoWith({ '': CONFIGS['python-shaped'] });
    const only = guardsFor(repoConfigs(single), single);
    assert.equal(only.length, 1);
    assert.equal(only[0].prefix, '');
  });
});

describe('the wiring covers the surface each guard needs', () => {
  /** Every matcher configured for `event` whose hooks run `script`. */
  function matchers(event, script) {
    return (HOOKS_JSON.hooks[event] ?? [])
      .filter((group) => JSON.stringify(group.hooks).includes(script))
      .map((group) => group.matcher)
      .filter(Boolean);
  }

  const WRITE_TOOLS = [
    'Edit',
    'Write',
    'NotebookEdit',
    'mcp__filesystem__write_file',
    'mcp__filesystem__edit_file',
    'mcp__memory__create_entities',
    'mcp__patch__apply_patch',
  ];

  it('gives every lifecycle event a hook', () => {
    for (const event of ['PreToolUse', 'PostToolUse', 'Stop', 'SessionEnd']) {
      assert.ok(HOOKS_JSON.hooks[event]?.length, `no ${event} hook is configured at all`);
    }
  });

  it('covers every tool that can write a file', () => {
    // A write tool outside the matcher is a write the guard never sees, and every other
    // test still passes. `re.search`-equivalent on purpose: the point is to pin which tool
    // names our config covers, not to re-implement the harness's matching rules.
    for (const tool of WRITE_TOOLS) {
      assert.ok(
        matchers('PreToolUse', 'protect_paths.mjs').some((pattern) =>
          new RegExp(pattern).test(tool),
        ),
        `the write guard does not cover ${tool}`,
      );
      assert.ok(
        matchers('PostToolUse', 'format_edited.mjs').some((pattern) =>
          new RegExp(pattern).test(tool),
        ),
        `the formatter does not cover ${tool}`,
      );
    }
  });

  it('covers the whole secret surface, which is what defect 1 was', () => {
    // `protect_secrets.py` shipped wired into the Codex adapter and nowhere else, so Claude
    // Code had no read-side guard at all — and no test could tell, because "PreToolUse
    // ignores reads" was the property being asserted.
    for (const tool of ['Read', 'Bash']) {
      assert.ok(
        matchers('PreToolUse', 'protect_paths.mjs').some((pattern) =>
          new RegExp(pattern).test(tool),
        ),
        `a secret can be reached through ${tool} without the guard seeing the call`,
      );
    }
  });

  it('does not run the formatter on a read', () => {
    for (const tool of ['Read', 'Grep', 'Glob', 'Bash']) {
      assert.ok(
        !matchers('PostToolUse', 'format_edited.mjs').some((p) => new RegExp(p).test(tool)),
        `the formatter fires on the read-only tool ${tool}`,
      );
    }
  });

  it('addresses every hook through the plugin root, which survives a worktree', () => {
    const commands = JSON.stringify(HOOKS_JSON.hooks);
    for (const script of [
      'protect_paths.mjs',
      'format_edited.mjs',
      'verify.mjs',
      'session_learnings.mjs',
    ]) {
      assert.ok(
        commands.includes(`\${CLAUDE_PLUGIN_ROOT}/hooks/${script}`),
        `${script} is not addressed through CLAUDE_PLUGIN_ROOT`,
      );
    }
  });

  it('gives the distiller a budget the distiller can actually use', () => {
    // `claude -p` takes minutes. A SessionEnd hook killed at its timeout looks exactly like
    // a session that taught nothing.
    const [group] = HOOKS_JSON.hooks.SessionEnd;
    assert.ok(group.hooks[0].timeout >= 300, 'the SessionEnd budget is too short to distil');
  });
});

describe('session learnings — note identity', () => {
  it('derives the learnings directory from the Obsidian vault root', () => {
    assert.equal(
      learningsDirectory({ OBSIDIAN_VAULT_DIRECTORY: '/v' }),
      join('/v', 'Project Learnings'),
    );
    assert.equal(learningsDirectory({}), '');
  });

  it('reads a flat front-matter field and ignores the body', () => {
    const text = '---\nsession: abc123\ndate: 2026-08-19 10:00\n---\n\n# h\n\nsession: not-this\n';
    assert.equal(frontMatter(text).session, 'abc123');
  });

  it('compares notes on their body, not their heading or date', () => {
    const a = '---\ndate: 1\n---\n\n# repo — session learnings (1)\n\nthe lesson\n';
    const b = '---\ndate: 2\n---\n\n# repo — session learnings (2)\n\nthe lesson\n';
    assert.equal(noteBody(a), noteBody(b));
  });

  it('splits the summary line off the body', () => {
    assert.deepEqual(splitSummary('SUMMARY: topics here\n\n## One\n'), ['topics here', '## One\n']);
    assert.deepEqual(splitSummary('## One\n'), ['', '## One\n']);
  });

  it('reduces a session id to the eight characters a filename carries', () => {
    assert.equal(shortId('abc-123-def-456'), 'abc123de');
    assert.equal(shortId(''), 'session');
  });

  it('finds the earlier note for this session, and nothing else', () => {
    const notes = [
      { path: 'a.md', key: shortId('mine'), session: 'mine', date: '1', body: 'lesson' },
      { path: 'b.md', key: shortId('other'), session: 'other', date: '2', body: 'other lesson' },
    ];
    assert.equal(existingNote(notes, 'mine').path, 'a.md');
    assert.equal(existingNote(notes, 'unseen'), undefined);
    assert.equal(priorBody(notes, 'mine'), 'lesson');
    assert.equal(priorBody(notes, 'unseen'), '');
  });

  it('rewrites the note this session already has, keeping its name and date', () => {
    const notes = [
      {
        path: 'a.md',
        key: shortId('mine'),
        session: 'mine',
        date: '2026-08-01 09:00',
        body: 'old',
      },
    ];
    const placed = placeNote(notes, 'new', 'mine', 'fallback.md');
    assert.equal(placed.target, 'a.md');
    assert.equal(placed.date, '2026-08-01 09:00');
    assert.equal(placed.skip, null);
  });

  it('writes nothing when the session ends again having learned nothing new', () => {
    const notes = [
      { path: 'a.md', key: shortId('mine'), session: 'mine', date: '1', body: 'same' },
    ];
    assert.match(placeNote(notes, 'same', 'mine', 'fallback.md').skip, /^unchanged/);
  });

  it('writes nothing when another session already holds the same body', () => {
    const notes = [
      { path: 'a.md', key: shortId('other'), session: 'other', date: '1', body: 'same' },
    ];
    assert.match(placeNote(notes, 'same', 'mine', 'fallback.md').skip, /^duplicate of/);
  });

  it('falls back to the dated path for a session it has not seen', () => {
    assert.deepEqual(placeNote([], 'body', 'mine', 'fallback.md'), {
      target: 'fallback.md',
      date: '',
      skip: null,
    });
  });

  it('keys a note written before the session field existed on its filename suffix', () => {
    const directory = scratch();
    writeFileSync(join(directory, '2026-08-01 repo abc12345.md'), '# repo\n\nthe lesson\n', 'utf8');
    const notes = readNotes(directory);
    assert.equal(notes.length, 1);
    assert.equal(notes[0].key, 'abc12345');
    assert.ok(existingNote(notes, 'abc12345-and-more'));
  });

  it('leaves the generated indexes out of the notes it reads', () => {
    const directory = scratch();
    writeFileSync(join(directory, '_INDEX.md'), '# index\n', 'utf8');
    writeFileSync(join(directory, '_hook.log'), 'noise\n', 'utf8');
    writeFileSync(join(directory, '2026-08-01 repo abc12345.md'), '# repo\n\nlesson\n', 'utf8');
    assert.deepEqual(
      readNotes(directory).map((n) => n.key),
      ['abc12345'],
    );
  });
});

describe('session learnings — recursion guard', () => {
  const transcript = (first) =>
    [
      JSON.stringify({ type: 'system', message: { role: 'system', content: 'boot' } }),
      JSON.stringify({ message: { role: 'user', content: [{ type: 'text', text: first }] } }),
    ].join('\n');

  it('recognises a distillation run from its own transcript', () => {
    assert.ok(isDistillerTranscript(transcript(`${DISTILLER_MARKER}\n\nrest of the prompt`)));
  });

  it('recognises the runs both predecessor implementations started', () => {
    for (const opening of LEGACY_OPENINGS) {
      assert.ok(
        isDistillerTranscript(transcript(`${opening} and then some`)),
        `a ${opening} transcript would be distilled into a copy of a note the vault has`,
      );
    }
    assert.equal(LEGACY_OPENINGS.length, 2, 'both predecessors must stay recognised');
  });

  it('leaves a session that only talks about this hook alone', () => {
    // These repos edit this file, so a marker appears as ordinary text in real sessions
    // about it. A skipped note looks exactly like a session that taught nothing.
    const raw = transcript(`why does ${DISTILLER_MARKER} appear twice in the prompt?`);
    assert.equal(isDistillerTranscript(raw), false);
  });

  it('reads past a leading system entry to find the first user message', () => {
    assert.equal(firstUserMessage(transcript('hello there')), 'hello there');
  });

  it('does not scan a whole transcript looking for the marker', () => {
    const padding = Array.from({ length: 200 }, (_, i) =>
      JSON.stringify({ message: { role: 'assistant', content: `line ${i}` } }),
    );
    const raw = [
      ...padding,
      JSON.stringify({ message: { role: 'user', content: DISTILLER_MARKER } }),
    ].join('\n');
    assert.equal(isDistillerTranscript(raw), false);
  });
});

describe('second brain — one writer and one indexer', () => {
  it('rebuilds the learnings index from every note front matter', () => {
    const directory = scratch();
    writeFileSync(
      join(directory, '2026-08-01 repo aaaaaaaa.md'),
      '---\ndate: 2026-08-01 09:00\nproject: repo\nsummary: first lesson\n---\n\n# h\n\nbody\n',
      'utf8',
    );
    writeFileSync(
      join(directory, '2026-08-02 other bbbbbbbb.md'),
      '---\ndate: 2026-08-02 09:00\nproject: other\nsummary: second | lesson\n---\n\n# h\n\nbody\n',
      'utf8',
    );
    rebuildIndex(directory);

    const index = readFileSync(join(directory, '_INDEX.md'), 'utf8');
    assert.match(index, /^---\ntags: \[project-learnings-index\]/);
    assert.match(index, /2 notes\./);
    assert.match(index, /\[\[2026-08-02 other bbbbbbbb\]\]/);
    // A pipe in a summary would otherwise split the table row into extra cells.
    assert.match(index, /second \\\| lesson/);
    // Newest first: the index is read top-down and the recent lesson is the likely answer.
    assert.ok(index.indexOf('bbbbbbbb') < index.indexOf('aaaaaaaa'));
  });

  it('rebuilds rather than appends, so a deleted note leaves the index', () => {
    const directory = scratch();
    const note = join(directory, '2026-08-01 repo aaaaaaaa.md');
    writeFileSync(note, '---\ndate: 1\nproject: repo\nsummary: s\n---\n\n# h\n\nbody\n', 'utf8');
    rebuildIndex(directory);
    assert.match(readFileSync(join(directory, '_INDEX.md'), 'utf8'), /1 notes\./);

    rmSync(note);
    rebuildIndex(directory);
    assert.match(readFileSync(join(directory, '_INDEX.md'), 'utf8'), /0 notes\./);
  });

  it('indexes the whole vault, not only the learnings folder', () => {
    const vault = scratch();
    mkdirSync(join(vault, 'Project Learnings'), { recursive: true });
    mkdirSync(join(vault, '.obsidian'), { recursive: true });
    writeFileSync(
      join(vault, 'hand written.md'),
      'A long enough line of ordinary prose here.\n',
      'utf8',
    );
    writeFileSync(
      join(vault, 'Project Learnings', 'a.md'),
      '---\nsummary: s\n---\n\nbody\n',
      'utf8',
    );
    writeFileSync(join(vault, '.obsidian', 'workspace.md'), 'editor config\n', 'utf8');
    writeFileSync(join(vault, '_VAULT_INDEX.md'), 'generated\n', 'utf8');

    const found = vaultNotes(vault);
    assert.deepEqual(found.sort(), ['Project Learnings/a.md', 'hand written.md'].sort());
  });

  it('describes a note with no front matter from its first line of real prose', () => {
    assert.equal(
      describeNote(
        '# Heading\n\n- bullet\n\nA long enough line of ordinary prose to describe it.\n',
      ),
      'A long enough line of ordinary prose to describe it.',
    );
  });

  it('falls back to the outline for a note that is only bullets', () => {
    assert.equal(
      describeNote('# Title\n\n- one thing\n- another thing\n'),
      'Title · one thing · another thing',
    );
  });
});
