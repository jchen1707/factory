// Git hooks export repository selectors. Fixtures must select their own repositories.
const fixtureEnv = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')),
);
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyDelivery, resolveDelivery } from './delivery_policy.mjs';

const config = {
  delivery: {
    default: 'core',
    requirements: {
      acceptance: { kind: 'correctness', description: 'Accepted behavior', gate: 'acceptance' },
      scale: {
        kind: 'engineering',
        description: 'Load targets',
        gate: 'scale',
        deferrableIn: ['prototype'],
      },
    },
    profiles: {
      prototype: {
        required: [],
        deferrals: [
          { requirement: 'scale', rationale: 'No load yet', revisit: 'Before public launch' },
        ],
      },
      core: { required: ['scale'], deferrals: [] },
      hardening: { required: ['scale'], deferrals: [] },
    },
  },
  gates: [{ name: 'acceptance', enabled: false }, { name: 'scale' }],
};

test('Prototype retains correctness and makes engineering deferral visible', () => {
  const policy = resolveDelivery(config, null, 'prototype');
  const effective = applyDelivery(config, policy);
  assert.equal(effective.gates[0].enabled, true);
  assert.equal(effective.gates[1].policyDeferral.revisit, 'Before public launch');
  assert.equal(config.gates[0].enabled, false);
});

test('Children cannot contradict or defer inherited requirements', () => {
  const parent = resolveDelivery(config);
  assert.throws(() => resolveDelivery(config, parent, 'prototype'), /cannot weaken/);
  const child = structuredClone(config);
  child.delivery.requirements.acceptance.description = 'Different contract';
  assert.throws(() => resolveDelivery(child, parent), /Conflicting/);
});

test('Safety and incomplete deferrals are rejected', () => {
  const child = structuredClone(config);
  child.delivery.requirements.scale.kind = 'safety';
  assert.throws(() => resolveDelivery(child, null, 'prototype'), /Invalid deferral/);
  child.delivery.requirements.scale.kind = 'engineering';
  child.delivery.profiles.prototype.deferrals[0].revisit = '';
  assert.throws(() => resolveDelivery(child, null, 'prototype'), /Invalid deferral/);
});

// Exercise the actual Stop process and report process against the same retained contract.
// The candidate deletes its policy/gates; only the host-selected authority remains binding.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deliveryDispatch } from './verify.mjs';

test('Stop and gate report enforce immutable Prototype correctness and retain deferrals', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'delivery-stop-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const candidate = join(dir, 'candidate');
  const authority = join(dir, 'authority');
  mkdirSync(candidate);
  mkdirSync(authority);
  assert.equal(spawnSync('git', ['init', '-q', candidate], { env: fixtureEnv }).status, 0);
  mkdirSync(join(candidate, 'src'));
  writeFileSync(join(candidate, 'src', 'changed.js'), 'changed\n');
  assert.equal(
    spawnSync('git', ['add', 'src/changed.js'], { cwd: candidate, env: fixtureEnv }).status,
    0,
  );
  writeFileSync(join(candidate, 'harness.config.json'), JSON.stringify({ gates: [] }));
  const trusted = structuredClone(config);
  trusted.hooks = { gatedPaths: ['src'], gatedExtensions: ['.js'] };
  trusted.gates = [
    {
      name: 'acceptance',
      kind: 'test',
      enabled: false,
      run: [
        process.execPath,
        '-e',
        "process.exit(require('node:fs').existsSync('broken') ? 1 : 0)",
      ],
    },
    { name: 'scale', kind: 'test', run: [process.execPath, '-e', 'process.exit(1)'] },
  ];
  writeFileSync(join(authority, 'harness.config.json'), JSON.stringify(trusted));
  const env = {
    ...fixtureEnv,
    HARNESS_AUTHORITY_ROOT: authority,
    HARNESS_DELIVERY_PROFILE: 'prototype',
    HARNESS_SKIP_VERIFY: '',
    CLAUDE_SKIP_VERIFY: '',
  };
  const invoke = (script, args = []) =>
    spawnSync(process.execPath, [fileURLToPath(new URL(script, import.meta.url)), ...args], {
      cwd: candidate,
      env,
      encoding: 'utf8',
      input: JSON.stringify({ cwd: candidate, env: fixtureEnv }),
    });
  const stop = invoke('./verify.mjs');
  assert.equal(stop.status, 0, stop.stderr);
  const report = invoke('./gate_report.mjs', ['--force', '--json']);
  assert.equal(report.status, 0, report.stderr);
  const gates = JSON.parse(report.stdout).gates;
  assert.deepEqual(
    gates.map(({ name, status }) => [name, status]),
    [
      ['acceptance', 'pass'],
      ['scale', 'deferred'],
    ],
  );
  assert.match(gates[1].outputTail, /Before public launch/);
  writeFileSync(join(candidate, 'broken'), 'reproduced acceptance failure\n');
  const blocked = invoke('./verify.mjs');
  assert.equal(blocked.status, 2, blocked.stderr);
  assert.match(blocked.stderr, /acceptance/);
  const failed = invoke('./gate_report.mjs', ['--force', '--json']);
  assert.notEqual(failed.status, 0);
  assert.equal(JSON.parse(failed.stdout).verdict, 'fail');
});

test('ordinary Stop dispatch preserves discovered repository root from nested cwd', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'delivery-root-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  mkdirSync(join(dir, 'src'));
  writeFileSync(join(dir, 'harness.config.json'), JSON.stringify(config));
  const effective = deliveryDispatch(join(dir, 'src'));
  assert.equal(effective.root.root, dir);
  assert.equal(effective.targets[0].root, dir);
});

test('nested apps inherit policy through routers and reject authority escape', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'delivery-apps-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  mkdirSync(join(dir, 'apps', 'api'), { recursive: true });
  const write = (path, value) =>
    writeFileSync(join(dir, path, 'harness.config.json'), JSON.stringify(value));
  write('', { delivery: config.delivery, apps: ['apps'] });
  write('apps', { apps: ['api'] });
  write('apps/api', { gates: config.gates });
  const effective = deliveryDispatch(dir, '', 'prototype');
  assert.equal(effective.targets.length, 1);
  assert.equal(effective.targets[0].root, join(dir, 'apps', 'api'));
  assert.equal(effective.targets[0].gates[0].policyRequired, true);
  assert.equal(effective.targets[0].gates[1].policyDeferral.requirement, 'scale');
  const conflict = structuredClone(config);
  conflict.delivery.requirements.acceptance.description = 'Weaker child contract';
  write('apps/api', conflict);
  assert.throws(() => deliveryDispatch(dir), /Conflicting parent\/child/);
  write('apps', { apps: ['../../outside'] });
  assert.throws(() => deliveryDispatch(dir), /escapes authority root/);
});

test('missing retained authority does not fall back to an ancestor config', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'delivery-missing-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  mkdirSync(join(dir, 'missing'));
  writeFileSync(join(dir, 'harness.config.json'), JSON.stringify(config));
  assert.throws(() => deliveryDispatch(dir, join(dir, 'missing')), /authority config is missing/);
});
