# Project status

Single place to resume from after any interruption. Updated at every wave/merge.
Plan: [docs/superpowers/plans/2026-11-02-baby-burn-name-generator.md](superpowers/plans/2026-11-02-baby-burn-name-generator.md) · Spec: [docs/superpowers/specs/2026-11-02-baby-burn-name-generator-design.md](superpowers/specs/2026-11-02-baby-burn-name-generator-design.md)

Legend: T = build task, O = owner task, #N = pull request.

**Event:** Sat 2026-11-14 · **Last updated:** 2026-11-03

## Merge gate (owner rule)

A PR merges after: per-task review, simplify, chisel (Chisle), anti-slop, paperthin, code review.

## Owner tasks

| # | Task | Due | Needs | Status |
|---|---|---|---|---|
| O0 | Chisle + paperthin installed; anti-slop = miqdadbadjuber/anti-slop (owner installs locally); allow-list for banned words still open | Tue 11-03 | - | partly done; allow-list open |
| O1 | Put OpenAI + Gemini API keys in `.env` | Mon 11-02 eve | - | overdue |
| O2 | Pi + printer: flash 64-bit Pi OS Lite, SSH on, QL-800 Editor Lite off, `deploy/setup-pi.sh`, `tools/test_print.py` | Tue 11-03 | T4, T7 | overdue |
| O3 | Cloudflare: account, add card (R2 requires one), `deploy/setup-r2.sh` on the Mac, scoped R2 token | Tue 11-03 | T7 | overdue |
| O4 | Bake-off on 5 to 10 consenting friends' photos; pick OpenAI vs Gemini + prompt | Wed 11-04 eve | T5, O1 | todo |
| O5 | Content review pass 1 in `/admin/review`; add own inside jokes | Thu 11-05 to Fri 11-06 | T6, T10 | todo |
| O6 | First full guest run on Pi + iPad; scan QR on Wi-Fi and cellular | Fri 11-06 | T9, T10, O2, O3 | todo |
| O7 | Failure drills + 30-run soak with friends; content pass 2 | Sat to Sun 11-07/08 | T11 | todo |
| O8 | Dress rehearsal at the house (Ethernet spot, iPad stand, lighting) | Thu 11-12 eve | - | todo |
| O9 | Freeze: confirm name-DB reset + album cleanup; pack a spare label roll (Brother DK-1234, sold as DK-11234 in the US: 60 × 86 mm die-cut name badges), Ethernet cable, power strip, iPad charger | Fri 11-13 eve | - | todo |

## Claude tasks

| Wave | Tasks | Due | Status |
|---|---|---|---|
| 0 | T0 skeleton, config, schema, sample content | Mon 11-02 | done; merged (PR #1) |
| 1 | T1 content · T2 names · T3 badge · T4 printer · T5 transform + bake-off · T6 content drafts (private repo) · T7 Pi + R2 ops · T8 kiosk UI | Tue 11-03 midday | done; merged (PR #2). Content: private repo branch drafts-v1 (399 names / 64 questions / 35 taglines) |
| 2 | T9 kiosk API, sessions, cloud photos + album, photo page | Wed 11-04 | done; merged (PR #3) |
| 3 | T10 admin · T8 live-API wiring check | Thu 11-05 morning | done; merged (PR #3) |
| 4 | T11 hardware bring-up, drills, soak (with owner) | Sat to Sun 11-07/08 | todo (needs Pi SSH) |
| - | Final whole-branch review + rulings list to owner | Sun 11-08 | done 11-03: Ready to merge; 6 Important + 8 minor fixed (5709144, 203 tests); residuals parked for T11 |
