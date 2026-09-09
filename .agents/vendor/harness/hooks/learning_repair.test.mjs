import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { distilTranscript } from './session_learnings.mjs';

const hook = new URL('./session_learnings.mjs', import.meta.url).pathname;
test('missing transcript is a capture failure, never an uneventful short session', () => {
  const result = distilTranscript({
    transcriptPath: '/nonexistent/learning-repair.jsonl',
    sessionId: 'missing-1',
    cwd: tmpdir(),
    directory: tmpdir(),
  });
  assert.match(result.outcome, /failed: transcript unavailable/);
});
test('first capture creates both indexes and retains canonical project across worktrees', () => {
  const root = mkdtempSync(join(tmpdir(), 'learning-repair-'));
  try {
    const repo = join(root, 'product');
    const tree = join(root, 'ticket-42');
    const vault = join(root, 'vault');
    const bin = join(root, 'bin');
    for (const p of [repo, vault, bin]) mkdirSync(p);
    const git = (args) => {
      const r = spawnSync('git', args, { cwd: repo, encoding: 'utf8' });
      assert.equal(r.status, 0, r.stderr);
    };
    git(['init']);
    git([
      '-c',
      'user.name=Fixture',
      '-c',
      'user.email=fixture@example.invalid',
      'commit',
      '--allow-empty',
      '-m',
      'fixture',
    ]);
    git(['worktree', 'add', '-b', 'ticket', tree]);
    writeFileSync(
      join(bin, 'claude'),
      '#!/bin/sh\nprintf "SUMMARY: Recovery preserves source\\n\\n## Implementation learnings\\nRetain the source snapshot before restarting interrupted execution.\\n"\n',
      { mode: 0o755 },
    );
    const transcript = join(root, 'session.jsonl');
    writeFileSync(
      transcript,
      JSON.stringify({
        message: { role: 'user', content: 'Recovery must preserve source snapshots. '.repeat(25) },
      }) + '\n',
    );
    const env = {
      ...process.env,
      OBSIDIAN_VAULT_DIRECTORY: vault,
      PATH: bin + ':' + process.env.PATH,
      CLAUDE_LEARNINGS_OFF: '0',
      CLAUDE_LEARNINGS_SKIP: '0',
    };
    const run = (environment = env) =>
      spawnSync(process.execPath, [hook], {
        env: environment,
        input: JSON.stringify({
          cwd: tree,
          session_id: 'recovery-fixture-1',
          transcript_path: transcript,
        }),
        encoding: 'utf8',
      });
    const first = run();
    assert.equal(first.status, 0, first.stderr);
    const directory = join(vault, 'Project Learnings');
    const notes = () =>
      readdirSync(directory).filter((n) => n.endsWith('.md') && !n.startsWith('_'));
    assert.equal(notes().length, 1, 'known lesson must create one note in an empty vault');
    const note = readFileSync(join(directory, notes()[0]), 'utf8');
    assert.match(note, /project: product\n/);
    assert.match(readFileSync(join(directory, '_INDEX.md'), 'utf8'), /Recovery preserves source/);
    assert.match(readFileSync(join(vault, '_VAULT_INDEX.md'), 'utf8'), /Recovery preserves source/);
    run();
    assert.equal(notes().length, 1, 'replay must not duplicate notes');
    const alias = { ...env, OBSIDIAN_VAULT_DIR: vault };
    delete alias.OBSIDIAN_VAULT_DIRECTORY;
    assert.match(run(alias).stderr, /skipped: unchanged/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('a partial factory export cannot overwrite a note from the full transcript', () => {
  const directory = mkdtempSync(join(tmpdir(), 'learning-partial-'));
  try {
    const original =
      '---\nsession: full-session-42\nproject: product\n---\n\nFull original learning\n';
    const path = join(directory, 'historical-name.md');
    writeFileSync(path, original);
    const result = distilTranscript({
      directory,
      sessionId: 'full-session-42',
      evidence: 'retained-events-partial',
      transcriptPath: '/missing',
      cwd: directory,
    });
    assert.match(result.outcome, /existing note retained/);
    assert.equal(readFileSync(path, 'utf8'), original);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('full session IDs do not conflate notes with matching eight-character prefixes', async () => {
  const { existingNote, placeNote } = await import('./session_learnings.mjs');
  const notes = [
    {
      session: '12345678-first',
      key: '12345678',
      path: '/vault/date product 12345678.md',
      body: 'first',
      date: 'old',
    },
  ];
  assert.equal(existingNote(notes, '12345678-second'), undefined);
  assert.notEqual(
    placeNote(notes, 'second', '12345678-second', notes[0].path).target,
    notes[0].path,
  );
});

test('a live capture has a retryable failure outcome and a dead local worker can recover', async () => {
  const { createHash } = await import('node:crypto');
  const { hostname } = await import('node:os');
  const directory = mkdtempSync(join(tmpdir(), 'learning-lock-'));
  const sessionId = 'locked-session';
  const lock = join(
    directory,
    `._capture-${createHash('sha256').update(sessionId).digest('hex')}.lock`,
  );
  try {
    mkdirSync(lock);
    writeFileSync(join(lock, 'owner.json'), JSON.stringify({ host: hostname(), pid: process.pid }));
    const options = { directory, sessionId, transcriptPath: '/missing', cwd: directory };
    assert.match(distilTranscript(options).outcome, /failed: session capture already running/);
    writeFileSync(join(lock, 'owner.json'), JSON.stringify({ host: hostname(), pid: 2147483647 }));
    assert.match(distilTranscript(options).outcome, /failed: transcript unavailable/);
    assert.equal(readdirSync(directory).length, 0);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
