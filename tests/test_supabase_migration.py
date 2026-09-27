from pathlib import Path

MIGRATION = Path(__file__).parents[1] / "supabase/migrations/202609270001_revmind_core.sql"


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
