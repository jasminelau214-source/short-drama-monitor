"""Offline Step A policy checks; NOT a runtime implementation or E2E test."""

import copy
import json
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
BASE_SHA = "a206bc9c21138a9559aecf5a7a34069167268cfc"
ALLOWED_PATHS = {
    "core_contract_v2.json", "docs/CORE_CONTRACT_V2.md",
    "promotion_manifest_v2.json", "test_core_contract_v2.py",
}
GATES = {
    "execution", "structure", "truth", "semantic", "fault", "identity",
    "stability", "path_optimization", "e2e", "integration",
}
REPLAY_IDS = {
    "legacy_115_official_web_pending", "dub_anchored_labels",
    "cross_platform_same_title", "incomplete_success_state",
    "duplicate_missing_extra_rank_or_title", "stale_and_superseded_run",
    "partial_5_of_6_mobo_failure", "retry_and_quota",
    "concurrent_first_seen_and_duplicate_write", "false_complete",
    "silent_failure", "official_web_golden_e2e",
}

# Independent approved policy expectations. Missing keys and type coercion fail
# closed (Python otherwise treats True == 1). Mutations below test the guard.
POLICY = {
    "contractVersion": "2.0",
    "status": "LOCKED_NOT_IMPLEMENTED",
    "baseMainSha": BASE_SHA,
    "activation.scope": "INTEGRATION_CONTRACT_ONLY",
    "activation.currentBaselineRemainsProductionAuthority": True,
    "activation.runtimeImplemented": False,
    "activation.productionPromotionRequiresSeparateApproval": True,
    "scheduleContract.timezone": "Asia/Shanghai",
    "scheduleContract.collectAt": "16:00",
    "scheduleContract.checkAt": "16:25",
    "scheduleContract.notifyOnlyOnException": True,
    "scheduleContract.schedulerInstallationAuthorized": False,
    "sourceSemantics.productionPrimary": "OFFICIAL_WEB",
    "sourceSemantics.appRole": "VALIDATION_EVIDENCE",
    "sourceSemantics.verifiedSourceFirstSeenTriggersResearch": True,
    "sourceSemantics.unregisteredOrUnverifiedSourceFailsClosed": True,
    "sourceSemantics.additionalPlatformsRequireContractChange": True,
    "sourceSemantics.activeOfficialWebPlatforms": ["DramaBox", "FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort"],
    "sourceSemantics.collectionProfile": {"language": "English", "locale": "en-US", "region": "US", "regionRule": "US_WHEN_FILTER_AVAILABLE"},
    "identityAndNewness.identityKey": "canonical_drama_identity",
    "identityAndNewness.firstSeenTrigger": "first_seen_any_verified_source",
    "identityAndNewness.researchSubjectScope": "GLOBAL_CANONICAL_DRAMA",
    "identityAndNewness.dedupeAcrossPlatformsAndSources": True,
    "identityAndNewness.preservePerSourceObservations": True,
    "identityAndNewness.publicationIdentitySeparateFromContentIdentity": True,
    "identityAndNewness.titleMatchAloneProvesContentIdentity": False,
    "identityAndNewness.dubLabelsOnlyRemovedWhenAnchoredAndProven": True,
    "identityAndNewness.unresolvedIdentityStatus": "REVIEW_REQUIRED",
    "identityAndNewness.concurrentFirstSeenRequiresAtomicUniqueness": True,
    "identityAndNewness.researchRefreshRequiresExplicitVersionedAuthorization": True,
    "platformFailureIsolation.independentPlatformCommit": True,
    "platformFailureIsolation.successfulPlatformsProceedImmediately": True,
    "platformFailureIsolation.failedPlatformCurrentDateState": "MISSING_OR_FAILED",
    "platformFailureIsolation.copyPriorDayIntoCurrentDate": False,
    "platformFailureIsolation.priorAuthoritativeRunMayRemainDisplayable": True,
    "platformFailureIsolation.staleDisplayRequiresActualSourceDate": True,
    "platformFailureIsolation.crossPlatformCompositeMustExposeCoverage": True,
    "researchEligibility.enqueueIsCandidateOnly": True,
    "researchEligibility.workerMustRevalidateBeforeExternalSpend": True,
    "researchEligibility.authoritativeObservationRequired": True,
    "researchEligibility.globalResearchReuseAllowed": True,
    "researchEligibility.platformWritebackReuseDoesNotPermitCrossPlatformRecordFallback": True,
    "researchEligibility.legacy115PendingMayAutoExecute": False,
    "researchEligibility.deferredResumeMustRevalidateEligibility": True,
    "researchComplete.deterministicSchemaValidationRequired": True,
    "researchComplete.coreFieldsRequired": True,
    "researchComplete.evidenceUrlRequired": True,
    "researchComplete.minimumConfidence": "medium",
    "researchComplete.unresolvedIdentityOrSourceConflictBlocksComplete": True,
    "researchComplete.optionalOpeningPaywallFieldsMayBeMissingIfDeclared": True,
    "researchComplete.coreFields": ["synopsis", "genre", "lane", "audience", "storyCore", "storySkin", "conflict", "payoff", "localizationLevel", "localizationJudgment", "mismatch"],
    "researchComplete.optionalFields": ["openingSummary", "openingType", "payEpisode", "paywallSummary", "paywallType"],
    "researchComplete.placeholderTextCountsAsReliableContent": False,
    "researchComplete.evidenceMustSupportResolvedIdentity": True,
    "researchComplete.insufficientEvidenceStatus": "NEEDS_GPT",
    "researchComplete.identityOrSourceConflictStatus": "REVIEW_REQUIRED",
    "writeback.samePlatformRecordRequired": True,
    "writeback.canonicalIdentityMatchRequired": True,
    "writeback.completeRequired": True,
    "writeback.finalDeterministicValidationRequired": True,
    "writeback.crossPlatformFallbackForbidden": True,
    "writeback.authoritativeRunRecheckAtWriteRequired": True,
    "writeback.idempotentConditionalWriteRequired": True,
    "authoritativeRun.scope": ["collection_date", "platform", "source_type", "target_key"],
    "authoritativeRun.latestCompleteStructuralRunWins": True,
    "authoritativeRun.incompleteOrFailedRunCannotOverride": True,
    "authoritativeRun.supersededTitleCannotExecuteOldResearchTask": True,
    "batchValidity.topN": 10,
    "batchValidity.batchCompleteRequired": True,
    "batchValidity.rowCountEqualsTopN": True,
    "batchValidity.rankSetExactlyOneToTopN": True,
    "batchValidity.duplicateRankForbidden": True,
    "batchValidity.duplicateTitleForbidden": True,
    "batchValidity.identityMustResolve": True,
    "batchValidity.sourceAndRankingSemanticsMustMatchEvidence": True,
    "batchValidity.partialFailsClosed": True,
    "researchBudget.mode": "FREE_ONLY",
    "researchBudget.paidBudgetUsd": 0,
    "researchBudget.automaticPaidFallback": False,
    "researchBudget.maxQueuedResearchTasks": None,
    "researchBudget.defaultConcurrency": 2,
    "researchBudget.throttleConcurrencyFloor": 1,
    "researchBudget.technicalRetriesAfterInitialAttempt": 2,
    "researchBudget.wholeTaskMechanicalRetryForbiddenAfterProviderSpend": True,
    "researchBudget.maxExternalSearchRoundsPerDrama": 3,
    "researchBudget.taskTimeoutSeconds": 300,
    "researchBudget.quotaExhaustedStatus": "DEFERRED_FREE_QUOTA",
    "researchBudget.quotaExhaustionIsFailure": False,
    "researchBudget.providerMustBeVerifiedFreeAtRuntime": True,
    "researchBudget.retryAuthority": "PER_EXTERNAL_API_CALL",
    "researchBudget.maxAttemptsPerExternalApiCall": 3,
    "researchBudget.nestedRetriesForbidden": True,
    "researchBudget.businessErrorMechanicalRetryForbidden": True,
    "researchBudget.backoffRequired": True,
    "researchBudget.quotaResumeFromCheckpoint": True,
    "researchBudget.quotaWaitConsumesActiveTimeout": False,
    "researchBudget.resumeResetsConsumedActiveTimeout": False,
    "researchBudget.providerVerificationUnknownFailsClosed": True,
    "researchBudget.stages": ["SEARCH", "ANALYZE", "VALIDATE", "WRITEBACK"],
    "stabilityAndPromotion.preProductionFormalWindows": 3,
    "stabilityAndPromotion.postProductionObservationDays": 7,
    "stabilityAndPromotion.stabilityObservationBlocksDailyProcessing": False,
    "stabilityAndPromotion.ciGreenAloneIsProductionReady": False,
    "stabilityAndPromotion.nonPassGateBlocksPromotion": True,
    "stabilityAndPromotion.exactHeadEvidenceRequired": True,
    "stateSemantics.collectionAnalysisResearchStatesRemainIndependent": True,
    "stateSemantics.successPropagationForbidden": True,
    "stateSemantics.newResearchDeferredState": "DEFERRED_FREE_QUOTA",
    "stateSemantics.terminalStateReentryRequiresExplicitDecision": True,
    "stateSemantics.collectionSuccessDoesNotImplyResearchComplete": True,
    "stateSemantics.researchCompleteDoesNotImplyPublished": True,
    "productionSafety.wholeLegacyBranchMergeForbidden": True,
}
PRODUCTION_FLAGS = {
    "productionWriteAuthorized", "productionDbMigrationAuthorized",
    "renderProductionChangeAuthorized", "schedulerProductionChangeAuthorized",
    "researchQueueRestartAuthorized", "mainWriteAuthorized",
    "productionRlsChangeAuthorized", "secretsChangeAuthorized",
}
AUTHORIZATION_FLAGS = {
    "mainWriteAuthorized", "productionWriteAuthorized", "productionDbMigrationAuthorized",
    "productionRlsChangeAuthorized", "productionRenderChangeAuthorized",
    "productionSchedulerChangeAuthorized", "productionResearchQueueChangeAuthorized",
    "secretsChangeAuthorized",
}
TRANSITIONS = {
    "PENDING": {"RESEARCHING": "ELIGIBILITY_AND_FREE_PROVIDER_VERIFIED", "DEFERRED_FREE_QUOTA": "FREE_QUOTA_UNAVAILABLE", "REVIEW_REQUIRED": "IDENTITY_SOURCE_OR_AUTHORITY_CONFLICT"},
    "RESEARCHING": {"COMPLETE": "ALL_COMPLETE_GATES_PASS", "DEFERRED_FREE_QUOTA": "FREE_QUOTA_UNAVAILABLE", "NEEDS_GPT": "INSUFFICIENT_EVIDENCE", "REVIEW_REQUIRED": "IDENTITY_SOURCE_SCHEMA_OR_AUTHORITY_CONFLICT", "FAILED": "TECHNICAL_ATTEMPTS_OR_ACTIVE_TIMEOUT_EXHAUSTED"},
    "DEFERRED_FREE_QUOTA": {"RESEARCHING": "QUOTA_RESTORED_AND_ELIGIBILITY_REVALIDATED_AT_CHECKPOINT", "REVIEW_REQUIRED": "IDENTITY_SOURCE_OR_AUTHORITY_CONFLICT"},
    "COMPLETE": {}, "NEEDS_GPT": {}, "REVIEW_REQUIRED": {}, "FAILED": {},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def at_path(data, path):
    for key in path.split("."):
        data = data[key]
    return data


def strict_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(strict_equal(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(strict_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def validate_contract(contract):
    for path, expected in POLICY.items():
        require(strict_equal(at_path(contract, path), expected), path)
    require(set(contract["sourceSemantics"]["pausedPlatforms"]) == {"DramaWave", "ShortMax"}, "paused scope")
    require(set(contract["productionSafety"]) == PRODUCTION_FLAGS | {"wholeLegacyBranchMergeForbidden"}, "production flags missing")
    for key in PRODUCTION_FLAGS:
        require(contract["productionSafety"][key] is False, key)
    gates = contract["stabilityAndPromotion"]["requiredGates"]
    require(set(gates) == GATES and len(gates) == len(GATES), "required gates")
    require(strict_equal(contract["stateSemantics"]["researchTransitions"], TRANSITIONS), "research state transitions")
    require("maxProviderAttemptsPerStage" not in contract["researchBudget"], "ambiguous stage retry cap")


def validate_manifest(manifest):
    require(type(manifest["manifestVersion"]) is int and manifest["manifestVersion"] == 2, "manifest version")
    require(manifest["stage"] == "STEP_A_CONTRACT_SCAFFOLD", "stage")
    require(manifest["base"] == {"branch": "main", "sha": BASE_SHA}, "base")
    require(manifest["integration"]["branch"] == "integration/core-contract-v2-2026-10-08", "unique branch")
    require(manifest["integration"]["wholeBranchPromotionAllowed"] is False, "whole branch promotion")
    require(set(manifest["authorization"]) == AUTHORIZATION_FLAGS, "authorization flags missing")
    require(all(value is False for value in manifest["authorization"].values()), "production authorization")
    step = manifest["stepA"]
    require(set(step) == {"allowedPaths", "runtimeCodeChangeAllowed", "schemaChangeAllowed", "schedulerChangeAllowed", "deploymentChangeAllowed"}, "step A flags")
    require(set(step["allowedPaths"]) == ALLOWED_PATHS and len(step["allowedPaths"]) == 4, "allowed paths")
    require(all(value is False for key, value in step.items() if key != "allowedPaths"), "runtime writes")
    gate = manifest["promotionGate"]
    require(set(gate["required"]) == GATES and len(gate["required"]) == len(GATES), "required gates")
    require(gate["currentStatus"] == "BLOCK_PROMOTION", "promotion status")
    require(gate["results"] == {name: {"status": "NOT_RUN", "evidence": None} for name in GATES}, "unproven runtime gates")
    replays = manifest["incidentReplays"]
    require({r["id"] for r in replays} == REPLAY_IDS and len(replays) == len(REPLAY_IDS), "incident coverage")
    require(all(r["status"] == "NOT_RUN" and r["evidence"] is None and r["requiredOutcome"] for r in replays), "replay evidence claims")
    binding = manifest["evidenceBinding"]
    require(binding["scope"] == "EXACT_CANDIDATE_HEAD", "evidence head")
    require(set(binding["requiredFields"]) == {"commitSha", "environment", "executedAt", "dataScope", "artifactSha256", "result"}, "evidence fields")
    require(binding["stepATestPassIsRuntimeEvidence"] is False, "false runtime evidence")
    require(manifest["legacy115Disposition"] == "FROZEN_REQUIRES_SEPARATE_APPROVAL", "legacy queue")
    require(manifest["baselineConflict"]["resolution"] == "V2_CANDIDATE_ONLY_CURRENT_REMAINS_PRODUCTION_AUTHORITY", "baseline conflict")
    require(manifest["baselineConflict"]["activationRequiresIntegrationGateAndSeparateApproval"] is True, "activation")
    require(manifest["baselineReferences"] == [
        {"name": "JSM_系统审计与集成基线_Current", "updatedAt": "2026-09-20", "sha256": "a99e61f4ccfac7448899173ab97f260f7f8a8472730d78aa5d325136c5b8b93a"},
        {"name": "JSM_核心业务契约审计_Current", "updatedAt": "2026-09-20", "sha256": "07e68bb1ef66b5babdee0e23cf3d0e650dd06236f19f407a38f866c25360087c"},
    ], "baseline provenance")
    require(set(manifest["legacyEvidence"]) == {"integration/core-contract-v1-2026-09-20", "pilot/audit/shadow branches"}, "legacy evidence coverage")
    require(all(v["wholeBranchMergeAllowed"] is False and v["role"] == "EVIDENCE_AND_CANDIDATE_PATCH_SOURCE_ONLY" for v in manifest["legacyEvidence"].values()), "legacy merge")
    require(manifest["stagingEvidence"] == {"status": "NOT_RUN", "exactHeadDeployment": None, "formalWindows": [], "requiredConsecutiveWindows": 3, "windowsTimezone": "Asia/Shanghai"}, "staging evidence")
    require(manifest["rollback"] == {"status": "NOT_RUN", "applicationSnapshot": None, "databaseSnapshot": None, "schedulerSnapshot": None, "restoreEvidence": None, "stepARollback": "ABANDON_CONTRACT_CANDIDATE_NO_RUNTIME_OR_DATA_ROLLBACK_NEEDED"}, "rollback evidence")
    units = manifest["futurePromotionUnits"]
    require({u["name"] for u in units} == {"identity-runtime", "source-newness-runtime", "research-runtime", "collector-runtime", "persistence-migration", "scheduler-runtime", "api-frontend-runtime"} and len(units) == 7, "future units missing")
    require(all(unit["status"] == "NOT_STARTED" for unit in units), "future implementation")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8", stderr=subprocess.PIPE).strip()


def validate_changed_surface(root, base):
    # Compare the actual working tree to the base, including committed/staged
    # edits; no self-reported manifest can hide an extra runtime change.
    raw = git(root, "diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, "--")
    parts = raw.split("\0") if raw else []
    if parts and parts[-1] == "":
        parts.pop()
    require(len(parts) % 2 == 0, "malformed git diff")
    changes = dict(zip(parts[1::2], parts[0::2]))
    for path in filter(None, git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")):
        changes[path] = "A"
    require(set(changes) == ALLOWED_PATHS, "actual changed paths: " + repr(changes))
    require(all(status == "A" for status in changes.values()), "Step A must only add contract files")


class AdversarialStepATests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / "core_contract_v2.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((ROOT / "promotion_manifest_v2.json").read_text(encoding="utf-8"))

    def test_contract_and_manifest_full_policy(self):
        validate_contract(self.contract)
        validate_manifest(self.manifest)
        self.assertEqual(self.contract["baseMainSha"], self.manifest["base"]["sha"])

    def test_every_policy_mutation_and_missing_field_is_rejected(self):
        for path, expected in POLICY.items():
            for mode in ("mutate", "remove"):
                with self.subTest(path=path, mode=mode):
                    changed = copy.deepcopy(self.contract)
                    keys = path.split(".")
                    parent = changed
                    for key in keys[:-1]:
                        parent = parent[key]
                    if mode == "remove":
                        del parent[keys[-1]]
                    else:
                        parent[keys[-1]] = not expected if type(expected) is bool else {"invalid": "policy"}
                    with self.assertRaises((ValueError, KeyError, TypeError)):
                        validate_contract(changed)

    def test_boolean_integer_coercion_cannot_enable_production(self):
        for key in PRODUCTION_FLAGS:
            for replacement in (True, 0, None, "false"):
                with self.subTest(key=key, replacement=replacement):
                    changed = copy.deepcopy(self.contract)
                    changed["productionSafety"][key] = replacement
                    with self.assertRaises(ValueError):
                        validate_contract(changed)

    def test_manifest_authorization_cannot_be_enabled_or_omitted(self):
        for key in AUTHORIZATION_FLAGS:
            for mode in ("enable", "remove"):
                with self.subTest(key=key, mode=mode):
                    changed = copy.deepcopy(self.manifest)
                    if mode == "enable":
                        changed["authorization"][key] = True
                    else:
                        del changed["authorization"][key]
                    with self.assertRaises(ValueError):
                        validate_manifest(changed)

    def test_gate_false_pass_and_empty_gate_sets_rejected(self):
        for status in ("PASS", "FAIL", "BLOCKED", "UNKNOWN"):
            for gate in GATES:
                with self.subTest(gate=gate, status=status):
                    changed = copy.deepcopy(self.manifest)
                    changed["promotionGate"]["results"][gate]["status"] = status
                    with self.assertRaises(ValueError):
                        validate_manifest(changed)
        for key in ("required", "results"):
            changed = copy.deepcopy(self.manifest)
            changed["promotionGate"][key] = [] if key == "required" else {}
            with self.assertRaises(ValueError):
                validate_manifest(changed)

    def test_quota_cannot_be_failed_and_unvalidated_complete_is_forbidden(self):
        for start, end in (("DEFERRED_FREE_QUOTA", "FAILED"), ("PENDING", "COMPLETE"), ("COMPLETE", "RESEARCHING")):
            with self.subTest(start=start, end=end):
                changed = copy.deepcopy(self.contract)
                changed["stateSemantics"]["researchTransitions"][start][end] = "UNCONDITIONAL"
                with self.assertRaises(ValueError):
                    validate_contract(changed)

    def test_replays_cannot_claim_pass_or_disappear(self):
        for replay in self.manifest["incidentReplays"]:
            with self.subTest(replay=replay["id"]):
                changed = copy.deepcopy(self.manifest)
                changed["incidentReplays"] = [r for r in changed["incidentReplays"] if r["id"] != replay["id"]]
                with self.assertRaises(ValueError):
                    validate_manifest(changed)
                changed = copy.deepcopy(self.manifest)
                next(r for r in changed["incidentReplays"] if r["id"] == replay["id"])["status"] = "PASS"
                with self.assertRaises(ValueError):
                    validate_manifest(changed)

    def test_missing_manifest_evidence_sections_fail_closed(self):
        for field in ("baselineReferences", "legacyEvidence", "futurePromotionUnits", "incidentReplays"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.manifest)
                changed[field] = {} if field == "legacyEvidence" else []
                with self.assertRaises(ValueError):
                    validate_manifest(changed)

    def test_real_git_surface_and_base_ancestry(self):
        self.assertEqual(git(ROOT, "rev-parse", "origin/main"), BASE_SHA)
        self.assertEqual(git(ROOT, "merge-base", "HEAD", BASE_SHA), BASE_SHA)
        self.assertEqual(git(ROOT, "branch", "--show-current"), self.manifest["integration"]["branch"])
        self.assertEqual(git(ROOT, "rev-list", "--merges", BASE_SHA + "..HEAD"), "")
        validate_changed_surface(ROOT, BASE_SHA)

    def test_surface_guard_rejects_untracked_staged_and_committed_runtime(self):
        with tempfile.TemporaryDirectory(prefix="jsm-step-a-") as temp:
            root = Path(temp)
            git(root, "init", "--quiet")
            # Temp repository only. Disable hooks even if a global template
            # installs hooks; commits cannot invoke user hooks or signing.
            git(root, "config", "core.hooksPath", str(root / "no-hooks"))
            git(root, "config", "commit.gpgsign", "false")
            git(root, "config", "user.name", "Step A isolated fixture")
            git(root, "config", "user.email", "step-a@example.invalid")
            (root / "app.py").write_text("# unchanged baseline\n", encoding="utf-8")
            git(root, "add", "app.py")
            git(root, "commit", "--quiet", "-m", "fixture baseline")
            base = git(root, "rev-parse", "HEAD")
            for name in ALLOWED_PATHS:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("contract fixture\n", encoding="utf-8")
            validate_changed_surface(root, base)
            git(root, "add", "--", *sorted(ALLOWED_PATHS))
            validate_changed_surface(root, base)
            git(root, "commit", "--quiet", "-m", "contract fixture")
            validate_changed_surface(root, base)
            extra = root / "research_runtime.py"
            extra.write_text("# forbidden runtime addition\n", encoding="utf-8")
            for stage in ("untracked", "staged", "committed"):
                with self.subTest(stage=stage):
                    if stage == "staged":
                        git(root, "add", "research_runtime.py")
                    elif stage == "committed":
                        git(root, "commit", "--quiet", "-m", "forbidden fixture")
                    with self.assertRaises(ValueError):
                        validate_changed_surface(root, base)
            # A separate check of modifications to an existing runtime path.
            # Remove the *generated fixture* addition from the final tree so it
            # cannot mask a broken detector for modifications to existing files.
            extra.unlink()
            git(root, "add", "-u")
            git(root, "commit", "--quiet", "-m", "restore fixture surface")
            validate_changed_surface(root, base)
            (root / "app.py").write_text("# forbidden runtime edit\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_changed_surface(root, base)


class CoreContractV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads((ROOT / "core_contract_v2.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads((ROOT / "promotion_manifest_v2.json").read_text(encoding="utf-8"))

    def test_contract_is_locked_but_not_implemented(self):
        self.assertEqual(self.contract["contractVersion"], "2.0")
        self.assertEqual(self.contract["status"], "LOCKED_NOT_IMPLEMENTED")

    def test_verified_source_first_seen_is_global_research_trigger(self):
        source = self.contract["sourceSemantics"]
        identity = self.contract["identityAndNewness"]
        self.assertTrue(source["verifiedSourceFirstSeenTriggersResearch"])
        self.assertEqual(identity["firstSeenTrigger"], "first_seen_any_verified_source")
        self.assertEqual(identity["researchSubjectScope"], "GLOBAL_CANONICAL_DRAMA")
        self.assertTrue(identity["dedupeAcrossPlatformsAndSources"])

    def test_official_web_scope_and_paused_platforms_are_explicit(self):
        source = self.contract["sourceSemantics"]
        self.assertEqual(
            set(source["activeOfficialWebPlatforms"]),
            {"DramaBox", "FlexTV", "GoodShort", "MoboReels", "NetShort", "ReelShort"},
        )
        self.assertEqual(set(source["pausedPlatforms"]), {"DramaWave", "ShortMax"})
        self.assertEqual(source["collectionProfile"]["language"], "English")
        self.assertEqual(source["collectionProfile"]["locale"], "en-US")
        self.assertEqual(source["collectionProfile"]["region"], "US")

    def test_platform_failure_is_isolated_without_stale_copy(self):
        isolation = self.contract["platformFailureIsolation"]
        self.assertTrue(isolation["independentPlatformCommit"])
        self.assertTrue(isolation["successfulPlatformsProceedImmediately"])
        self.assertFalse(isolation["copyPriorDayIntoCurrentDate"])
        self.assertTrue(isolation["crossPlatformCompositeMustExposeCoverage"])

    def test_research_complete_and_writeback_fail_closed(self):
        complete = self.contract["researchComplete"]
        writeback = self.contract["writeback"]
        self.assertTrue(complete["deterministicSchemaValidationRequired"])
        self.assertTrue(complete["coreFieldsRequired"])
        self.assertTrue(complete["evidenceUrlRequired"])
        self.assertEqual(complete["minimumConfidence"], "medium")
        self.assertTrue(writeback["samePlatformRecordRequired"])
        self.assertTrue(writeback["crossPlatformFallbackForbidden"])

    def test_zero_paid_cost_budget_is_hard_contract(self):
        budget = self.contract["researchBudget"]
        self.assertEqual(budget["mode"], "FREE_ONLY")
        self.assertEqual(budget["paidBudgetUsd"], 0)
        self.assertFalse(budget["automaticPaidFallback"])
        self.assertIsNone(budget["maxQueuedResearchTasks"])
        self.assertEqual(budget["defaultConcurrency"], 2)
        self.assertEqual(budget["technicalRetriesAfterInitialAttempt"], 2)
        self.assertEqual(budget["maxAttemptsPerExternalApiCall"], 3)
        self.assertTrue(budget["wholeTaskMechanicalRetryForbiddenAfterProviderSpend"])
        self.assertEqual(budget["maxExternalSearchRoundsPerDrama"], 3)
        self.assertEqual(budget["taskTimeoutSeconds"], 300)
        self.assertEqual(budget["quotaExhaustedStatus"], "DEFERRED_FREE_QUOTA")
        self.assertFalse(budget["quotaExhaustionIsFailure"])

    def test_stability_observation_does_not_delay_daily_pipeline(self):
        stability = self.contract["stabilityAndPromotion"]
        self.assertEqual(stability["preProductionFormalWindows"], 3)
        self.assertEqual(stability["postProductionObservationDays"], 7)
        self.assertFalse(stability["stabilityObservationBlocksDailyProcessing"])
        self.assertFalse(stability["ciGreenAloneIsProductionReady"])

    def test_step_a_manifest_is_non_production_and_contract_only(self):
        manifest = self.manifest
        self.assertEqual(manifest["base"]["branch"], "main")
        self.assertEqual(
            manifest["base"]["sha"],
            "a206bc9c21138a9559aecf5a7a34069167268cfc",
        )
        self.assertFalse(manifest["integration"]["wholeBranchPromotionAllowed"])
        self.assertEqual(manifest["promotionGate"]["currentStatus"], "BLOCK_PROMOTION")
        self.assertTrue(all(value is False for value in manifest["authorization"].values()))
        self.assertFalse(manifest["stepA"]["runtimeCodeChangeAllowed"])
        self.assertFalse(manifest["stepA"]["schemaChangeAllowed"])
        self.assertFalse(manifest["stepA"]["schedulerChangeAllowed"])
        self.assertFalse(manifest["stepA"]["deploymentChangeAllowed"])

    def test_step_a_changed_surface_is_exact_and_non_runtime(self):
        expected = {
            "core_contract_v2.json",
            "docs/CORE_CONTRACT_V2.md",
            "promotion_manifest_v2.json",
            "test_core_contract_v2.py",
        }
        self.assertEqual(set(self.manifest["stepA"]["allowedPaths"]), expected)
        forbidden_prefixes = (
            "supabase/",
            "windows/",
            ".github/workflows/",
        )
        for path in expected:
            self.assertFalse(path.startswith(forbidden_prefixes))
            self.assertNotIn(path, {"app.py", "persistence.py", "research_worker.py", "render.yaml"})

    def test_legacy_branches_are_evidence_only(self):
        legacy = self.manifest["legacyEvidence"]
        self.assertFalse(legacy["integration/core-contract-v1-2026-09-20"]["wholeBranchMergeAllowed"])
        self.assertEqual(
            legacy["integration/core-contract-v1-2026-09-20"]["role"],
            "EVIDENCE_AND_CANDIDATE_PATCH_SOURCE_ONLY",
        )


if __name__ == "__main__":
    # Python tests cannot accidentally reach providers or production services.
    # Git subprocess commands above are exclusively local, never fetch/push.
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden in Step A tests")), patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden in Step A tests")):
        unittest.main()
