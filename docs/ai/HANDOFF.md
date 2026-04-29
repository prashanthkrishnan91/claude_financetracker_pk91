# AI Handoff — Investing App

## Last change
Cleaned Deploy tab by removing redundant preview and tightening WHY copy (PR #126).

## Files touched
- v2/frontend/src/app/dashboard/deposits/page.tsx
- allocation table components
- deriveAllocationWhy logic

## Behavior change
- Removed "After this trade" preview block to reduce clutter
- Allocation table is now the single source of truth for before/after percentages
- WHY column now uses short, scannable phrases instead of repetitive text
- Slight UI compression (reduced padding) for better vertical density

## Known issues
- Build/test environment still requires proper Supabase env setup
- Some WHY phrases may still feel slightly repetitive across tickers

## Next likely task
Enhance WHY logic to be more context-aware and personalized without introducing verbosity or LLM dependency.

## Debug notes
- Allocation math unchanged; only presentation layer modified
- Supabase env errors affect local build validation but not logic correctness
