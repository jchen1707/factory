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

test('automatic body fallback retrieves a bounded matching excerpt without unrelated context', (t) => {
  const vault = mkdtempSync(join(tmpdir(), 'recall-body-'));
  t.after(() => rmSync(vault, { recursive: true, force: true }));
  mkdirSync(join(vault, 'Project Learnings'));
  writeFileSync(join(vault, 'Project Learnings/_INDEX.md'), '');
  writeFileSync(
    join(vault, '_VAULT_INDEX.md'),
    '| `lesson.md` | queue | delivery recovery |\n| `other.md` | style | typography |',
  );
  writeFileSync(
    join(vault, 'lesson.md'),
    'only intro '.repeat(800) +
      'zebra-reconcile-83 rollback requires durable intent before the external call.',
  );
  writeFileSync(join(vault, 'other.md'), 'UNRELATED_BODY');
  const options = { cwd: vault, environment: { OBSIDIAN_VAULT_DIRECTORY: vault } };
  assert.deepEqual(recall(options).notes, []);
  const result = recall({
    ...options,
    query: 'Using only provided learning context, describe zebra-reconcile-83 rollback',
  });
  assert.equal(result.status, 'recalled');
  assert.equal(result.search, 'body_fallback');
  assert.equal(result.notes.length, 1);
  assert.match(result.notes[0].text, /durable intent/);
  assert.ok(result.notes[0].text.length <= 3000);
  assert.equal(JSON.stringify(result).includes('UNRELATED_BODY'), false);
});

test('body fallback reports incomplete search budgets and refuses escaped notes', (t) => {
  const vault = mkdtempSync(join(tmpdir(), 'recall-body-limit-'));
  t.after(() => rmSync(vault, { recursive: true, force: true }));
  mkdirSync(join(vault, 'Project Learnings'));
  writeFileSync(join(vault, 'Project Learnings/_INDEX.md'), '');
  const rows = [];
  for (let i = 0; i < 34; i++) {
    const path = `note-${String(i).padStart(2, '0')}.md`;
    rows.push(`| \`${path}\` | neutral | unrelated |`);
    writeFileSync(join(vault, path), i === 0 ? 'a'.repeat(65536) + ' hiddenword' : 'ordinary body');
  }
  writeFileSync(join(vault, '_VAULT_INDEX.md'), rows.join('\n'));
  const result = recall({
    cwd: vault,
    query: 'hiddenword',
    environment: { OBSIDIAN_VAULT_DIRECTORY: vault },
  });
  assert.equal(result.status, 'partial');
  assert.equal(result.notes.length, 0);
  assert.ok(result.warnings.some((w) => w.includes('32 indexed')));
  assert.ok(result.warnings.some((w) => w.includes('truncated')));
  symlinkSync('/etc/hosts', join(vault, 'escape.md'));
  writeFileSync(join(vault, '_VAULT_INDEX.md'), '| `escape.md` | neutral | unrelated |');
  const escaped = recall({
    cwd: vault,
    query: 'localhost',
    environment: { OBSIDIAN_VAULT_DIRECTORY: vault },
  });
  assert.equal(escaped.status, 'partial');
  assert.deepEqual(escaped.notes, []);
});

test('the shared Project Learnings directory does not make unrelated notes relevant', (t) => {
  const vault = mkdtempSync(join(tmpdir(), 'recall-directory-'));
  t.after(() => rmSync(vault, { recursive: true, force: true }));
  mkdirSync(join(vault, 'Project Learnings'));
  writeFileSync(
    join(vault, 'Project Learnings/_INDEX.md'),
    [
      '| 2026-09-09 | factory | Sandbox teardown must preserve native conversation messages because retained event streams provide incomplete history. | [[native]] |',
      '| 2026-09-09 | kitchen-fixture | Sourdough hydration measurement | [[unrelated]] |',
    ].join('\n'),
  );
  writeFileSync(join(vault, '_VAULT_INDEX.md'), '');
  writeFileSync(
    join(vault, 'Project Learnings/native.md'),
    'Preserve native messages before sandbox removal.',
  );
  writeFileSync(
    join(vault, 'Project Learnings/unrelated.md'),
    'UNRELATED_SOURDOUGH_92: weigh flour before water.',
  );
  const result = recall({
    cwd: vault,
    environment: { OBSIDIAN_VAULT_DIRECTORY: vault },
    query:
      'What prior project lesson applies to native transcript preservation before sandbox removal? State the unique lesson identifier and cite the note path. Use only automatically supplied prior context; do not use tools.',
  });
  assert.deepEqual(
    result.notes.map((note) => note.path),
    ['Project Learnings/native.md'],
  );
});
