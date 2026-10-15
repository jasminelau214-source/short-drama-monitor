-- Isolated staging only. Keep Supabase's automatic RLS trigger enabled while
-- removing unnecessary direct API execution of its event-trigger helper.
BEGIN;
DO $guard$
BEGIN
  IF to_regclass('public.research_tasks') IS NOT NULL
     OR to_regclass('public.analysis_runs') IS NOT NULL THEN
    RAISE EXCEPTION 'JSM_V2_LEGACY_DATABASE_REFUSED';
  END IF;
  IF to_regclass('jsm_v2_runtime.meta') IS NULL THEN
    RAISE EXCEPTION 'JSM_V2_STAGING_MARKER_REQUIRED';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM jsm_v2_runtime.meta
    WHERE singleton AND scope = 'JSM_V2_ISOLATED_STAGING_V1') THEN
    RAISE EXCEPTION 'JSM_V2_STAGING_MARKER_REQUIRED';
  END IF;
  IF to_regprocedure('public.rls_auto_enable()') IS NOT NULL THEN
    IF (SELECT prorettype FROM pg_proc
        WHERE oid = to_regprocedure('public.rls_auto_enable()'))
        <> 'event_trigger'::regtype THEN
      RAISE EXCEPTION 'JSM_V2_UNEXPECTED_PLATFORM_HELPER';
    END IF;
    REVOKE EXECUTE ON FUNCTION public.rls_auto_enable()
      FROM PUBLIC, anon, authenticated, service_role;
  END IF;
END
$guard$;
COMMIT;
