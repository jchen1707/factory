/** Original prototype composition, populated exclusively from the recorded fixture manifest.
 * This is a comparison renderer, not the untouched design and not production UI.
 * Its sections follow prototype.js; real evidence replaces the prototype's invented values.
 */
export function renderFixtureReference({name, fixture}) {
  const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
  const human = value => String(value || 'Unknown').replaceAll('_', ' ');
  const panel = (title, body) => `<section class="panel"><h2>${esc(title)}</h2>${body}</section>`;
  const badge = value => `<span class="badge ${['blocked', 'awaiting_human', 'fail', 'failed'].includes(value) ? 'warning' : ''}">${esc(human(value))}</span>`;
  const table = (headings, rows) => `<div class="scroll" role="region" aria-label="${esc(name)} fixture table" tabindex="0"><table><thead><tr>${headings.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  const disclosure = (title, body) => `<details><summary>${esc(title)}</summary>${body}</details>`;
  const detail = fixture.detail, row = detail.row, timeline = fixture.timeline;
  const cost = r => r.known_spend_usd == null ? 'Unknown' : `${r.spend_status === 'partial' ? '≥ ' : ''}$${r.known_spend_usd.toFixed(2)}`;
  const context = r => r.context_pct == null ? 'Unavailable' : `${Math.round(r.context_pct * 100)}% · fresh`;
  const stats = () => {
    const observed = fixture.runs.filter(r => r.usage_status !== 'unknown');
    const costs = fixture.runs.filter(r => r.known_spend_usd != null);
    const complete = fixture.runs.length > 0 && fixture.runs.every(r => r.spend_status === 'complete');
    const values = name === 'runs' ? [
      ['Open runs', fixture.runs.length, 'Displayed runs'],
      ['Context availability', fixture.runs.reduce((sum, r) => sum + r.fresh_context_invocations, 0), 'Live invocations with fresh context observations'],
      ['Cumulative tokens', observed.length ? observed.reduce((sum, r) => sum + r.tokens_in + r.tokens_out, 0).toLocaleString('en-US') : 'Unavailable', `Displayed runs · ${fixture.runs.every(r => r.usage_status === 'complete') ? 'complete' : 'incomplete'}`],
      ['Estimated cost', costs.length ? `${complete ? '' : '≥ '}$${costs.reduce((sum, r) => sum + r.known_spend_usd, 0).toFixed(2)}` : 'Unavailable', `Displayed runs · API-equivalent USD · ${complete ? 'complete' : 'incomplete / lower bound'}`],
    ] : [
      ['State / attempt', human(row.state), `Attempt ${row.attempt}`],
      ['Context occupancy', context(row), 'Current attempt · measured observation'],
      ['Cumulative tokens', (row.tokens_in + row.tokens_out).toLocaleString('en-US'), `${fixture.invocations.length} invocations · incomplete observations`],
      ['Estimated cost', cost(row), 'API-equivalent USD · incomplete usage'],
    ];
    return `<div class="stats">${values.map(([label, value, sub]) => `<div class="stat">${esc(label)}<strong>${esc(value)}</strong><small>${esc(sub)}</small></div>`).join('')}</div>`;
  };
  const evidence = () => disclosure('Source, delivery and retained findings', `<p>Branch ${esc(row.branch)} · attempt ${row.attempt} / ${esc(row.rung)} · elapsed ${row.elapsed_in_state}s / timeout ${row.timeout_seconds ?? 'unavailable'}s.</p>${detail.review_findings.map(f => `<p>${esc(f.severity)} · ${esc(f.summary)} · ${esc(f.file)}:${f.line}</p>`).join('')}${detail.artifacts.map(a => `<p>Artifact ${esc(a.name)} · SHA-256 <code>${esc(a.sha256)}</code></p>`).join('')}<p>${detail.pr_url ? esc(detail.pr_url) : 'No pull request recorded'}</p>`)
    + disclosure('Evidence completeness', `<p>Usage ${esc(row.usage_status)}. Known estimate ${esc(cost(row))}; missing evidence is not zero usage.</p>`)
    + disclosure('Source and policy', '<p>Current run selection is frozen. Policy replacement requires an eligible paused state and invalidates affected evidence.</p>')
    + disclosure('Child agents', `<p>${fixture.invocations.filter(i => i.role === 'child').length} retained child invocations. ${row.active_invocations} active invocations.</p>`);
  const field = (label, value, choices) => `<label>${esc(label)}${choices ? `<select>${choices.map(choice => `<option${choice === value ? ' selected' : ''}>${esc(choice)}</option>`).join('')}</select>` : `<input value="${esc(value)}" placeholder="Inherited">`}</label>`;
  const settings = (values, project) => `<form><div class="fields">${[
    field('Delivery profile', values.delivery_profile || 'Repository default', ['Repository default', 'prototype', 'core', 'hardening']),
    field('Model preset', values.model_preset || 'existing', ['existing', 'volume', 'high-confidence']),
    field('Implementation mode', values.mode || 'automatic', ['automatic', 'approval']),
    field('Workflow', values.workflow || 'existing', ['existing', 'diagnosis']),
    field('Active agent limit', values.max_active_agents ?? ''),
    field('Child delegation', values.delegation_mode || 'inherit', ['inherit', 'disabled', 'read-only', 'isolated-write']),
    field('Children per parent', values.max_children_per_parent ?? ''),
    field('Delegation depth', values.max_delegation_depth ?? ''),
    ...(project ? [field('Concurrency', project.concurrency), field('Certification', values.certification_mode || 'manual', ['manual', 'automatic']), field('Certification configuration', values.certification_config ?? ''), field('Sandbox isolation', values.isolation || 'shared', ['shared', 'per-run']), field('Isolation evidence', values.isolation_measurement ?? '')] : []),
    field('Test design', values.test_design ? 'Enabled' : 'Disabled', ['Enabled', 'Disabled']),
  ].join('')}</div><p>${project ? 'New runs inherit these defaults. Existing runs retain their snapshots.' : 'Project limits bound run overrides. Current work continues when settings change.'}</p><button type="button">Save fixture settings</button> <button type="reset" class="secondary">Reset</button></form>`;
  let content;
  if (name === 'runs') content = stats() + `<div class="grid">${panel('Work in progress', table(['Ticket', 'Project', 'State', 'Context', 'Estimate'], fixture.runs.map(r => [
    `<a href="detail.html">${esc(r.ticket)}</a>${r.title ? `<small>${esc(r.title)}</small>` : ''}` + disclosure('Run signals', `<p>Branch: ${esc(r.branch)}<br>Attempt / rung: ${r.attempt} / ${esc(r.rung)}<br>Elapsed / timeout: ${r.elapsed_in_state}s / ${r.timeout_seconds ?? 'unavailable'}<br>Tokens in / out: ${r.tokens_in} / ${r.tokens_out}<br>Activity: ${esc(r.activity || 'Unavailable')}<br>PR: ${esc(r.pr_url || 'Not delivered')}</p>`),
    esc(r.project), badge(r.state), esc(context(r)), esc(cost(r)),
  ])))}${panel('Needs attention', fixture.runs.filter(r => ['blocked', 'awaiting_human'].includes(r.state)).map(r => `<p>${badge(r.state)} <strong>${esc(r.ticket)}</strong></p><p>${esc(r.blocked_reason || 'No reason recorded')}</p><a href="detail.html">Inspect run →</a><hr>`).join(''))}</div>`;
  if (name === 'projects') content = panel('Registered projects', table(['Project', 'Delivery', 'Run slots', 'Agent slots', 'Waiting'], fixture.projects.map(p => [esc(p.name), esc(p.settings.delivery_profile || 'Repository default'), `${p.occupied_slots} / ${p.concurrency}`, `${p.active_agents} / ${p.effective_agent_limit ?? 'inherited'}`, esc(p.waiting)]))) + panel(`Project defaults · ${fixture.projects[0].name}`, settings(fixture.projects[0].settings, fixture.projects[0]));
  if (name === 'detail') content = stats() + `<div class="grid"><div>${panel(`Attempt ${row.attempt} · ${human(row.state)}`, `<p><span class="live"></span>${esc(row.activity || 'Activity unavailable')}</p><progress value="${row.context_pct == null ? 0 : row.context_pct * 100}" max="100" aria-label="Context occupancy"></progress><p>${esc(context(row))}. Cumulative usage includes previous attempts.</p><div class="actions"><button type="button">Suspend fixture run</button><button type="button" class="secondary">Cancel fixture run</button><a href="timeline.html">Open timeline →</a></div>`)}${panel('Verification and review', table(['Evidence', 'Status', 'Meaning'], [...detail.gates.map(g => [esc(g.name), badge(g.status), esc(g.caveat || 'Recorded result')]), ...detail.review_findings.map(f => [esc(f.file), badge(f.severity), esc(f.summary)]), ['Pull request', 'Not available', 'James merges after delivery']]))}</div>${panel('Retained evidence', evidence() + '<p><a href="settings.html">Run settings →</a></p>' + disclosure('Artifacts and live tail', `<p>${detail.artifacts.map(a => esc(a.name)).join(' · ')}</p><pre>Fixture retained event stream · 250 lines</pre>`))}</div>`;
  if (name === 'timeline') {
    const origin = Math.min(...timeline.blocks.map(b => b.start)), span = Math.max(1, timeline.elapsed_total_s);
    const lanes = [...new Set(timeline.blocks.map(b => b.lane))];
    const waterfall = `<div class="waterfall" role="img" aria-label="Measured fixture transition durations"><p>0 → ${span}s · elapsed wall time</p>${lanes.map(lane => `<div style="display:grid;grid-template-columns:90px minmax(0,1fr)"><strong>${esc(lane)}</strong><div style="position:relative;height:44px">${timeline.blocks.filter(b => b.lane === lane).map(b => `<span title="${esc(b.state)} · ${b.start - origin}s to ${b.end - origin}s · ${b.duration_s}s${b.live ? ' · active' : ''}" style="position:absolute;box-sizing:border-box;min-width:0;padding:9px 2px;white-space:nowrap;overflow:hidden;left:${(b.start - origin) / span * 100}%;width:${b.duration_s / span * 100}%">${esc(b.state)} · ${b.duration_s}s${b.live ? ' · active' : ''}</span>`).join('')}</div></div>`).join('')}</div>`;

    content = panel('Runtime waterfall', waterfall + table(['Tool call', 'Type', 'Duration', 'Outcome'], timeline.tool_calls.map(c => [esc(c.summary), esc(c.kind), c.duration_s == null ? 'Unknown' : `${c.duration_s.toFixed(1)}s`, esc(c.exit_code ?? 'Unknown')]))) + `<div class="grid">${panel('Execution timeline', `<ol class="timeline">${detail.transitions.map(t => `<li><time>${t.at - origin}s · ${esc(t.actor)}</time><strong>${esc(human(t.to_state))}</strong><p>${esc(t.rule)}</p></li>`).join('')}</ol>`)}${panel('Agent and tool activity', evidence() + table(['Role', 'State'], timeline.cards.map(c => [esc(c.role), badge(c.status)])))}</div>`;
  }
  if (name === 'settings') content = panel('Invocation admission', `<p>${fixture.settings.waiting_invocation ? `Waiting invocation: ${esc(fixture.settings.waiting_invocation)}` : 'No invocation awaiting admission'}</p>` + table(['Invocation', 'Runtime', 'Usage', 'Estimate'], fixture.invocations.map(i => [esc(i.role), esc(i.metadata.sandbox || 'Identity unavailable'), i.telemetry?.usage ? `${i.telemetry.usage.input_tokens} input / ${i.telemetry.usage.output_tokens} output` : 'Final report unavailable', i.telemetry?.estimate ? `≥ $${i.telemetry.estimate.known_usd.toFixed(2)}` : 'Unknown']))) + (fixture.scenario === 'stress' ? '<p>Stress reference intentionally expands all retained invocations; production groups active work before collapsed history.</p>' : '') + panel('Effective run settings', settings(fixture.settings)) + panel('Frozen authority', '<p>Current policy selection is retained. Pause in an eligible state before replacing policy; affected evidence will be invalidated.</p>');
  if (name === 'runtimes') content = panel('Sandbox inventory', table(['Sandbox', 'Ownership / state', 'Layout', 'Certification', 'Runs'], fixture.runtimes.map(r => [esc(r.name), `${r.operator_owned ? 'Operator' : 'Factory'} · ${esc(r.state)}`, esc(r.layout), esc(r.compatibility), esc(r.runs_using.join(', ') || 'None')]))) + panel('Compatibility evidence', '<p>Recorded evidence is historical. Current identity must be observed before certification can be asserted.</p>' + fixture.runtimes.map(r => disclosure(r.name, `<p>${esc(r.compatibility)}</p><pre>${esc(JSON.stringify(r.recorded_compatibility, null, 2))}</pre>`)).join(''));
  if (name === 'config') content = panel('Model routing', `<form>${table(['Role', 'Model', 'Effort'], fixture.config.roles.map(r => [esc(r.name), field(`${r.name} model`, r.model), field(`${r.name} effort`, r.effort, [r.effort])]))}<div class="fields">${field('Run ceiling · estimated USD', fixture.config.usd_per_run)}${field('Warning · estimated USD', fixture.config.usd_warn_at)}</div><p>API-equivalent estimates, not account charges. Missing pricing remains unknown.</p><button type="button">Save fixture configuration</button></form>`) + panel('Configuration sources', '<p>Project registry: read-only inventory. Model routing: validated before saving. Existing run selections remain frozen.</p><a href="projects.html">Inspect project defaults →</a>');
  document.querySelector('#content').innerHTML = content;
  document.querySelector('.toolbar').firstElementChild.textContent = 'DETERMINISTIC COMPOSITION REFERENCE · FIXTURES ONLY';
  document.querySelector('.heading p').textContent = ['detail', 'timeline', 'settings'].includes(name) ? `${row.ticket} · ${row.title}` : 'Same recorded fixture values as the implementation capture';
  document.querySelector('.ops-strip').innerHTML = `<strong>FIXTURE OPERATIONS</strong><span>${fixture.runs.length} open</span><span>${fixture.runs.filter(r => r.state === 'blocked').length} blocked</span><span>${fixture.runs.filter(r => r.state === 'awaiting_human').length} await human merge</span>`;
}
