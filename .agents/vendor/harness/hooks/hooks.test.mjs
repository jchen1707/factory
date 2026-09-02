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
import { spawnSync } from 'node:child_process';
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
  EXIT,
  REPORT_SCHEMA_VERSION,
  buildReport,
  classifyRun,
  requirementMet,
  computeVerdict,
  exitCode,
  parseArgs,
} from './gate_report.mjs';
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

describe('gate report — base diff (factory post-commit)', () => {
  // The defect this exists to pin: the factory commits the agent's work before it verifies,
  // so the working tree is clean. `gatedChange` reads `git status --porcelain` (uncommitted
  // edits) and sees nothing — every gate would be skipped_unchanged regardless of what the
  // change touched. `gatedChangeSince` diffs `<base>..HEAD` and sees the committed change.
  // A real run on BAC-5 found this; only a real run could, because the fakes in the suite
  // above all inject `isChanged` directly and never exercised the post-commit tree.
  const hooks = {
    gatedPaths: ['src'],
    gatedFiles: ['harness.config.json'],
    gatedExtensions: ['.py'],
  };

  it('detects a committed gated change that a clean working tree hides from gatedChange', () => {
    // Clean working tree: `git status` is empty (the work is committed).
    const statusRunner = () => ({ status: 0, stdout: '', stderr: '', error: null });
    assert.equal(gatedChangeWith(statusRunner, hooks), false); // the defect: nothing seen.

    // Same change, now committed: `git diff <base>..HEAD` names it.
    const diffRunner = (_cmd, args) => {
      assert.equal(args[0], 'diff');
      assert.ok(args.includes('origin/v2..HEAD'), 'diffs the base ref against HEAD');
      return { status: 0, stdout: 'src/app/ai/retrieval/ingestion.py\n', stderr: '', error: null };
    };
    assert.equal(gatedChangeWith(diffRunner, hooks, { base: 'origin/v2' }), true);
  });

  it('reads the destination of a rename, which is the file that exists', () => {
    const diffRunner = () => ({
      status: 0,
      stdout: 'src/old.py\tsrc/new.py\n',
      stderr: '',
      error: null,
    });
    assert.equal(gatedChangeWith(diffRunner, hooks, { base: 'origin/v2' }), true);
  });

  it('is false when the diff touches only ungated prose', () => {
    const diffRunner = () => ({
      status: 0,
      stdout: 'docs/architecture.md\nREADME.md\n',
      stderr: '',
      error: null,
    });
    assert.equal(gatedChangeWith(diffRunner, hooks, { base: 'origin/v2' }), false);
  });

  it('reads the working tree, not a diff, when no base ref is given', () => {
    // The Stop hook's path. Previously this asserted that a separate `gatedChangeSince`
    // refused to run without a base; there is one function now, and what matters is which
    // question it asks git — a `diff` here would silently re-scope the Stop gate.
    let argv = null;
    const runner = (_cmd, args) => {
      argv = args;
      return { status: 0, stdout: '', stderr: '', error: null };
    };
    assert.equal(gatedChangeWith(runner, hooks, { base: '' }), false);
    assert.equal(argv[0], 'status');
    assert.ok(argv.includes('--porcelain'));
  });

  it('does not block when git cannot produce the diff', () => {
    const failing = () => ({ status: 1, stdout: '', stderr: 'boom', error: null });
    assert.equal(gatedChangeWith(failing, hooks, { base: 'origin/v2' }), false);
  });

  it('parses --base <ref> and --base=<ref>', () => {
    assert.equal(parseArgs(['--base', 'origin/v2']).base, 'origin/v2');
    assert.equal(parseArgs(['--base=origin/v2']).base, 'origin/v2');
    assert.equal(parseArgs([]).base, '');
    // Existing flags still parse alongside it.
    assert.deepEqual(parseArgs(['--gate', 'playwright']).gates, ['playwright']);
    assert.deepEqual(parseArgs(['--gate=playwright']).gates, ['playwright']);
    assert.deepEqual(
      parseArgs(['--gate', 'playwright', '--gate', 'pg']).gates,
      ['playwright', 'pg'],
      'repeatable, and order is preserved',
    );
    assert.deepEqual(parseArgs([]).gates, []);
    // An empty value asserts nothing rather than a gate named ''. The argv came from a
    // caller interpolating a name it did not have.
    assert.deepEqual(parseArgs(['--gate', '']).gates, []);
    assert.deepEqual(parseArgs(['--gate=']).gates, []);

    const mixed = parseArgs(['--all', '--json', '--base', 'main', '--cwd', '/tmp']);
    assert.equal(mixed.all && mixed.json, true);
    assert.equal(mixed.base, 'main');
    assert.equal(mixed.cwd, '/tmp');
  });
});

