-- Align the RevMind Trading data plane with identity issued by the separate RevMind OS project.
-- Browser clients receive no direct table access; the hosted API scopes every service-role read.

drop trigger if exists on_auth_user_created on auth.users;
drop function if exists public.handle_new_user();

alter table public.profiles drop constraint if exists profiles_id_fkey;

create table public.organizations (
  id uuid primary key,
  name text not null,
  account_status text not null default 'active'
    check (account_status in ('active','suspended')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.organization_members (
  organization_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  member_role text not null default 'member'
    check (member_role in ('member','organization_admin')),
  created_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);

alter table public.research_runs
  add column organization_id uuid references public.organizations(id) on delete cascade;

create index research_runs_tenant_created_idx
  on public.research_runs(user_id, organization_id, created_at desc, id);

alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;

drop policy if exists "profiles self read" on public.profiles;
drop policy if exists "profiles self update" on public.profiles;
drop policy if exists "plans public active read" on public.plans;
drop policy if exists "subscriptions self read" on public.subscriptions;
drop policy if exists "promos never directly readable" on public.promo_codes;
drop policy if exists "promo redemptions self read" on public.promo_redemptions;
drop policy if exists "research self read" on public.research_runs;
drop policy if exists "audit self read" on public.audit_events;
drop function if exists public.is_admin();

revoke all on table public.profiles from anon, authenticated;
revoke all on table public.plans from anon, authenticated;
revoke all on table public.subscriptions from anon, authenticated;
revoke all on table public.promo_codes from anon, authenticated;
revoke all on table public.promo_redemptions from anon, authenticated;
revoke all on table public.research_runs from anon, authenticated;
revoke all on table public.audit_events from anon, authenticated;
revoke all on table public.organizations from anon, authenticated;
revoke all on table public.organization_members from anon, authenticated;

comment on column public.profiles.id is
  'External user UUID issued and verified by the central RevMind OS identity project.';
