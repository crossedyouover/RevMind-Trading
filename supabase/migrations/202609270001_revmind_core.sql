-- RevMind hosted migration foundation.
-- Run only in the user's own Supabase project. Broker secrets never belong in these tables.

create extension if not exists pgcrypto;

create type public.app_role as enum ('user', 'support_admin', 'master_admin');
create type public.subscription_status as enum ('trial', 'active', 'past_due', 'canceled');

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  display_name text,
  role public.app_role not null default 'user',
  account_status text not null default 'active' check (account_status in ('active','suspended')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.plans (
  code text primary key check (code ~ '^[A-Z0-9][A-Z0-9_-]{2,31}$'),
  name text not null,
  price_cents integer not null default 0 check (price_cents >= 0),
  billing_period text not null default 'NONE' check (billing_period in ('NONE','MONTH','YEAR')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into public.plans(code,name) values ('FREE','Free') on conflict (code) do nothing;

create table public.subscriptions (
  user_id uuid primary key references public.profiles(id) on delete cascade,
  plan_code text not null references public.plans(code),
  status public.subscription_status not null default 'trial',
  provider text,
  provider_customer_id text,
  provider_subscription_id text,
  starts_at timestamptz not null default now(),
  ends_at timestamptz,
  updated_at timestamptz not null default now()
);

create table public.promo_codes (
  code text primary key check (code ~ '^[A-Z0-9][A-Z0-9_-]{2,31}$'),
  percent_off integer not null default 0 check (percent_off between 0 and 100),
  bonus_days integer not null default 0 check (bonus_days between 0 and 3650),
  max_redemptions integer check (max_redemptions is null or max_redemptions > 0),
  redemption_count integer not null default 0 check (redemption_count >= 0),
  expires_at timestamptz,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table public.promo_redemptions (
  code text not null references public.promo_codes(code) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  redeemed_at timestamptz not null default now(),
  primary key (code, user_id)
);

create table public.research_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  source text not null,
  timeframe text not null,
  status text not null,
  observed_at timestamptz not null,
  result jsonb not null,
  created_at timestamptz not null default now()
);

create table public.audit_events (
  id bigint generated always as identity primary key,
  user_id uuid references public.profiles(id) on delete set null,
  event_type text not null,
  entity_type text,
  entity_id text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public
as $$
begin
  insert into public.profiles(id, email) values (new.id, coalesce(new.email, ''));
  insert into public.subscriptions(user_id, plan_code, status) values (new.id, 'FREE', 'trial');
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public
as $$ select exists (select 1 from public.profiles where id = auth.uid() and role in ('support_admin','master_admin') and account_status = 'active') $$;

alter table public.profiles enable row level security;
alter table public.plans enable row level security;
alter table public.subscriptions enable row level security;
alter table public.promo_codes enable row level security;
alter table public.promo_redemptions enable row level security;
alter table public.research_runs enable row level security;
alter table public.audit_events enable row level security;

create policy "profiles self read" on public.profiles for select to authenticated using (id = auth.uid() or public.is_admin());
create policy "profiles self update" on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());
create policy "plans public active read" on public.plans for select to authenticated using (active = true or public.is_admin());
create policy "subscriptions self read" on public.subscriptions for select to authenticated using (user_id = auth.uid() or public.is_admin());
create policy "promos never directly readable" on public.promo_codes for select to authenticated using (false);
create policy "promo redemptions self read" on public.promo_redemptions for select to authenticated using (user_id = auth.uid() or public.is_admin());
create policy "research self read" on public.research_runs for select to authenticated using (user_id = auth.uid() or public.is_admin());
create policy "audit self read" on public.audit_events for select to authenticated using (user_id = auth.uid() or public.is_admin());

-- Writes, billing updates, promo redemption, and role changes belong in authenticated
-- Edge Functions or the RevMind API. Do not grant browser clients direct write access.
