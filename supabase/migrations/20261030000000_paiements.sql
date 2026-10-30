-- KLDataViz : abonnements prépayés par CinetPay.
-- paid_until nul = forfait sans échéance (attribué à la main dans l'administration).

alter table app.organizations add column if not exists paid_until timestamptz;
alter table app.organizations add column if not exists rappel_envoye date;

create table if not exists app.payments (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null references app.organizations(id) on delete cascade,
  user_id         uuid references auth.users(id) on delete set null,
  email           text not null,
  plan            text not null,                -- pas de clé étrangère : l'historique survit à un forfait supprimé
  months          integer not null check (months between 1 and 12),
  amount          integer not null check (amount > 0),
  currency        text not null default 'XOF',
  provider        text not null default 'cinetpay',
  mode            text not null default 'test' check (mode in ('test', 'live')),
  merchant_tx     text not null unique,
  transaction_id  text,
  notify_token    text,
  status          text not null default 'pending' check (status in ('pending', 'paid', 'failed', 'a_verifier')),
  created_at      timestamptz not null default now(),
  paid_at         timestamptz
);

create index if not exists payments_org_idx on app.payments (org_id, created_at desc);
create index if not exists organizations_paid_until_idx on app.organizations (paid_until) where paid_until is not null;

alter table app.payments enable row level security;
