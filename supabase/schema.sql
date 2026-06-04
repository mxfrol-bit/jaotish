-- Matrix Engine — схема v0.1
-- Доступ только серверный (service_role), поэтому RLS включён без публичных политик.

create table if not exists me_profiles (
    id             bigserial primary key,
    profile_id     uuid unique not null,
    telegram_id    bigint,
    name           text,
    birth_date     date,
    analysis_type  text,
    method_version text,
    data           jsonb not null,
    created_at     timestamptz not null default now()
);

create index if not exists me_profiles_tg_idx  on me_profiles (telegram_id);
create index if not exists me_profiles_pid_idx on me_profiles (profile_id);

-- Сохранённые данные пользователя — чтобы не вводить имя/дату повторно.
create table if not exists me_users (
    telegram_id  bigint primary key,
    name         text,
    birth_date   date,
    gender       text,
    birth_time   text,              -- ЧЧ:ММ (для астро-модулей)
    birth_place  text,              -- город рождения (для геокодинга)
    lat          double precision,  -- предрассчитанные координаты
    lon          double precision,
    timezone     text,              -- IANA-таймзона места рождения
    updated_at   timestamptz not null default now()
);

-- Партнёры для синастрии (совместимость с конкретным человеком).
create table if not exists me_partners (
    id           bigserial primary key,
    telegram_id  bigint not null,
    partner_id   uuid unique not null,
    name         text,
    birth_date   date,
    birth_time   text,              -- ЧЧ:ММ (для астро)
    birth_place  text,
    lat          double precision,
    lon          double precision,
    timezone     text,
    created_at   timestamptz not null default now()
);

create index if not exists me_partners_tg_idx on me_partners (telegram_id);

create table if not exists me_feedback (
    id          bigserial primary key,
    profile_id  uuid references me_profiles (profile_id) on delete cascade,
    text        text,
    rating      int,
    created_at  timestamptz not null default now()
);

alter table me_profiles enable row level security;
alter table me_feedback enable row level security;
alter table me_users    enable row level security;
alter table me_partners enable row level security;
-- Политик нет: доступ только через service_role с бэкенда (он обходит RLS).
