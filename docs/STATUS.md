# Project status

Single place to resume from after any interruption. Updated at every wave/merge.
Plan: [docs/superpowers/plans/2026-11-02-baby-burn-name-generator.md](superpowers/plans/2026-11-02-baby-burn-name-generator.md) · Spec: [docs/superpowers/specs/2026-11-02-baby-burn-name-generator-design.md](superpowers/specs/2026-11-02-baby-burn-name-generator-design.md)

**Event:** Sat 2026-11-14 · **Last updated:** 2026-11-03

## Merge gate (owner rule)

A PR merges only after it passes: per-task review, simplify, chisel, anti-slop, code review, or after owner input when a review needs a decision.
"chisel" and "anti-slop" are not installed as skills; until the owner names them, they map to `ponytail:ponytail-review` (cut over-building) and `humanizer` + `pr-review-toolkit:comment-analyzer` (AI-slop prose and comments).

## Owner tasks

| # | Task | Due | Needs | Status |
|---|---|---|---|---|
| O0 | Install Chisle (chisel = `chisle-review`) and paperthin; confirm what "anti-slop" is; say if any banned words should be allowed | Tue 11-03 | - | todo (blocks PR #1 and #2 merge) |
| O1 | Put OpenAI + Gemini API keys in `.env` | Mon 11-02 eve | - | todo |
| O2 | Pi + printer: flash 64-bit Pi OS Lite, SSH on, QL-800 Editor Lite off, `deploy/setup-pi.sh`, `tools/test_print.py` | Tue 11-03 | T4, T7 | todo |
| O3 | Cloudflare: account, add card (R2 requires one), `deploy/setup-r2.sh` on the Mac, scoped R2 token | Tue 11-03 | T7 | todo |
| O4 | Bake-off on 5 to 10 consenting friends' photos; pick OpenAI vs Gemini + prompt | Wed 11-04 eve | T5, O1 | todo |
| O5 | Content review pass 1 in `/admin/review`; add own inside jokes | Thu 11-05 to Fri 11-06 | T6, T10 | todo |
| O6 | First full guest run on Pi + iPad; scan QR on Wi-Fi and cellular | Fri 11-06 | T9, T10, O2, O3 | todo |
| O7 | Failure drills + 30-run soak with friends; content pass 2 | Sat to Sun 11-07/08 | T11 | todo |
| O8 | Dress rehearsal at the house (Ethernet spot, iPad stand, lighting) | Thu 11-12 eve | - | todo |
| O9 | Freeze: confirm name-DB reset + album cleanup; pack spare DK-1234 roll, Ethernet cable, power strip, iPad charger | Fri 11-13 eve | - | todo |

## Claude tasks

| Wave | Tasks | Due | Status |
|---|---|---|---|
| 0 | T0 skeleton, config, schema, sample content | Mon 11-02 | gate passed (simplify, chisel stand-in, anti-slop, code review); PR #1 waits on real chisle-review |
| 1 | T1 content · T2 names · T3 badge · T4 printer · T5 transform + bake-off · T6 content drafts (private repo) · T7 Pi + R2 ops · T8 kiosk UI | Tue 11-03 midday | all 8 tasks done and reviewed; gate passed; PR #2 waits on real chisle-review. Content: private repo branch drafts-v1 (399 names / 64 questions / 35 taglines) |
| 2 | T9 kiosk API, sessions, cloud photos + album, photo page | Wed 11-04 | done and reviewed; in PR #3 |
| 3 | T10 admin · T8 live-API wiring check | Thu 11-05 morning | done and reviewed; gate passed (PR #3, 184 tests); waits on real chisle-review |
| 4 | T11 hardware bring-up, drills, soak (with owner) | Sat to Sun 11-07/08 | todo |
| - | Final whole-branch review + rulings list to owner | Sun 11-08 | todo |

## Done

- 2026-11-03: waves 2-3 built, reviewed and gated (PR #3: kiosk server, cloud photos + album, admin review page; 184 tests).
- 2026-11-03: wave 1 built, task-reviewed and gated (PR #2, 112 tests); content drafts reviewed twice and pushed (private drafts-v1).
- 2026-11-02: design approved; plan approved; lore research; public + private repos created; T0 built and task-reviewed (PR #1).