describe('gate report — base diff integration (real git)', () => {
  // The one test the fakes above cannot stand in for: run the real `gate_report.mjs --base`
  // against a temp repo whose working tree is clean (the work is committed) and assert the
  // gate *runs* rather than `skipped_unchanged`. On the unfixed code `--base` is ignored,
  // `isChanged` falls back to `git status --porcelain` (empty, post-commit), and the gate is
  // skipped — exactly the BAC-5 evidence-mismatch. This is the failing-first proof.
  const hook = join(dirname(fileURLToPath(import.meta.url)), 'gate_report.mjs');
  const gatesRoots = [];

  function git(cwd, ...args) {
    const r = spawnSync('git', args, { cwd, encoding: 'utf8' });
    assert.equal(r.status, 0, `git ${args.join(' ')} failed: ${r.stderr}`);
    return r.stdout.trim();
  }

  function repo() {
    const root = mkdtempSync(join(tmpdir(), 'gate-report-base-'));
    gatesRoots.push(root);
    const cfg = {
      name: 'temp',
      gates: [{ name: 'true-gate', kind: 'lint', run: ['true'] }],
      hooks: { gatedPaths: ['src'], gatedFiles: [], gatedExtensions: ['.py'] },
    };
    writeFileSync(join(root, 'harness.config.json'), JSON.stringify(cfg));
    git(root, 'init', '-q');
    git(root, 'config', 'user.email', 't@t');
    git(root, 'config', 'user.name', 't');
    git(root, 'add', '-A');
    git(root, 'commit', '-qm', 'base');
    return root;
  }

  after(() => gatesRoots.forEach((d) => rmSync(d, { recursive: true, force: true })));

  it('runs the gate for a committed change a clean working tree hides without --base', () => {
    const root = repo();
    // Add a gated file and commit it — the working tree is clean afterwards.
    mkdirSync(join(root, 'src'));
    writeFileSync(join(root, 'src', 'foo.py'), 'x = 1\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-qm', 'add foo');
    assert.equal(git(root, 'status', '--porcelain'), ''); // clean: git status sees nothing.

    const base = git(root, 'rev-parse', 'HEAD~1');
    const r = spawnSync('node', [hook, '--base', base, '--json', '--cwd', root], {
      encoding: 'utf8',
    });
    assert.equal(r.status, 0, r.stderr);
    const report = JSON.parse(r.stdout);
    assert.equal(report.verdict, 'pass');
    assert.equal(report.gates[0].name, 'true-gate');
    assert.equal(report.gates[0].status, 'pass', 'the committed change ran the gate');
    assert.notEqual(report.gates[0].status, 'skipped_unchanged');
  });

  it('skips the gate when no --base is given and the working tree is clean', () => {
    // The contrast that is the defect: without --base, a committed change is invisible to
    // `git status`, so the gate is skipped_unchanged. This pins the behaviour the factory
    // must not regress to once it always passes --base.
    const root = repo();
    mkdirSync(join(root, 'src'));
    writeFileSync(join(root, 'src', 'foo.py'), 'x = 1\n');
    git(root, 'add', '-A');
    git(root, 'commit', '-qm', 'add foo');

    const r = spawnSync('node', [hook, '--json', '--cwd', root], { encoding: 'utf8' });
    assert.equal(r.status, 0, r.stderr);
    const report = JSON.parse(r.stdout);
    assert.equal(report.gates[0].status, 'skipped_unchanged');
  });
});

