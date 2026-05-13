-- =============================================================
-- AGRONUMEROS — Schema Ganadería
-- Ejecutar en Supabase → SQL Editor
-- =============================================================

-- — 1. LINIERS DIARIO ——————————————————————————————————————
create table if not exists liniers_diario (
  id                  bigserial primary key,
  fecha               date not null,
  categoria           text not null,        -- novillo, novillito, vaca, vaquillona, toro, mej
  subcategoria        text not null,        -- novillo_especial, vaca_conserva, etc.
  subcategoria_nombre text,
  precio              numeric(10,2),        -- ARS/kg vivo
  cabezas             int,
  kg_prom             numeric(8,2),
  unique (fecha, subcategoria)
);
create index if not exists idx_liniers_fecha on liniers_diario(fecha desc);
create index if not exists idx_liniers_cat   on liniers_diario(categoria, fecha desc);

-- — 2. PRECIOS GANADERÍA SEMANAL (pre-agregado para gráficos) ——
create table if not exists precios_ganaderia_semanal (
  id              bigserial primary key,
  semana          date not null,            -- lunes de la semana (ISO date_trunc)
  categoria       text not null,
  precio_promedio numeric(10,2),
  cabezas_total   int,
  fuente          text default 'liniers',
  unique (semana, categoria, fuente)
);

-- — 3. ÍNDICES GANADEROS (INMAG, etc.) ——————————————————————
create table if not exists indices_ganaderos (
  id      bigserial primary key,
  fecha   date not null,
  nombre  text not null,                    -- 'INMAG', 'IPGV', etc.
  valor   numeric(12,4),
  unique (fecha, nombre)
);

-- — 4. ROSGAN — PRECIOS CRÍA/INVERNADA ——————————————————————
create table if not exists rosgan_precios (
  id          bigserial primary key,
  fecha       date not null,
  tipo        text not null,                -- 'cria' | 'invernada'
  categoria   text not null,               -- 'vaca_cria', 'ternero_liviano', 'novillo_250', etc.
  precio      numeric(10,2),               -- USD/kg
  unique (fecha, tipo, categoria)
);

-- — 5. EXISTENCIAS BOVINAS (censo SENASA) ———————————————————
create table if not exists ganaderia_existencias_bovinas (
  id              bigserial primary key,
  anio            int not null,
  provincia       text not null,
  departamento    text not null default '',
  vacas           bigint,
  vaquillonas     bigint,
  novillos        bigint,
  novillitos      bigint,
  terneros        bigint,
  terneras        bigint,
  toros           bigint,
  toritos         bigint,
  bueyes          bigint,
  unique (anio, provincia, departamento)
);

-- — 6. CONSIGNATARIAS ————————————————————————————————————
create table if not exists consignatarias (
  id            serial primary key,
  nombre        text not null,
  nombre_corto  text,
  url_remates   text,
  activa        boolean default true
);

-- — 7. REMATES ———————————————————————————————————————————
create table if not exists remates (
  id                bigserial primary key,
  consignataria_id  int references consignatarias(id),
  nombre            text not null,
  fecha             date,
  hora              text,
  lugar             text,
  localidad         text,
  provincia         text,
  region            text,
  tipo_principal    text,
  tipo_venta        text,
  modalidad         text,
  canal_tv          text,
  url_streaming     text,
  razas             text,
  total_cabezas     int,
  categorias        text,
  descripcion       text,
  url_fuente        text,
  url_flyer         text,
  flyer_tipo        text,
  activo            boolean default true
);
create index if not exists idx_remates_fecha on remates(fecha);

-- — 8. VISTAS ————————————————————————————————————————————

-- Vista resumen Liniers: última semana vs semana anterior por subcategoría
create or replace view v_precios_liniers_resumen as
with
  max_f as (
    select max(fecha) as f from liniers_diario
  ),
  semanas as (
    select
      date_trunc('week', f)::date                       as sem_ini,
      (date_trunc('week', f) - interval '7 days')::date as sem_ant_ini
    from max_f
  )
select
  l.categoria,
  l.subcategoria,
  l.subcategoria_nombre,
  max(case when l.fecha = m.f then l.fecha  end) as ultima_fecha,
  -- Último día
  avg(case when l.fecha = m.f then l.precio  end)::numeric(10,2)  as precio_ult_dia,
  sum(case when l.fecha = m.f then l.cabezas end)                 as cabezas_ult_dia,
  avg(case when l.fecha = m.f then l.kg_prom end)::numeric(8,2)   as kg_prom_ult_dia,
  -- Semana actual (desde lunes hasta hoy)
  avg(case when l.fecha >= s.sem_ini                              then l.precio  end)::numeric(10,2)  as precio_sem_act,
  sum(case when l.fecha >= s.sem_ini                              then l.cabezas end)                 as cabezas_sem_act,
  avg(case when l.fecha >= s.sem_ini                              then l.kg_prom end)::numeric(8,2)   as kg_prom_sem_act,
  -- Semana anterior
  avg(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini  then l.precio  end)::numeric(10,2)  as precio_sem_ant,
  sum(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini  then l.cabezas end)                 as cabezas_sem_ant,
  avg(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini  then l.kg_prom end)::numeric(8,2)   as kg_prom_sem_ant,
  -- Variación porcentual precio
  case when avg(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini then l.precio end) > 0
    then round(
      ( avg(case when l.fecha >= s.sem_ini then l.precio end)
      - avg(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini then l.precio end)
      ) / avg(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini then l.precio end) * 100
    , 1)
  end as var_precio_pct,
  -- Variación porcentual cabezas
  case when sum(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini then l.cabezas end) > 0
    then round(
      ( sum(case when l.fecha >= s.sem_ini then l.cabezas end)::numeric
      - sum(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini then l.cabezas end)
      ) / sum(case when l.fecha >= s.sem_ant_ini and l.fecha < s.sem_ini then l.cabezas end) * 100
    , 1)
  end as var_cabezas_pct
from liniers_diario l
cross join max_f m
cross join semanas s
where l.fecha >= s.sem_ant_ini
group by l.categoria, l.subcategoria, l.subcategoria_nombre;

-- Vista simple Rosgan (el frontend la consulta directamente)
create or replace view v_rosgan_resumen as
select fecha, tipo, categoria, precio
from   rosgan_precios
order  by fecha, tipo, categoria;

-- — 9. RLS ———————————————————————————————————————————————
alter table liniers_diario                enable row level security;
alter table precios_ganaderia_semanal     enable row level security;
alter table indices_ganaderos             enable row level security;
alter table rosgan_precios                enable row level security;
alter table ganaderia_existencias_bovinas enable row level security;
alter table consignatarias                enable row level security;
alter table remates                       enable row level security;

create policy "anon read liniers_diario"                 on liniers_diario                for select using (true);
create policy "anon read precios_ganaderia_semanal"      on precios_ganaderia_semanal     for select using (true);
create policy "anon read indices_ganaderos"              on indices_ganaderos             for select using (true);
create policy "anon read rosgan_precios"                 on rosgan_precios                for select using (true);
create policy "anon read ganaderia_existencias_bovinas"  on ganaderia_existencias_bovinas for select using (true);
create policy "anon read consignatarias"                 on consignatarias                for select using (true);
create policy "anon read remates"                        on remates                       for select using (true);
