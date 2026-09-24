# Drama Identity Contract v1

Status: **LOCKED for Integration testing**

This file mirrors the JSM Core Contract v1 rule for drama title identity.

## Rule

One drama identity must use one canonical title-normalization implementation.

Authoritative Python implementation:

`drama_identity.normalize_title()`

Current callers must delegate to it instead of keeping local regex copies.

## Supported release markers

Anchored distribution labels may be removed when they clearly describe release/language packaging:

- `(DUBBED) Title`
- `[ENG DUB] Title`
- `[English Dubbed] Title`
- `English Dubbed: Title`
- `Title - Dubbed`
- `Title English Dubbed`

Words inside the title are not release labels:

- `The Dubbed Wife`
- `Dubbed in Blood`

## Current character scope

Contract v1 preserves the existing JSM ASCII identity key: `a-z0-9`.

A non-Latin-only title therefore produces no identity key and must fail closed rather than being silently matched. Unicode identity support requires a separate audited contract revision.

## Platform safety

Title identity does **not** authorize cross-platform writeback.

A normalized-title match answers only the title-key question. Platform/scope/writeback eligibility is handled by separate Core Contract gates.

## Database contract

The current production Supabase trigger still contains an independent SQL normalization expression. It is **not yet compliant** with this single-source contract.

Stage I will test and replace that SQL behavior only in the isolated Integration path before any production schema change is proposed.