/**
 * The real `gatedChange`, driven with a fake `exec` so the suite never shells out to git.
 *
 * These were two hand-written re-implementations of the function's body — one per mode —
 * because the `git` call was welded in and there was nothing to inject. That made the two
 * things a unit test must not do into a single choice: shell out, or test a copy. The copy
 * won, and it meant a change to the real pathspec construction left this suite green.
 * `exec` is the seam that removes the choice, and it is the same one `buildReport` takes.
 */
function gatedChangeWith(exec, hooks, options = {}) {
  return gatedChange('.', hooks, '', { ...options, exec });
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

/**
 * A fake gate run result — the shape `runArgv` returns, plus the `durationMs` a real run
 * measures. `buildReport` takes `runGate` as an injection, so the report logic runs here
 * against these with no `git` and no toolchain, the same way `gatedChangeWith` keeps the
 * Stop gate's pathspec test off the shell.
 */
function runResult(status, { stdout = '', stderr = '', error = null } = {}) {
  return { status, stdout, stderr, error, durationMs: 9 };
}

/** A single-config repo dispatched, ready to hand to `buildReport`. */
function dispatched(raw) {
  const root = normalise(raw);
  return { root, ...dispatch(root) };
}

/** Build a report for `rootDir` with injected side effects, so no subprocess runs. */
function reportFrom(
  rootDir,
  { all = false, changed = () => true, runGate = () => runResult(0) } = {},
) {
  const root = loadConfig(rootDir);
  const { targets, missing } = dispatch(root);
  return buildReport({ root, targets, missing, all, isChanged: changed, runGate });
}

describe('gate report — classification', () => {
  it('classifies a gate that started and exited zero as pass', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'g', kind: 'lint', run: ['true'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0, { stdout: 'all good\n' }),
    });
    assert.equal(report.gates[0].status, 'pass');
    assert.equal(report.gates[0].exit, 0);
    assert.equal(report.gates[0].outputTail, ''); // A pass carries no output tail.
    assert.equal(report.gates[0].durationMs, 9);
  });

  it('classifies a non-zero exit as fail and captures the tail of its output', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'g', kind: 'test', run: ['false'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(1, { stdout: 'line1\nline2\nFAILED here\n' }),
    });
    assert.equal(report.gates[0].status, 'fail');
    assert.equal(report.gates[0].exit, 1);
    assert.match(report.gates[0].outputTail, /FAILED here/);
  });

  it('classifies a process that could not be spawned as unavailable, not fail', () => {
    // The case verify.mjs swallows (it returns 0 so a tooling problem does not wedge the
    // turn) and a report must not. `unavailable` is the whole reason `verdict: incomplete`
    // exists — a green exit code must not stand in for "the gate could not run."
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'g', kind: 'build', run: ['missing-binary-xyz'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(null, { error: new Error('spawnSync missing-binary-xyz') }),
    });
    assert.equal(report.gates[0].status, 'unavailable');
    assert.equal(report.gates[0].exit, null); // No process, no exit code.
    assert.match(report.gates[0].outputTail, /spawnSync missing-binary-xyz/);
  });

  it('classifies the error from a run result directly', () => {
    assert.equal(classifyRun({ status: 0, error: null }), 'pass');
    assert.equal(classifyRun({ status: 2, error: null }), 'fail');
    assert.equal(classifyRun({ status: null, error: new Error('x') }), 'unavailable');
  });

  it('reports a gate whose `requires` probe fails as unavailable, and never runs the gate', () => {
    // The defect this closes: a missing browser makes `pnpm test:e2e` exit non-zero exactly
    // the way a real regression does. Reported as `fail`, an agent spends an attempt trying
    // to fix code that is not broken, and writing code never installs a browser.
    let ran = false;
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        {
          name: 'playwright',
          kind: 'e2e',
          run: ['pnpm', 'test:e2e'],
          requires: ['pnpm', 'exec', 'playwright', '--version'],
          caveat: 'needs browsers installed',
        },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      all: true,
      isChanged: () => true,
      runGate: () => {
        ran = true;
        return runResult(1, { stdout: 'Executable does not exist\n' });
      },
      probeGate: () => runResult(1, { stderr: 'Please run: playwright install\n' }),
    });
    assert.equal(ran, false, 'the gate must not run once its requirement is unmet');
    assert.equal(report.gates[0].status, 'unavailable');
    assert.equal(report.gates[0].exit, null); // The gate never ran, so it has no exit code.
    assert.equal(report.gates[0].durationMs, null);
    assert.match(report.gates[0].outputTail, /playwright install/);
    assert.equal(report.gates[0].caveat, 'needs browsers installed');
    // And the verdict follows `unavailable`, so a green exit code cannot stand in for it.
    assert.equal(report.verdict, 'incomplete');
    assert.equal(exitCode(report.verdict), 3);
  });

  it('runs the gate normally when its `requires` probe passes', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        {
          name: 'playwright',
          kind: 'e2e',
          run: ['pnpm', 'test:e2e'],
          requires: ['pnpm', 'exec', 'playwright', '--version'],
        },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      all: true,
      isChanged: () => true,
      runGate: () => runResult(0),
      probeGate: () => runResult(0, { stdout: '1.55.0\n' }),
    });
    assert.equal(report.gates[0].status, 'pass');
    assert.equal(report.verdict, 'pass');
  });

  it('keeps a real failure a `fail` when the requirement is met', () => {
    // The other half of the same judgment. If a met requirement did not leave `fail` alone,
    // this mechanism would hide the failures it exists to distinguish itself from.
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'playwright', kind: 'e2e', run: ['pnpm', 'test:e2e'], requires: ['pw', '-v'] },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      all: true,
      isChanged: () => true,
      runGate: () => runResult(1, { stdout: '1 test failed\n' }),
      probeGate: () => runResult(0),
    });
    assert.equal(report.gates[0].status, 'fail');
    assert.equal(report.gates[0].exit, 1);
    assert.equal(report.verdict, 'fail');
  });

  it('does not probe a gate that was never going to run', () => {
    // A `requires` probe is a subprocess. Spending one to answer a question that
    // `not_applicable` already settled is pure cost, and on the opt-in kinds -- the only
    // ones that carry `requires` in practice -- that is the common case, not the rare one.
    let probes = 0;
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'playwright', kind: 'e2e', run: ['pw'], requires: ['pw', '-v'] },
        {
          name: 'off',
          kind: 'integration',
          run: ['pg'],
          requires: ['docker', 'info'],
          enabled: false,
        },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true, // changed, but nothing asserted the opt-in gates
      runGate: () => runResult(0),
      probeGate: () => {
        probes += 1;
        return runResult(0);
      },
    });
    assert.equal(probes, 0);
    assert.equal(report.gates[0].status, 'not_applicable');
    assert.equal(report.gates[1].status, 'disabled');
  });

  it('leaves a gate with no `requires` exactly as it was', () => {
    // Every config predating this field declares gates that simply run. The default
    // injection is what keeps that true for callers that pass no `probeGate` at all.
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'ruff', kind: 'lint', run: ['ruff', 'check'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.equal(report.gates[0].status, 'pass');
  });

  it('treats a `requires` probe that could not be spawned as unmet, not as met', () => {
    // The probe is the cheaper of the two commands. If it cannot start, the gate's own
    // toolchain is not there either; guessing "probably fine" puts the expensive wrong
    // answer -- a `fail` that is really a missing tool -- straight back on the table.
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'pg', kind: 'integration', run: ['pytest'], requires: ['docker', 'info'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      all: true,
      isChanged: () => true,
      runGate: () => runResult(0),
      probeGate: () => runResult(null, { error: new Error('spawnSync docker ENOENT') }),
    });
    assert.equal(report.gates[0].status, 'unavailable');
    assert.match(report.gates[0].outputTail, /ENOENT/);
  });

  it('names the requirement in the tail when the probe said nothing at all', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'pg', kind: 'integration', run: ['pytest'], requires: ['docker', 'info'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      all: true,
      isChanged: () => true,
      runGate: () => runResult(0),
      probeGate: () => runResult(1), // non-zero, no stdout, no stderr, no error
    });
    assert.equal(report.gates[0].status, 'unavailable');
    assert.equal(report.gates[0].outputTail, 'requires: docker info');
  });

  it('ignores an empty `requires`, which asserts nothing', () => {
    let probes = 0;
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'g', kind: 'lint', run: ['true'], requires: [] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
      probeGate: () => {
        probes += 1;
        return runResult(1);
      },
    });
    assert.equal(probes, 0);
    assert.equal(report.gates[0].status, 'pass');
  });

  it('decides a requirement from a probe result directly', () => {
    assert.equal(requirementMet({ status: 0, error: null }), true);
    assert.equal(requirementMet({ status: 1, error: null }), false);
    assert.equal(requirementMet({ status: null, error: new Error('x') }), false);
  });

  it('marks e2e and integration not_applicable without --all, and runs them with it', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'playwright', kind: 'e2e', run: ['pw'], when: 'the change is user-visible' },
        { name: 'pg', kind: 'integration', run: ['pg'], when: 'the change touches a migration' },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const off = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.deepEqual(
      off.gates.map((g) => g.status),
      ['not_applicable', 'not_applicable'],
    );
    // The `when` clause travels with the row, so a reader knows when it stops being optional.
    assert.equal(off.gates[0].when, 'the change is user-visible');

    const on = buildReport({
      root,
      targets,
      missing,
      all: true,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.deepEqual(
      on.gates.map((g) => g.status),
      ['pass', 'pass'],
    );
  });

  it('asserts exactly the opt-in gates named by --gate, and no others', () => {
    // The FRO-7 case, as a test. An agent that honestly ran `playwright` must not thereby
    // assert `lighthouse`'s unrelated `when` clause — which is prose nothing here can
    // evaluate, and which was false for that change. Before per-gate assertion the only
    // way to run one opt-in gate was `--all`, which ran both.
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'playwright', kind: 'e2e', run: ['pw'], when: 'the change is user-visible' },
        {
          name: 'lighthouse',
          kind: 'integration',
          run: ['lhci'],
          when: 'performance budgets are in scope',
        },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const ran = [];
    const report = buildReport({
      root,
      targets,
      missing,
      gates: ['playwright'],
      isChanged: () => true,
      runGate: (gate) => {
        ran.push(gate.name);
        return runResult(0);
      },
    });
    assert.deepEqual(
      report.gates.map((g) => [g.name, g.status]),
      [
        ['playwright', 'pass'],
        ['lighthouse', 'not_applicable'],
      ],
    );
    // The unasserted gate is not merely reported unrun — it never executed. A gate that
    // cannot pass on this machine must not be able to fail the verdict from a claim that
    // was never about it.
    assert.deepEqual(ran, ['playwright']);
    assert.equal(report.verdict, 'pass');
  });

  it('treats --all as asserting every opt-in gate, and --gate as additive to it', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'playwright', kind: 'e2e', run: ['pw'], when: 'user-visible' },
        { name: 'lighthouse', kind: 'integration', run: ['lhci'], when: 'perf budgets' },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const all = buildReport({
      root,
      targets,
      missing,
      all: true,
      gates: ['playwright'],
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.deepEqual(
      all.gates.map((g) => g.status),
      ['pass', 'pass'],
    );
  });

  it('ignores an asserted gate name this repo does not declare', () => {
    // A caller naming a gate that does not exist has asserted nothing, which is the same
    // nothing as not naming it. It must not throw and must not turn some other gate on.
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'playwright', kind: 'e2e', run: ['pw'], when: 'user-visible' }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      gates: ['cypress'],
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.deepEqual(
      report.gates.map((g) => g.status),
      ['not_applicable'],
    );
  });

  it('reports a gate with enabled:false as disabled, and never runs it', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'eslint', kind: 'lint', run: ['lint'] },
        { name: 'lighthouse', kind: 'integration', run: ['lhci'], enabled: false },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const ran = [];
    const report = buildReport({
      root,
      targets,
      missing,
      all: true, // even a blanket assertion must not switch a disabled gate back on
      gates: ['lighthouse'], // nor an explicit one
      isChanged: () => true,
      runGate: (gate) => {
        ran.push(gate.name);
        return runResult(0);
      },
    });
    assert.deepEqual(
      report.gates.map((g) => [g.name, g.status]),
      [
        ['eslint', 'pass'],
        ['lighthouse', 'disabled'],
      ],
    );
    assert.deepEqual(ran, ['eslint'], 'a disabled gate must not execute');
    // Off is not broken: a disabled gate cannot drag the verdict to fail or incomplete.
    assert.equal(report.verdict, 'pass');
  });

  it('still reports a disabled gate when the app was unchanged', () => {
    // `disabled` outranks `skipped_unchanged`: the operator's answer does not depend on
    // what the turn happened to touch, and a reader comparing two reports should not see
    // a gate's reason for not running flicker.
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'lighthouse', kind: 'integration', run: ['lhci'], enabled: false }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => false,
      runGate: () => runResult(0),
    });
    assert.equal(report.gates[0].status, 'disabled');
  });

  it('treats an absent or true `enabled` as on', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'absent', kind: 'lint', run: ['a'] },
        { name: 'explicit', kind: 'lint', run: ['b'], enabled: true },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.deepEqual(
      report.gates.map((g) => g.status),
      ['pass', 'pass'],
    );
  });

  it('marks every gate of an untouched app skipped_unchanged', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'g', kind: 'lint', run: ['true'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => false, // gatedChange() was false — the turn touched nothing gated.
      runGate: () => {
        throw new Error('an untouched app must not run its gates');
      },
    });
    assert.equal(report.gates[0].status, 'skipped_unchanged');
    assert.equal(report.gates[0].exit, null);
  });

  it('carries a gate caveat and when through to the entry', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        {
          name: 'mypy',
          kind: 'types',
          run: ['mypy'],
          caveat: 'checks only the paths in pyproject; a new dir is silently unchecked',
        },
        { name: 'pw', kind: 'e2e', run: ['pw'], when: 'user-visible' },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.equal(
      report.gates[0].caveat,
      'checks only the paths in pyproject; a new dir is silently unchecked',
    );
    assert.equal(report.gates[0].when, null);
    assert.equal(report.gates[1].when, 'user-visible');
    assert.equal(report.gates[1].caveat, null);
  });
});

