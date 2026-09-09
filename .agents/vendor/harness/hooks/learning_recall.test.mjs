import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { recall } from './learning_recall.mjs';

test('same project in another worktree recalls relevant lesson, excludes unrelated context', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'recall-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const repo = join(root, 'product');
  mkdirSync(repo);
  const git = (...args) => {
    const r = spawnSync('git', args, { cwd: repo, encoding: 'utf8' });
    assert.equal(r.status, 0, r.stderr);
  };
  git('init');
  git(
    '-c',
    'user.name=Test',
    '-c',
    'user.email=test@example.com',
    'commit',
    '--allow-empty',
    '-m',
    'init',
  );
  git('remote', 'add', 'origin', 'https://example.com/team/product.git');
  const other = join(root, 'ticket-123');
  git('worktree', 'add', '-b', 'ticket', other);
  const vault = join(root, 'vault');
  mkdirSync(join(vault, 'Project Learnings'), { recursive: true });
  writeFileSync(
    join(vault, 'Project Learnings', '_INDEX.md'),
    '| Date | Project | Summary | Note |\n| 2026-09-01 | product | sqlite lock recovery | [[locking]] |\n| 2026-09-01 | unrelated | CSS grid layouts | [[styles]] |\n',
  );
  writeFileSync(join(vault, '_VAULT_INDEX.md'), '| Note | Tags | What it covers |\n');
  writeFileSync(
    join(vault, 'Project Learnings', 'locking.md'),
    'A SQLite lock needs a bounded retry.',
  );
  writeFileSync(join(vault, 'Project Learnings', 'styles.md'), 'UNRELATED CONTENT');
  const opts = { cwd: other, environment: { OBSIDIAN_VAULT_DIRECTORY: vault } };
  assert.equal(recall(opts).project, 'product');
  assert.deepEqual(
    recall(opts).index.map((n) => n.path),
    ['Project Learnings/locking.md'],
  );
  const result = recall({ ...opts, query: 'debug sqlite lock' });
  assert.equal(result.status, 'recalled');
  assert.equal(result.notes.length, 1);
  assert.match(result.notes[0].text, /bounded retry/);
  assert.equal(JSON.stringify(result).includes('UNRELATED'), false);
  assert.equal(recall({ ...opts, query: 'astronomy' }).status, 'no_relevant_learnings');
  assert.equal(recall({ ...opts, environment: {} }).status, 'configuration_missing');
  assert.equal(
    recall({ ...opts, environment: { OBSIDIAN_VAULT_DIRECTORY: join(root, 'missing') } }).status,
    'unavailable',
  );
});

test('recall excludes symlinks outside vault and reports missing evidence', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'recall-boundary-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const vault = join(root, 'vault');
  mkdirSync(vault);
  writeFileSync(join(root, 'secret.md'), 'PRIVATE OUTSIDE CONTENT');
  symlinkSync(join(root, 'secret.md'), join(vault, 'escape.md'));
  writeFileSync(
    join(vault, '_VAULT_INDEX.md'),
    '| `escape.md` | sqlite | sqlite recovery |\n| `missing.md` | sqlite | sqlite recovery |\n',
  );
  const result = recall({
    cwd: root,
    query: 'sqlite',
    environment: { OBSIDIAN_VAULT_DIRECTORY: vault },
  });
  assert.equal(result.status, 'partial');
  assert.deepEqual(result.notes, []);
  assert.equal(result.warnings.length, 3);
  assert.equal(JSON.stringify(result).includes('PRIVATE'), false);
});

test('cross-project matches are relevant and note context is bounded', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'recall-budget-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, 'Project Learnings'));
  writeFileSync(join(root, 'Project Learnings', '_INDEX.md'), '');
  const rows = [];
  for (let i = 0; i < 10; i++) {
    rows.push(`| \`note-${i}.md\` | database | sqlite lesson ${i} |`);
    writeFileSync(join(root, `note-${i}.md`), 'sqlite evidence '.repeat(1000));
  }
  writeFileSync(join(root, '_VAULT_INDEX.md'), rows.join('\n'));
  const result = recall({
    cwd: root,
    query: 'sqlite',
    environment: { OBSIDIAN_VAULT_DIRECTORY: root },
  });
  assert.equal(result.status, 'recalled');
  assert.equal(result.notes.length, 4);
  assert.ok(result.notes.every((note) => note.text.length <= 3000));
});

test('the established process alias works, but never overrides an explicit canonical setting', () => {
  const fixture = mkdtempSync(join(tmpdir(), 'recall-alias-'));
  try {
    mkdirSync(join(fixture, 'Project Learnings'));
    writeFileSync(
      join(fixture, 'Project Learnings/_INDEX.md'),
      '| Date | Project | Summary | Note |\n',
    );
    writeFileSync(join(fixture, '_VAULT_INDEX.md'), '| Note | Tags | Description |\n');
    assert.equal(
      recall({ environment: { OBSIDIAN_VAULT_DIR: fixture } }).status,
      'no_relevant_learnings',
    );
    assert.equal(
      recall({ environment: { OBSIDIAN_VAULT_DIR: fixture, OBSIDIAN_VAULT_DIRECTORY: '' } }).status,
      'configuration_missing',
    );
    assert.equal(
      recall({
        environment: {
          OBSIDIAN_VAULT_DIR: fixture,
          OBSIDIAN_VAULT_DIRECTORY: '/nonexistent-vault',
        },
      }).status,
      'unavailable',
    );
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});
