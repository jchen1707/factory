/** Shared delivery policy. Configuration owns requirements; the factory owns selection. */
import { readFileSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const PROFILES = ['prototype', 'core', 'hardening'];

export function resolveDelivery(config, parent = null, selected = '') {
  const delivery = config.delivery;
  if (!delivery) return parent;
  const profile = selected || parent?.profile || delivery.default;
  if (!PROFILES.includes(profile)) throw new Error(`Unknown delivery profile: ${profile}`);
  const requirements = { ...(parent?.requirements || {}), ...delivery.requirements };
  for (const [id, requirement] of Object.entries(parent?.requirements || {})) {
    if (
      delivery.requirements[id] &&
      JSON.stringify(delivery.requirements[id]) !== JSON.stringify(requirement)
    ) {
      throw new Error(`Conflicting parent/child requirement: ${id}`);
    }
  }
  const deferrals = [...(parent?.deferrals || [])];
  const required = new Set(parent?.required || []);
  for (const [id, requirement] of Object.entries(requirements)) {
    if (
      ['product', 'correctness', 'safety'].includes(requirement.kind) ||
      (profile === 'hardening' && requirement.kind === 'production')
    )
      required.add(id);
  }
  const declared = delivery.profiles[profile];
  if (!declared) throw new Error(`Profile is not declared: ${profile}`);
  for (const id of declared.required || []) {
    if (!requirements[id]) throw new Error(`Unknown requirement: ${id}`);
    required.add(id);
  }
  for (const deferral of declared.deferrals || []) {
    const requirement = requirements[deferral.requirement];
    if (
      !requirement ||
      requirement.kind !== 'engineering' ||
      !requirement.deferrableIn?.includes(profile) ||
      !deferral.rationale?.trim() ||
      !deferral.revisit?.trim()
    ) {
      throw new Error(`Invalid deferral: ${deferral.requirement}`);
    }
    if (parent?.required.includes(deferral.requirement))
      throw new Error('Child policy cannot weaken parent policy');
    required.delete(deferral.requirement);
    deferrals.push(deferral);
  }
  const reviewAxes = declared.reviewAxes ?? parent?.reviewAxes;
  if (reviewAxes && JSON.stringify(reviewAxes) !== JSON.stringify(['standards', 'spec']))
    throw new Error('reviewAxes must select standards and spec');
  return {
    profile,
    requirements,
    required: [...required],
    deferrals,
    ...(reviewAxes ? { reviewAxes: [...reviewAxes] } : {}),
  };
}

export function applyDelivery(config, policy) {
  if (!policy) return config;
  const gates = config.gates.map((gate) => ({ ...gate }));
  for (const id of policy.required) {
    const requirement = policy.requirements[id];
    if (!requirement.gate) continue;
    const gate = gates.find((entry) => entry.name === requirement.gate);
    if (!gate) throw new Error(`Required gate is undeclared: ${requirement.gate}`);
    gate.enabled = true;
    gate.policyRequired = true;
  }
  for (const deferral of policy.deferrals) {
    const gate = gates.find(
      (entry) => entry.name === policy.requirements[deferral.requirement].gate,
    );
    if (!gate) continue;
    if (gate.policyRequired) throw new Error(`Deferred gate is also required: ${gate.name}`);
    gate.policyDeferral = deferral;
  }
  return { ...config, gates, policy };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [path, selected = ''] = process.argv.slice(2);
  const config = JSON.parse(readFileSync(path, 'utf8'));
  if (process.argv.includes('--tree')) {
    const root = dirname(resolve(path));
    const policies = {};
    const visit = (file, parent = null) => {
      const config = JSON.parse(readFileSync(file, 'utf8'));
      const key = relative(root, dirname(file)) || '.';
      const policy = resolveDelivery(config, parent, selected);
      if (config.gates) applyDelivery(config, policy);
      policies[key] = policy;
      for (const app of config.apps || []) {
        const target = resolve(dirname(file), app, 'harness.config.json');
        const rel = relative(root, target);
        if (isAbsolute(app) || rel.startsWith('..')) throw new Error('App escapes authority root');
        if (Object.hasOwn(policies, relative(root, dirname(target)) || '.'))
          throw new Error('Cyclic app authority');
        visit(target, policy);
      }
    };
    visit(resolve(path));
    process.stdout.write(`${JSON.stringify(policies)}\n`);
  } else {
    process.stdout.write(`${JSON.stringify(resolveDelivery(config, null, selected))}\n`);
  }
}