describe('gate report — verdict and exit codes', () => {
  it('is pass (exit 0) when every gate that ran passed or was not applicable', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'g', kind: 'lint', run: ['true'] },
        { name: 'pw', kind: 'e2e', run: ['pw'], when: 'user-visible' },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.equal(report.verdict, 'pass');
    assert.equal(exitCode(report.verdict), 0);
  });

  it('is fail (exit 1) when any gate failed', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'ok', kind: 'lint', run: ['true'] },
        { name: 'bad', kind: 'test', run: ['false'] },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: (gate) => (gate.name === 'bad' ? runResult(1) : runResult(0)),
    });
    assert.equal(report.verdict, 'fail');
    assert.equal(exitCode(report.verdict), 1);
  });

  it('is incomplete (exit 3) when a gate was unavailable, even with no failures', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'ok', kind: 'lint', run: ['true'] },
        { name: 'missing', kind: 'build', run: ['nope'] },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: (gate) =>
        gate.name === 'missing' ? runResult(null, { error: new Error('x') }) : runResult(0),
    });
    assert.equal(report.verdict, 'incomplete');
    assert.equal(exitCode(report.verdict), 3);
  });

  it('is incomplete (exit 3) when an app named in the root config had no config of its own', () => {
    const root = repoWith({ '': ROUTER, 'apps/api': API });
    mkdirSync(join(root, 'apps', 'web'), { recursive: true }); // named, but no config
    const report = reportFrom(root, { changed: () => true, runGate: () => runResult(0) });
    assert.deepEqual(report.missingApps, ['apps/web']);
    assert.equal(report.verdict, 'incomplete');
    assert.equal(exitCode(report.verdict), 3);
  });

  it('lets a real fail outrank incomplete, so the exit code names the actionable signal', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'bad', kind: 'test', run: ['false'] },
        { name: 'missing', kind: 'build', run: ['nope'] },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: (gate) =>
        gate.name === 'bad' ? runResult(1) : runResult(null, { error: new Error('x') }),
    });
    assert.equal(report.verdict, 'fail');
    assert.equal(exitCode(report.verdict), 1);
  });

  it('computes the verdict from gate statuses and missing apps directly', () => {
    assert.equal(computeVerdict([{ status: 'pass' }], []), 'pass');
    assert.equal(
      computeVerdict([{ status: 'not_applicable' }, { status: 'skipped_unchanged' }], []),
      'pass',
    );
    assert.equal(computeVerdict([{ status: 'pass' }, { status: 'fail' }], []), 'fail');
    assert.equal(computeVerdict([{ status: 'pass' }, { status: 'unavailable' }], []), 'incomplete');
    assert.equal(computeVerdict([{ status: 'pass' }], ['apps/web']), 'incomplete');
    assert.equal(
      computeVerdict([{ status: 'fail' }, { status: 'unavailable' }], ['apps/web']),
      'fail',
    );
  });

  it('maps the three verdicts to distinct exit codes 0/1/3', () => {
    assert.deepEqual(EXIT, { pass: 0, fail: 1, incomplete: 3 });
    assert.equal(exitCode('pass'), 0);
    assert.equal(exitCode('fail'), 1);
    assert.equal(exitCode('incomplete'), 3);
  });
});

