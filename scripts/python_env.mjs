#!/usr/bin/env node
// The checkout is shared with macOS. Linux commands need a dependency environment
// outside its bind-mounted .venv. A factory-provided per-run environment takes priority.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [command, ...args] = process.argv.slice(2);
if (!command) throw new Error('Expected a command argv');
const env = { ...process.env };
if (process.platform === 'linux' && !env.UV_PROJECT_ENVIRONMENT) {
  const project = createHash('sha256').update(root).digest('hex').slice(0, 16);
  env.UV_PROJECT_ENVIRONMENT = join(tmpdir(), `factory-python-${process.getuid()}`, project);
}
const result = spawnSync(command, args, { env, stdio: 'inherit' });
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
