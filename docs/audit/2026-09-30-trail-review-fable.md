# Trail review, 2026-09-30 pstack integration

## 1. Rows whose evidence does not show the claim

- 19:03:28 (pick) cites "pstack-claude declared at project scope shows enabled 0.9.45". The captured output says `Scope: user`. Every `claude plugin list` in the run (19:03:17, 19:07:30) ran on a machine where James already has pstack installed at user scope, so it shows the user install, not that the project block does anything.
- 19:03:28 (hazard) says "pstack@pstack-claude disabled" for the `pstack@harness` configuration, but the command's `head -14` cut the output before that Status line. The claim is carried over from the 19:03:10 experiment.
- 19:18:36 "green on every check" was true for f88251e. harness#48 head is now 8259700 (CLAUDE.md commit, 19:21:08) with Meta still in progress; the 19:21:20 row marks it `done` on local checks only.
- 19:20:22 "pushed, rerunning": the rerun (078072d) fixed the vendor_sync assertion but `gates` still fails on the 3 routing tests, so `gates` is red too, not only `integration`.

## 2. Unlogged pivots

- Adding a CI workflow to factory (19:07:44) has no decision row; it is the biggest scope addition in the run.
- Changing factory `CLAUDE.md` from a link to `@AGENTS.md` (19:07:30) appears only inside the graft row.
- The report's Sonnet wording changed from "Dropout" to "Late entry" (19:08:37); the judge verdict still says dropout, not scored.

## 3. Unproven claims

- Doctrine and report: "the plugin loads with no install step" after the trust prompt on a fresh clone. Never measured; no session or machine without the user-level install was tried. The judge's grounding says a project-only external source is not fetched, and nothing shows `extraKnownMarketplaces` behaves differently.
- Issue 104 says the empty-HOME fallback list reproduces CI. CI's measured list is `gpt-5.4-mini, gpt-5.5, gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra`, the local empty-HOME list had eight models, the host has six. Three catalogues, and the issue names only two.

## 4. Risky choices

- The pin veto rests on one `claude plugin list` in a scratch dir with no `claude plugin install`. The judge had already flagged this and prescribed a loud hook plus an install line, not abandonment. The shipped block floats at upstream HEAD (0.9.45 to 0.9.52 in two days, report item 9) with no staleness signal, and its own fresh-clone claim is the same class of unmeasured assumption.
- Factory CI is red on both jobs until #104 is decided, so red carries no signal on any later PR. It also clones `jchen1707/harness@v2` unpinned into `../harness`, coupling factory CI to another repo's moving branch.
- harness#48 bumps layer A to 0.14.3 for a doc plus settings; every stack will re-vendor. Five repos and six GitHub artifacts in 30 minutes left one PR's head moving after its "verified" row.

## 5. Look first

1. Decide the `gpt-6-astra` replacement (#104); every factory red hangs on it.
2. Before merging any of the four harness-side PRs, open a fresh clone on a profile without user-level pstack and run `claude plugin list`. That is the design's load-bearing claim and it is untested.
3. harness#48 CI on 8259700, and the catalogue mismatch in CI.