describe('gate report — monorepo dispatch', () => {
  it('runs the touched app gates, skips the untouched app, and names both targets', () => {
    const root = repoWith(MONOREPO);
    const report = reportFrom(root, {
      changed: (target) => target.name === 'api',
      runGate: () => runResult(0),
    });

    // The touched app ran; the untouched app's gates are skipped_unchanged.
    const byName = Object.fromEntries(report.gates.map((g) => [g.name, g.status]));
    assert.equal(byName.pytest, 'pass'); // api's gate, ran
    assert.equal(byName.vitest, 'skipped_unchanged'); // web's gate, the turn touched no web path

    // Both apps appear as targets with their repo-relative dirs; the root is not a target
    // because the router config declares no gates of its own.
    assert.deepEqual(
      report.targets.map((t) => [t.name, t.dir]),
      [
        ['api', 'apps/api'],
        ['web', 'apps/web'],
      ],
    );
    assert.deepEqual(report.missingApps, []);
    assert.equal(report.verdict, 'pass');
  });

  it('runs the root gates first when the root declares gates of its own', () => {
    const root = repoWith({
      ...MONOREPO,
      '': { ...ROUTER, gates: [{ name: 'prettier', kind: 'format', run: ['pnpm', 'format'] }] },
    });
    const report = reportFrom(root, { changed: () => true, runGate: () => runResult(0) });
    assert.deepEqual(
      report.targets.map((t) => t.name),
      ['acme-portal', 'api', 'web'],
    );
  });

  it('routes each gate through its own app, so a Python suite never runs on a CSS change', () => {
    const root = repoWith(MONOREPO);
    const ranIn = [];
    const report = reportFrom(root, {
      changed: () => true, // both apps touched
      runGate: (gate, target) => {
        ranIn.push([gate.name, target.name]);
        return runResult(0);
      },
    });
    // Each gate ran in its own app's target, not all from the root.
    assert.ok(ranIn.some(([g, t]) => g === 'pytest' && t === 'api'));
    assert.ok(ranIn.some(([g, t]) => g === 'vitest' && t === 'web'));
    assert.equal(report.verdict, 'pass');
  });
});

