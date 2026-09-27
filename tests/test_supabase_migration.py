from pathlib import Path

MIGRATION = Path(__file__).parents[1] / "supabase/migrations/202609270001_revmind_core.sql"
IDENTITY_MIGRATION = (
    Path(__file__).parents[1] / "supabase/migrations/202609270002_revmind_os_identity.sql"
)


def test_hosted_migration_is_tenant_scoped_and_does_not_grant_browser_writes():
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "profiles",
        "plans",
        "subscriptions",
        "promo_codes",
        "promo_redemptions",
        "research_runs",
        "audit_events",
    ):
        assert f"alter table public.{table} enable row level security" in sql
    assert "references auth.users(id)" in sql
    assert "references public.profiles(id)" in sql
    assert "create policy \"research self read\"" in sql
    assert "create policy \"subscriptions self read\"" in sql
    assert "do not grant browser clients direct write access" in sql.lower()
    assert "service_role" not in sql.lower()


def test_auth_user_trigger_creates_free_entitlement_without_secrets():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "create trigger on_auth_user_created" in sql
    assert "insert into public.subscriptions" in sql
    assert "FREE" in sql
    assert "alpaca" not in sql.lower()
    assert "myfxbook" not in sql.lower()


def test_followup_aligns_identity_to_central_revmind_os() -> None:
    sql = IDENTITY_MIGRATION.read_text(encoding="utf-8").lower()
    assert "drop constraint if exists profiles_id_fkey" in sql
    assert "drop trigger if exists on_auth_user_created" in sql
    assert "external user uuid" in sql
    assert "add column organization_id uuid" in sql
    assert "create table public.organization_members" in sql


def test_followup_denies_direct_browser_table_access() -> None:
    sql = IDENTITY_MIGRATION.read_text(encoding="utf-8").lower()
    for table in (
        "profiles",
        "plans",
        "subscriptions",
        "promo_codes",
        "promo_redemptions",
        "research_runs",
        "audit_events",
        "organizations",
        "organization_members",
    ):
        assert f"revoke all on table public.{table} from anon, authenticated" in sql
    assert "service_key" not in sql
