import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Fan-in at a barrier: every axis reviews the SAME diff in its own context, then a
// single synthesiser reconciles them. The synthesiser never reviews the code itself —
// it only judges and merges what came back. Keeping those jobs apart is the point;
// a reviewer that also ranks tends to rank its own findings first.
//
// This file is layer A: one copy, shared by every stack. Everything stack-specific it
// once hard-coded — the ninth axis, the checklists, the tools that already own style —
// now comes from the repository's own `harness.config.json`. See
// `docs/agents/config.md` for why that file has the shape it does.

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.REVIEW_BASE || 'main';

/**
 * The consuming repository's root, found by walking up from the working directory.
 *
 * Not `process.cwd()` directly: a review is often started from a subdirectory, and
 * `${CLAUDE_PLUGIN_ROOT}` — where this file lives — is outside the repository entirely,
 * so neither end of the path can be assumed.
 */
function repoRoot() {
  let dir = resolve(process.cwd());
  for (;;) {
    if (existsSync(join(dir, 'harness.config.json'))) return dir;
    const parent = dirname(dir);
    if (parent === dir) {
      throw new Error(
        'no harness.config.json found from ' +
          process.cwd() +
          ' upwards. Layer A reads every stack fact from it, so the review cannot be ' +
          'assembled without it. See docs/agents/config.md.',
      );
    }
    dir = parent;
  }
}

const ROOT = repoRoot();
const CONFIG = JSON.parse(readFileSync(join(ROOT, 'harness.config.json'), 'utf8'));
const REVIEW = CONFIG.review ?? {};
const AGENT_DIR = join(ROOT, REVIEW.agentDir ?? '.agents/agents');
const CHECKLIST_DIR = join(ROOT, REVIEW.checklistDir ?? 'docs/agents/subagents');

// The eight axes every stack runs. The ninth is the one this stack has and the other
// does not — an async boundary, an accessibility surface — and it is a whole definition
// in the repo rather than a shared frame plus a checklist. It sits after `tests`
// because that is where both stacks had it, and axis order is the order findings are
// read in.
const SHARED_AXES = [
  { label: 'standards', agent: 'standards-reviewer' },
  { label: 'spec', agent: 'spec-checker' },
  { label: 'security', agent: 'security-reviewer' },
  { label: 'tests', agent: 'test-reviewer' },
  { label: 'simplicity', agent: 'simplicity-reviewer' },
  { label: 'design', agent: 'design-reviewer' },
  { label: 'speed', agent: 'perf-reviewer' },
  { label: 'cost', agent: 'cost-reviewer' },
];

const AXES = REVIEW.ninthAxis
  ? [...SHARED_AXES.slice(0, 4), REVIEW.ninthAxis, ...SHARED_AXES.slice(4)]
  : SHARED_AXES;

export const meta = {
  name: 'full-review',
  description:
    `Fan out a diff to ${AXES.length} independent reviewers (` +
    AXES.map((a) => a.label).join(', ') +
    '), then fan in to one deduplicated ranked report.',
};

/** Body of a Markdown definition, with its YAML frontmatter stripped. Empty when absent. */
function body(path) {
  if (!existsSync(path)) return '';
  return readFileSync(path, 'utf8')
    .replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '')
    .trim();
}

/**
 * One axis's prompt, assembled from the two halves that own it.
 *
 * The **frame** is layer A: the role, the method and the reporting rules, identical in
 * every stack. The **checklist** is layer B: what "in this repo's terms" actually means
 * here. Both are the same files the standalone subagent reads, so `/security-reviewer`
 * and the `security` axis cannot drift apart — which was already true of the frame and
 * is now true of the stack half too.
 *
 * A stack definition at the same name wins over the shared frame, which is how the ninth
 * axis resolves and how a stack can deliberately override one.
 *
 * There is no one-line fallback. An axis that resolves to nothing is a broken
 * installation — the plugin is not enabled, or the vendored tree is missing — and a
 * review that silently continues on a one-sentence brief reports "no findings" from an
 * axis that never ran. That is the failure this whole repository exists to prevent.
 */
function axisPrompt(agent) {
  const own = body(join(AGENT_DIR, `${agent}.md`));
  const frame = own || body(join(HERE, '..', 'agents', `${agent}.md`));
  const checklist = body(join(CHECKLIST_DIR, `${agent}.md`));

  if (!frame && !checklist) {
    throw new Error(
      `the ${agent} axis resolved to nothing. Looked in ${AGENT_DIR}, ` +
        `${join(HERE, '..', 'agents')} and ${CHECKLIST_DIR}. Layer A is delivered as a ` +
        'plugin or as a vendored tree; one of them is missing.',
    );
  }
  if (!checklist) return frame;
  return (
    frame +
    `\n\n---\n\n## ${CONFIG.name}: what to look for, in this repo's terms\n\n` +
    'This is the checklist the frame above told you to read. It is reproduced here so ' +
    'you have it without a file read; it is authoritative for this repository, and where ' +
    'it names a source file, that source outranks it.\n\n' +
    checklist
  );
}

const FINDING_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          severity: { enum: ['critical', 'high', 'medium', 'low'] },
          summary: { type: 'string' },
          why_it_matters: { type: 'string' },
        },
        required: ['file', 'severity', 'summary'],
      },
    },
  },
};

const reviews = await pipeline(AXES, (axis) =>
  agent(
    axisPrompt(axis.agent) +
      `\n\nReview the diff: git diff ${BASE}...HEAD\n` +
      `Report ONLY real defects in this diff. An empty findings list is a valid and ` +
      `common result — do not manufacture findings to look thorough.`,
    { label: axis.label, schema: FINDING_SCHEMA },
  ),
);

const all = AXES.flatMap((axis, i) =>
  (reviews[i]?.findings ?? []).map((f) => ({ ...f, axis: axis.label })),
);

if (all.length === 0) {
  return `No findings across ${AXES.length} axes for ${BASE}...HEAD.`;
}

// The one thing the synthesiser needs from the stack: which tools already own style, so
// it can drop what they enforce. Naming them is cheaper and more accurate than asking it
// to infer them from the findings.
const styleGates = REVIEW.styleEnforcedBy ?? "this repository's linter and formatter";

return await agent(
  `Below are findings from ${AXES.length} independent reviewers of the same diff. ` +
    `Produce ONE report:\n` +
    `1. Merge duplicates — the same defect found by several axes is one finding; keep the clearest wording and note which axes agreed (agreement raises confidence).\n` +
    `2. Drop anything that is a style preference already enforced by ${styleGates}.\n` +
    `3. Rank by severity, then by how many axes independently found it.\n` +
    `4. For each: file:line, one-sentence defect, why it matters, and the smallest fix.\n\n` +
    `Do not add findings of your own — you have not read the diff.\n\n` +
    JSON.stringify(all, null, 2),
  { label: 'synthesise' },
);