describe('gate report — document shape', () => {
  it('emits the schema version, root, targets, missingApps, gates and verdict', () => {
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [{ name: 'g', kind: 'lint', run: ['true'] }],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    assert.equal(report.schemaVersion, REPORT_SCHEMA_VERSION);
    assert.equal(report.root, root.root);
    assert.deepEqual(report.targets, [{ name: 'solo', dir: '.' }]);
    assert.deepEqual(report.missingApps, []);
    assert.deepEqual(report.requestedKinds, []);
    assert.deepEqual(
      Object.keys(report.gates[0]).sort(),
      [
        'app',
        'caveat',
        'durationMs',
        'exit',
        'kind',
        'name',
        'outputTail',
        'status',
        'when',
      ].sort(),
    );
  });

  it("names the declaring app on every row, whatever the row's status", () => {
    // The human summary prints this beside a gate name in a monorepo. It used to print the
    // gate name twice, because the entry did not carry the app at all.
    const { root, targets, missing } = dispatched({
      name: 'solo',
      gates: [
        { name: 'ran', kind: 'lint', run: ['true'] },
        { name: 'off', kind: 'lint', run: ['true'], enabled: false },
        { name: 'optin', kind: 'e2e', run: ['true'] },
      ],
      hooks: { gatedPaths: ['src'], gatedExtensions: ['.py'] },
    });
    const report = buildReport({
      root,
      targets,
      missing,
      isChanged: () => true,
      runGate: () => runResult(0),
    });
    for (const gate of report.gates) assert.equal(gate.app, 'solo', `${gate.name} lost its app`);
  });
});

describe('gate report — --kinds narrows the run', () => {
  const declared = {
    name: 'solo',
    gates: [
      { name: 'eslint', kind: 'lint', run: ['true'] },
      { name: 'tsc', kind: 'types', run: ['true'] },
      { name: 'vitest', kind: 'test', run: ['true'] },
    ],
    hooks: { gatedPaths: ['src'], gatedExtensions: ['.ts'] },
  };

  /** The report for one `--kinds` request, recording which gates actually ran. */
  function reportFor(kinds) {
    const { root, targets, missing } = dispatched(declared);
    const ran = [];
    const report = buildReport({
      root,
      targets,
      missing,
      kinds,
      isChanged: () => true,
      runGate: (gate) => {
        ran.push(gate.name);
        return runResult(0);
      },
    });
    return { report, ran };
  }

  it('runs only the requested kinds', () => {
    const { report, ran } = reportFor(['lint', 'types']);
    assert.deepEqual(ran, ['eslint', 'tsc']);
    assert.equal(report.gates.find((g) => g.name === 'vitest').status, 'not_applicable');
  });

  it('reports the narrowed-out gates rather than omitting them', () => {
    // Same doctrine as a `disabled` row: a suite that does not check something and a suite
    // that never claimed to are different, and only a row can say which this was.
    const { report } = reportFor(['lint']);
    assert.equal(report.gates.length, 3);
  });

  it('echoes the request back so not_applicable is not ambiguous', () => {
    const { report } = reportFor(['lint']);
    assert.deepEqual(report.requestedKinds, ['lint']);
  });

  it('still passes: a kind the caller did not ask for is not a gate that failed to run', () => {
    const { report } = reportFor(['lint']);
    assert.equal(report.verdict, 'pass');
  });

  it('treats no kinds as every kind, which is what the Stop path and the factory want', () => {
    const { ran } = reportFor([]);
    assert.deepEqual(ran, ['eslint', 'tsc', 'vitest']);
  });

  it('is inert when asked for a kind this repo does not declare', () => {
    const { report, ran } = reportFor(['build']);
    assert.deepEqual(ran, []);
    assert.equal(report.verdict, 'pass');
  });

  it('runs a gate the change filter would have skipped, when the caller forced it', () => {
    // The `/lint` path: a human asked, so "git says nothing moved" is not an answer. Without
    // this the command reports a screen of skipped_unchanged and a green verdict, which is
    // the vacuous green the report exists to refuse.
    const { root, targets, missing } = dispatched(declared);
    const ran = [];
    const report = buildReport({
      root,
      targets,
      missing,
      kinds: ['lint'],
      force: true,
      isChanged: () => assert.fail('isChanged must not be consulted under --force'),
      runGate: (gate) => {
        ran.push(gate.name);
        return runResult(0);
      },
    });
    assert.deepEqual(ran, ['eslint']);
    assert.equal(report.verdict, 'pass');
  });

  it('parses --force, and leaves it off by default', () => {
    assert.equal(parseArgs(['--force']).force, true);
    assert.equal(parseArgs([]).force, false);
  });

  it('parses --kinds a,b, repeated flags, and --kinds=', () => {
    assert.deepEqual(parseArgs(['--kinds', 'lint,types']).kinds, ['lint', 'types']);
    assert.deepEqual(parseArgs(['--kinds=lint']).kinds, ['lint']);
    assert.deepEqual(parseArgs(['--kinds', 'lint', '--kinds', 'test']).kinds, ['lint', 'test']);
    assert.deepEqual(parseArgs(['--kinds', 'lint, ,types']).kinds, ['lint', 'types']);
    assert.deepEqual(parseArgs([]).kinds, []);
  });
});
